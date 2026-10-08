"""Small local controls for the opt-in LAN pairing gate."""
from __future__ import annotations

import asyncio
import ipaddress
import json
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.responses import Response

from mrp.server.lan_access import (
    PAIR_BODY_MAX_INFLIGHT,
    SESSION_COOKIE,
    SESSION_NORMAL_ABSOLUTE_TTL_SECONDS,
    SESSION_NORMAL_IDLE_TTL_SECONDS,
    SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS,
    SESSION_REMEMBERED_IDLE_TTL_SECONDS,
    LanAccess,
    SessionPersistenceError,
)

router = APIRouter()


class PairRequest(BaseModel):
    access_code: str = Field(max_length=48)
    remember: bool = True


PAIR_BODY_LIMIT_BYTES = 2048
PAIR_BODY_READ_TIMEOUT_SECONDS = 5.0


def _local_computer(request: Request) -> bool:
    return _access(request).is_local_client(request.scope)


def _access(request: Request) -> LanAccess:
    return request.app.state.lan_access


async def _pair_payload(request: Request) -> tuple[str, bool]:
    length_values = request.headers.getlist("content-length")
    if len(length_values) > 1:
        raise HTTPException(400, "配对请求格式无效")
    if length_values:
        length_text = length_values[0]
        if not length_text.isascii() or not length_text.isdecimal():
            raise HTTPException(400, "配对请求格式无效")
        if int(length_text) > PAIR_BODY_LIMIT_BYTES:
            raise HTTPException(413, "配对请求过大")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + PAIR_BODY_READ_TIMEOUT_SECONDS
    body = bytearray()
    stream = request.stream().__aiter__()
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise HTTPException(408, "配对请求读取超时")
        try:
            chunk = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            raise HTTPException(408, "配对请求读取超时") from None
        body.extend(chunk)
        if len(body) > PAIR_BODY_LIMIT_BYTES:
            raise HTTPException(413, "配对请求过大")

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(400, "配对请求格式无效") from None
        try:
            parsed = PairRequest.model_validate(payload)
            return parsed.access_code, parsed.remember
        except Exception:
            raise HTTPException(422, "请填写访问码") from None
    if content_type not in {"application/x-www-form-urlencoded", ""}:
        raise HTTPException(415, "配对请求格式不支持")
    try:
        values = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True, max_num_fields=8)
    except ValueError:
        raise HTTPException(400, "配对请求格式无效") from None
    try:
        parsed = PairRequest(
            access_code=values.get("access_code", values.get("code", [""]))[0],
            remember=values.get("remember", ["true"])[0].lower() in {"1", "true", "yes", "on"},
        )
        return parsed.access_code, parsed.remember
    except Exception:
        raise HTTPException(422, "请填写访问码") from None


@router.get("/api/v1/lan/status")
def lan_status(request: Request):
    status = _access(request).status()
    status["can_manage_devices"] = _local_computer(request)
    response = JSONResponse(status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.get("/api/v1/lan/csrf")
def lan_csrf(request: Request):
    response = JSONResponse({"token": _access(request).csrf_token})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.get("/api/v1/lan/sessions")
def lan_sessions(request: Request):
    if not _local_computer(request):
        raise HTTPException(403, "已配对设备只能在电脑本机管理")
    response = JSONResponse({"sessions": _access(request).list_sessions()})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.delete("/api/v1/lan/sessions/{session_id}")
def revoke_lan_session(session_id: str, request: Request):
    if not _local_computer(request):
        raise HTTPException(403, "已配对设备只能在电脑本机管理")
    access = _access(request)
    if not access.revoke_session_id(session_id):
        raise HTTPException(404, "设备不存在或会话已经失效")
    response = JSONResponse({"ok": True, "session_state_cleanup_confirmed": access.last_cleanup_confirmed})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/api/v1/lan/sessions/revoke-all")
def revoke_all_lan_sessions(request: Request):
    if not _local_computer(request):
        raise HTTPException(403, "已配对设备只能在电脑本机管理")
    count = _access(request).revoke_all_sessions()
    response = JSONResponse({"ok": True, "revoked": count, "session_state_cleanup_confirmed": _access(request).last_cleanup_confirmed})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.post("/api/v1/lan/rotate-code")
def rotate_lan_code(request: Request):
    access = _access(request)
    if not access.enabled:
        raise HTTPException(409, "局域网访问尚未开启")
    if not _local_computer(request):
        raise HTTPException(403, "访问码只能在电脑本机轮换")
    code = access.rotate_access_code()
    response = JSONResponse({
        "access_code": code,
        "sessions_revoked": True,
        "session_state_cleanup_confirmed": access.last_cleanup_confirmed,
    })
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.post("/api/v1/lan/pair")
async def pair(request: Request):
    access = _access(request)
    if not access.enabled:
        raise HTTPException(403, "局域网访问尚未开启")
    if _local_computer(request):
        return {"ok": True, "already_local": True}
    if not access.reserve_pair_body_read():
        return JSONResponse(
            status_code=429,
            content={"detail": "配对请求较多，请稍后重试。"},
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )
    client_ip = request.client.host if request.client else "unknown"
    try:
        code, remember = await _pair_payload(request)
    except HTTPException as exc:
        headers = {"Cache-Control": "no-store"}
        if exc.status_code in {408, 413}:
            headers["Connection"] = "close"
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=headers,
        )
    finally:
        access.release_pair_body_read()
    try:
        token, retry_after = await asyncio.to_thread(
            access.check_code, client_ip, code, request.headers.get("user-agent", ""), remember
        )
    except SessionPersistenceError:
        raise HTTPException(503, "会话状态无法安全保存，请检查本机用户数据目录的可用空间和权限。") from None
    if retry_after:
        return JSONResponse(
            status_code=429,
            content={"detail": "尝试次数过多，请等待一段时间后再试。"},
            headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
        )
    if token is None:
        raise HTTPException(401, "访问码不正确，请检查电脑上的 LAN 启动窗口。")
    absolute_ttl = SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS if remember else SESSION_NORMAL_ABSOLUTE_TTL_SECONDS
    idle_ttl = SESSION_REMEMBERED_IDLE_TTL_SECONDS if remember else SESSION_NORMAL_IDLE_TTL_SECONDS
    response = JSONResponse({"ok": True, "expires_in": absolute_ttl, "idle_expires_in": idle_ttl, "remembered": remember})
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=absolute_ttl,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.post("/api/v1/lan/logout")
def logout(request: Request):
    cleanup_confirmed = _access(request).revoke_session(request.scope.get("headers", []))
    response = (
        Response(status_code=204)
        if cleanup_confirmed
        else JSONResponse(
            status_code=503,
            content={
                "detail": "本进程已退出当前会话，但磁盘状态清理未确认；若失效标记也无法写入，下次启动可能恢复旧摘要。请停止服务后手动删除 %LOCALAPPDATA%\\Sekai o Tsumugu Hime\\lan-sessions.json 和同名 .invalid 文件。",
                "session_state_cleanup_confirmed": False,
            },
        )
    )
    response.delete_cookie(key=SESSION_COOKIE, path="/", httponly=True, secure=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-MRP-LAN-Session-State-Cleanup"] = "confirmed" if cleanup_confirmed else "unconfirmed"
    return response


@router.get("/api/v1/lan/root-ca.crt")
def export_lan_root_ca(request: Request):
    """Export only the public root certificate through loopback management."""
    access = _access(request)
    server = request.scope.get("server")
    try:
        loopback_management = bool(
            server
            and server[0]
            and ipaddress.ip_address(str(server[0]).split("%", 1)[0]).is_loopback
        )
    except ValueError:
        loopback_management = False
    if not loopback_management or not _local_computer(request):
        raise HTTPException(403, "根证书只能从电脑本机管理入口导出")
    if not access.enabled or access.tls_material is None or access.root_certificate_pem is None:
        raise HTTPException(409, "局域网 HTTPS 尚未准备好")
    fingerprint = access.tls_material.root_fingerprint_sha256[:12].lower()
    response = Response(
        content=access.root_certificate_pem,
        media_type="application/x-x509-ca-cert",
        headers={
            "Content-Disposition": f'attachment; filename="sekai-lan-root-{fingerprint}.crt"',
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
    return response


@router.post("/api/v1/lan/close")
async def close_lan(request: Request):
    access = _access(request)
    if not _local_computer(request):
        raise HTTPException(403, "请在电脑本机关闭局域网访问")
    if not access.enabled:
        raise HTTPException(409, "局域网访问已经关闭；请刷新本机状态。")

    server = request.scope.get("server")
    try:
        server_address = server[0] if server else None
        is_loopback_listener = server_address is not None and ipaddress.ip_address(
            str(server_address).split("%", 1)[0]
        ).is_loopback
    except ValueError:
        is_loopback_listener = False
    if not is_loopback_listener:
        raise HTTPException(409, "请从电脑本机的 localhost 设置页关闭 LAN 监听。")

    stop_lan = getattr(request.app.state, "stop_lan_listener", None)
    if not callable(stop_lan):
        raise HTTPException(409, "当前启动方式无法单独停止 LAN 监听；请运行 stop_server.bat。")

    try:
        session_state_cleanup_confirmed = access.close_lan()
    except Exception:
        # Listener shutdown is still required even if storage cleanup fails.
        access.enabled = False
        session_state_cleanup_confirmed = False
    try:
        result = await stop_lan()
    except Exception:
        raise HTTPException(
            500,
            "LAN 会话已撤销并禁止新的远程请求，但无法确认监听器已停止；请运行 stop_server.bat。",
        ) from None

    listener_stopped = bool(result.get("listener_stopped")) if isinstance(result, dict) else False
    firewall_rule_removed = bool(result.get("firewall_rule_removed")) if isinstance(result, dict) else False
    if not listener_stopped:
        raise HTTPException(
            500,
            "LAN 会话已撤销并禁止新的远程请求，但无法确认监听器已停止；请运行 stop_server.bat。",
        )
    detail = None
    if not firewall_rule_removed:
        detail = "监听已停止；防火墙规则清理未确认，请在再次启用 LAN 前检查。"
    if not session_state_cleanup_confirmed:
        detail = "本进程内会话已撤销且监听已停止，但磁盘会话清理未确认；请检查 LAN 状态目录，并在清理前不要重新启用 LAN。"
    response = JSONResponse(
        {
            "ok": firewall_rule_removed and session_state_cleanup_confirmed,
            "listener_stopped": True,
            "firewall_rule_removed": firewall_rule_removed,
            "session_state_cleanup_confirmed": session_state_cleanup_confirmed,
            "detail": detail,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response
