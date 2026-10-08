from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from starlette.requests import Request

from mrp.server.lan_access import (
    PAIR_BODY_MAX_INFLIGHT,
    SESSION_IDLE_TTL_SECONDS,
    SESSION_NORMAL_ABSOLUTE_TTL_SECONDS,
    SESSION_NORMAL_IDLE_TTL_SECONDS,
    SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS,
    SESSION_REMEMBERED_IDLE_TTL_SECONDS,
    LanAccess,
    LanAccessMiddleware,
    SessionPersistenceError,
)
from mrp.server.lan_runtime import serve_lan_with_local_control
from mrp.server.lan_tls import LanTLSSetupError, prepare_server_tls
from mrp.server.routers.lan import (
    _pair_payload,
    close_lan,
    export_lan_root_ca,
    lan_sessions,
    lan_status,
    logout,
    revoke_all_lan_sessions,
    revoke_lan_session,
    rotate_lan_code,
    pair,
)


def _configure_lan(monkeypatch: pytest.MonkeyPatch, session_store_path=None, *, access_code="ABCDEFGHJKMN") -> LanAccess:
    temporary_tls = getattr(monkeypatch, "_mrp_tls_test_directory", None)
    if temporary_tls is None:
        temporary_tls = tempfile.TemporaryDirectory(prefix="mrp-lan-tests-")
        monkeypatch.setattr(monkeypatch, "_mrp_tls_test_directory", temporary_tls, raising=False)
    material = prepare_server_tls(
        "192.168.40.10",
        store_path=Path(temporary_tls.name) / "security" / "lan-tls",
        code_root=Path(__file__).resolve().parents[3],
    )
    monkeypatch.setenv("MRP_LAN_TLS_STORE_DIR", str(material.store_path))
    monkeypatch.setenv("MRP_LAN_TLS_CERT_FILE", str(material.certificate_path))
    monkeypatch.setenv("MRP_LAN_TLS_KEY_FILE", str(material.private_key_path))
    monkeypatch.setenv("MRP_LAN_TLS_ROOT_FILE", str(material.root_certificate_path))
    monkeypatch.setenv("MRP_LAN_MODE", "1")
    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", access_code)
    monkeypatch.setenv("MRP_LAN_BIND_IP", "192.168.40.10")
    monkeypatch.setenv("MRP_LAN_SUBNET", "192.168.40.0/24")
    monkeypatch.setenv("MRP_LAN_INTERFACE_INDEX", "7")
    monkeypatch.setenv("MRP_LAN_INTERFACE_ALIAS", "Wi-Fi")
    monkeypatch.setenv("MRP_PORT", "8000")
    return LanAccess(session_store_path=session_store_path)


def test_lan_mode_requires_explicit_selected_private_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MRP_LAN_MODE", "1")
    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", "ABCDEFGHJKMN")
    monkeypatch.delenv("MRP_LAN_BIND_IP", raising=False)
    monkeypatch.delenv("MRP_LAN_SUBNET", raising=False)
    monkeypatch.delenv("MRP_LAN_INTERFACE_INDEX", raising=False)
    monkeypatch.delenv("MRP_LAN_INTERFACE_ALIAS", raising=False)
    with pytest.raises(RuntimeError, match="selected IPv4"):
        LanAccess()


def test_selected_destination_and_client_subnet_are_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    access = _configure_lan(monkeypatch)
    base = {
        "type": "http",
        "scheme": "https",
        "client": ("192.168.40.25", 50123),
        "server": ("192.168.40.10", 8000),
    }
    assert access.request_target_allowed(base)
    assert access.host_allowed([(b"host", b"192.168.40.10:8000")], base)
    assert not access.request_target_allowed({**base, "server": ("192.168.41.10", 8000)})
    assert not access.request_target_allowed({**base, "client": ("192.168.41.25", 50123)})
    assert not access.host_allowed([(b"host", b"192.168.40.11:8000")], base)
    loopback = {**base, "scheme": "http", "client": ("127.0.0.1", 50123), "server": ("127.0.0.1", 8000)}
    assert access.request_target_allowed(loopback)
    assert not access.request_target_allowed({**loopback, "client": ("192.168.40.25", 50123)})


def test_desktop_can_use_selected_lan_address_without_pairing_but_peer_cannot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = _configure_lan(monkeypatch)
    entered_app = []

    async def app(scope, receive, send):
        entered_app.append(scope["client"][0])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = LanAccessMiddleware(app, access)

    async def request(client_ip: str):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/lan/status",
            "scheme": "https",
            "client": (client_ip, 50123),
            "server": ("192.168.40.10", 8000),
            "headers": [(b"host", b"192.168.40.10:8000"), (b"accept", b"application/json")],
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)
        return sent[0]["status"]

    assert asyncio.run(request("192.168.40.10")) == 204
    assert asyncio.run(request("192.168.40.25")) == 401
    assert entered_app == ["192.168.40.10"]


def test_unauthenticated_api_distinguishes_expired_cookie_from_unpaired_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = _configure_lan(monkeypatch)
    middleware = LanAccessMiddleware(lambda scope, receive, send: None, access)

    async def request(cookie: bytes | None):
        headers = [(b"host", b"192.168.40.10:8000"), (b"accept", b"application/json")]
        if cookie:
            headers.append((b"cookie", cookie))
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/stories",
            "scheme": "https",
            "client": ("192.168.40.25", 50123),
            "server": ("192.168.40.10", 8000),
            "headers": headers,
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)
        return sent

    unpaired = asyncio.run(request(None))
    expired = asyncio.run(request(b"mrp_lan_session=stale-token"))
    assert json.loads(unpaired[1]["body"])["code"] == "lan_pairing_required"
    assert json.loads(expired[1]["body"])["code"] == "lan_session_expired"


def test_status_reports_one_selected_binding_and_firewall_rule_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    marker = tmp_path / "firewall.status"
    monkeypatch.setenv("MRP_LAN_FIREWALL_STATUS_FILE", str(marker))
    access = _configure_lan(monkeypatch)
    status = access.status()
    assert status["bind_address"] == "192.168.40.10"
    assert status["interfaces"] == [
        {
            "ip": "192.168.40.10",
            "url": "https://192.168.40.10:8000/",
            "name": "Wi-Fi",
            "interface_index": 7,
            "subnet": "192.168.40.0/24",
        }
    ]
    assert status["firewall_rule_verified"] is False
    assert "firewall_verified" not in status
    assert status["transport"] == "https"
    assert status["https_available"] is True
    assert status["root_ca_fingerprint_sha256"] == access.tls_material.root_fingerprint_sha256
    assert status["session_policies"] == {
        "normal": {"idle_days": 7, "absolute_days": 30},
        "remembered": {"idle_days": 30, "absolute_days": 90},
    }
    assert status["session_store_persistent"] is False
    assert status["session_store_available"] is True
    assert status["session_state_cleanup_confirmed"] is True
    assert status["public_reachability"] == "unverified"
    marker.write_text("verified", encoding="ascii")
    assert access.status()["firewall_rule_verified"] is True


def test_loopback_mutations_require_same_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    access = _configure_lan(monkeypatch)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = LanAccessMiddleware(app, access)

    async def request(origin: str):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/write",
            "scheme": "https",
            "client": ("127.0.0.1", 50000),
            "server": ("192.168.40.10", 8000),
            "headers": [(b"host", b"192.168.40.10:8000"), (b"origin", origin.encode())],
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)
        return sent[0]["status"]

    assert asyncio.run(request("https://attacker.example")) == 403
    assert asyncio.run(request("https://192.168.40.10:8000")) == 204


def test_websocket_requires_same_origin_even_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    access = _configure_lan(monkeypatch)
    middleware = LanAccessMiddleware(lambda scope, receive, send: None, access)
    scope = {
        "type": "websocket",
        "scheme": "wss",
        "client": ("127.0.0.1", 50000),
        "server": ("192.168.40.10", 8000),
        "headers": [(b"host", b"192.168.40.10:8000"), (b"origin", b"https://attacker.example")],
    }
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    assert sent == [{"type": "websocket.close", "code": 4403}]


def test_pairing_body_limit_is_checked_before_json_parse() -> None:
    body = b"{" + b" " * 2048
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/lan/pair",
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        "client": ("192.168.40.25", 50000),
        "server": ("192.168.40.10", 8000),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    with pytest.raises(HTTPException) as caught:
        asyncio.run(_pair_payload(Request(scope, receive)))
    assert caught.value.status_code == 413


def test_pair_payload_defaults_to_remembered_and_accepts_explicit_opt_out() -> None:
    async def parse(payload: dict[str, object]) -> tuple[str, bool]:
        body = json.dumps(payload).encode()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/lan/pair",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("192.168.40.25", 50000),
            "server": ("192.168.40.10", 8000),
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        return await _pair_payload(Request(scope, receive))

    assert asyncio.run(parse({"access_code": "ABCDEFGHJKMN"})) == ("ABCDEFGHJKMN", True)
    assert asyncio.run(parse({"access_code": "ABCDEFGHJKMN", "remember": False})) == ("ABCDEFGHJKMN", False)


def test_pair_response_cookie_ttl_matches_selected_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    access = _configure_lan(monkeypatch)
    app = SimpleNamespace(state=SimpleNamespace(lan_access=access))

    async def post(remember: bool):
        body = json.dumps({"access_code": "ABCDEFGHJKMN", "remember": remember}).encode()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/lan/pair",
            "scheme": "https",
            "headers": [
                (b"host", b"192.168.40.10:8000"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("192.168.40.25", 50123),
            "server": ("192.168.40.10", 8000),
            "app": app,
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        return await pair(Request(scope, receive))

    remembered = asyncio.run(post(True))
    remembered_payload = json.loads(remembered.body)
    assert remembered_payload["expires_in"] == SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS
    assert remembered_payload["idle_expires_in"] == SESSION_REMEMBERED_IDLE_TTL_SECONDS
    assert remembered_payload["remembered"] is True
    assert f"max-age={SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS}" in remembered.headers["set-cookie"].lower()
    assert "; secure" in remembered.headers["set-cookie"].lower()

    normal = asyncio.run(post(False))
    normal_payload = json.loads(normal.body)
    assert normal_payload["expires_in"] == SESSION_NORMAL_ABSOLUTE_TTL_SECONDS
    assert normal_payload["idle_expires_in"] == SESSION_NORMAL_IDLE_TTL_SECONDS
    assert normal_payload["remembered"] is False
    assert f"max-age={SESSION_NORMAL_ABSOLUTE_TTL_SECONDS}" in normal.headers["set-cookie"].lower()
    assert "; secure" in normal.headers["set-cookie"].lower()


def test_root_ca_export_is_public_and_loopback_management_only(monkeypatch: pytest.MonkeyPatch) -> None:
    access = _configure_lan(monkeypatch)
    app = SimpleNamespace(state=SimpleNamespace(lan_access=access))

    def request(client: str, server: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/lan/root-ca.crt",
                "scheme": "http",
                "headers": [(b"host", f"{server}:8000".encode())],
                "client": (client, 50000),
                "server": (server, 8000),
                "app": app,
            }
        )

    exported = export_lan_root_ca(request("127.0.0.1", "127.0.0.1"))
    assert exported.status_code == 200
    assert b"BEGIN CERTIFICATE" in exported.body
    assert b"PRIVATE KEY" not in exported.body
    assert exported.headers["cache-control"] == "no-store"
    assert exported.headers["x-content-type-options"] == "nosniff"

    with pytest.raises(HTTPException) as caught:
        export_lan_root_ca(request("127.0.0.1", "192.168.40.10"))
    assert caught.value.status_code == 403
    with pytest.raises(HTTPException) as caught:
        export_lan_root_ca(request("192.168.40.25", "127.0.0.1"))
    assert caught.value.status_code == 403


def test_pairing_body_readers_are_bounded_before_consuming_request_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = _configure_lan(monkeypatch)
    app = SimpleNamespace(state=SimpleNamespace(lan_access=access))
    for _ in range(PAIR_BODY_MAX_INFLIGHT):
        assert access.reserve_pair_body_read()

    async def unexpected_body_read():
        raise AssertionError("a saturated pairing request must be rejected before reading its body")

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/lan/pair",
            "scheme": "https",
            "headers": [(b"host", b"192.168.40.10:8000")],
            "client": ("192.168.40.25", 50123),
            "server": ("192.168.40.10", 8000),
            "app": app,
        },
        unexpected_body_read,
    )
    response = asyncio.run(pair(request))
    assert response.status_code == 429
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "1"
    for _ in range(PAIR_BODY_MAX_INFLIGHT):
        access.release_pair_body_read()


def test_pairing_body_reader_slot_is_released_and_connection_closed_on_oversize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = _configure_lan(monkeypatch)
    app = SimpleNamespace(state=SimpleNamespace(lan_access=access))
    body = b"{" + b" " * 2048
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/lan/pair",
            "scheme": "https",
            "headers": [
                (b"host", b"192.168.40.10:8000"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("192.168.40.25", 50123),
            "server": ("192.168.40.10", 8000),
            "app": app,
        },
        lambda: None,
    )

    response = asyncio.run(pair(request))

    assert response.status_code == 413
    assert response.headers["connection"] == "close"
    assert access._inflight_pair_body_reads == 0


def test_sensitive_lan_responses_are_no_store_except_fingerprinted_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = _configure_lan(monkeypatch)
    token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone")
    assert token is not None

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"cache-control", b"private, max-age=86400")],
            }
        )
        await send({"type": "http.response.body", "body": b"sensitive"})

    middleware = LanAccessMiddleware(app, access)

    async def request(path: str):
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "scheme": "https",
            "client": ("192.168.40.25", 50123),
            "server": ("192.168.40.10", 8000),
            "headers": [
                (b"host", b"192.168.40.10:8000"),
                (b"cookie", f"mrp_lan_session={token}".encode()),
            ],
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)
        return dict(sent[0]["headers"])

    sensitive_paths = (
        "/api/v1/settings",
        "/api/v1/stories/story-1",
        "/api/v1/chats/story-1",
        "/api/v1/stories/story-1/export",
        "/api/v1/media/story-1/image.png",
    )
    for path in sensitive_paths:
        headers = asyncio.run(request(path))
        assert headers[b"cache-control"] == b"no-store", path
        assert headers[b"pragma"] == b"no-cache", path

    hashed_asset = asyncio.run(request("/assets/index-a1b2c3d4e5.js"))
    assert hashed_asset[b"cache-control"] == b"private, max-age=86400"
    assert b"pragma" not in hashed_asset
    non_hashed_asset = asyncio.run(request("/assets/user-avatar.png"))
    assert non_hashed_asset[b"cache-control"] == b"no-store"


def test_active_remote_stream_stops_sending_after_lan_close(monkeypatch: pytest.MonkeyPatch) -> None:
    access = _configure_lan(monkeypatch)
    token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone")
    assert token is not None
    started = asyncio.Event()
    continue_stream = asyncio.Event()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"before-close", "more_body": True})
        started.set()
        await continue_stream.wait()
        await send({"type": "http.response.body", "body": b"after-close", "more_body": True})
        await send({"type": "http.response.body", "body": b"done", "more_body": False})

    middleware = LanAccessMiddleware(app, access)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/stories/story-1/stream",
        "scheme": "https",
        "client": ("192.168.40.25", 50123),
        "server": ("192.168.40.10", 8000),
        "headers": [
            (b"host", b"192.168.40.10:8000"),
            (b"cookie", f"mrp_lan_session={token}".encode()),
        ],
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    async def run_stream() -> None:
        stream_task = asyncio.create_task(middleware(scope, receive, send))
        await started.wait()
        access.close_lan()
        continue_stream.set()
        await stream_task

    asyncio.run(run_stream())

    starts = [message for message in messages if message["type"] == "http.response.start"]
    bodies = [message.get("body", b"") for message in messages if message["type"] == "http.response.body"]
    assert len(starts) == 1
    assert starts[0]["status"] == 200
    assert bodies == [b"before-close", b""]
    assert all(b"after-close" not in body for body in bodies)


def test_close_lan_stops_through_local_listener_and_revokes_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = _configure_lan(monkeypatch)
    token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone")
    assert token is not None
    stopped = []

    async def stop_lan_listener():
        stopped.append(True)
        return {"listener_stopped": True, "firewall_rule_removed": True}

    app = SimpleNamespace(
        state=SimpleNamespace(lan_access=access, stop_lan_listener=stop_lan_listener)
    )

    def request(client_ip: str, destination: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/lan/close",
                "scheme": "http" if destination == "127.0.0.1" else "https",
                "headers": [
                    (b"host", f"{destination}:8000".encode()),
                    (b"origin", f"{'http' if destination == '127.0.0.1' else 'https'}://{destination}:8000".encode()),
                ],
                "client": (client_ip, 50123),
                "server": (destination, 8000),
                "app": app,
            }
        )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(close_lan(request("192.168.40.25", "192.168.40.10")))
    assert caught.value.status_code == 403
    assert access.enabled
    assert not stopped

    with pytest.raises(HTTPException) as caught:
        asyncio.run(close_lan(request("127.0.0.1", "192.168.40.10")))
    assert caught.value.status_code == 409
    assert access.enabled

    response = asyncio.run(close_lan(request("127.0.0.1", "127.0.0.1")))
    payload = json.loads(response.body)
    assert payload == {
        "ok": True,
        "listener_stopped": True,
        "firewall_rule_removed": True,
        "session_state_cleanup_confirmed": True,
        "detail": None,
    }
    assert response.headers["cache-control"] == "no-store"
    assert stopped == [True]
    assert not access.enabled
    assert access.list_sessions() == []
    assert not access.is_session_valid([(b"cookie", f"mrp_lan_session={token}".encode())])


def test_close_stops_listener_even_if_session_store_write_and_unlink_fail(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = tmp_path / "lan-sessions.json"
    access = _configure_lan(monkeypatch, store)
    token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone")
    assert token
    stopped = []

    async def stop_lan_listener():
        stopped.append(True)
        return {"listener_stopped": True, "firewall_rule_removed": True}

    app = SimpleNamespace(state=SimpleNamespace(lan_access=access, stop_lan_listener=stop_lan_listener))
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/lan/close",
            "scheme": "http",
            "headers": [
                (b"host", b"127.0.0.1:8000"),
                (b"origin", b"http://127.0.0.1:8000"),
            ],
            "client": ("127.0.0.1", 50123),
            "server": ("127.0.0.1", 8000),
            "app": app,
        }
    )
    original_unlink = Path.unlink

    with monkeypatch.context() as patcher:
        def fail_persist(*, force=False):
            raise SessionPersistenceError("injected write failure")

        def fail_store_unlink(path: Path, *args, **kwargs):
            if path == store:
                raise PermissionError("injected unlink failure")
            return original_unlink(path, *args, **kwargs)

        patcher.setattr(access, "_persist_locked", fail_persist)
        patcher.setattr(Path, "unlink", fail_store_unlink)
        response = asyncio.run(close_lan(request))

    payload = json.loads(response.body)
    assert payload["listener_stopped"] is True
    assert payload["session_state_cleanup_confirmed"] is False
    assert "磁盘会话清理未确认" in payload["detail"]
    assert stopped == [True]
    assert not access.enabled
    assert not access.is_session_valid([(b"cookie", f"mrp_lan_session={token}".encode())])
    assert store.with_name(store.name + ".invalid").exists()


def test_runtime_stops_lan_listener_and_active_connections_but_keeps_loopback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MRP_LAN_FIREWALL_STATUS_FILE", raising=False)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    tls_material = prepare_server_tls(
        "127.0.0.2",
        store_path=tmp_path / "security" / "lan-tls",
        code_root=Path(__file__).resolve().parents[3],
    )
    trusted_context = ssl.create_default_context(cafile=str(tls_material.root_certificate_path))

    web_app = FastAPI()
    stream_cancelled = asyncio.Event()

    @web_app.get("/health")
    async def health():
        return {"ok": True}

    @web_app.get("/stream")
    async def stream():
        async def chunks():
            try:
                yield b"active-stream\n"
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                stream_cancelled.set()
                raise

        return StreamingResponse(chunks(), media_type="text/plain")

    firewall_calls = []

    async def firewall_disabler(project_root, requested_port):
        assert project_root == tmp_path
        assert requested_port == port
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.2", port)
        firewall_calls.append(True)
        return True

    async def run_check() -> None:
        runtime_task = asyncio.create_task(
            serve_lan_with_local_control(
                web_app,
                "127.0.0.2",
                port,
                tls_material=tls_material,
                project_root=tmp_path,
                firewall_disabler=firewall_disabler,
            )
        )
        try:
            for _ in range(200):
                if callable(getattr(web_app.state, "stop_lan_listener", None)):
                    break
                if runtime_task.done():
                    await runtime_task
                await asyncio.sleep(0.01)
            assert callable(getattr(web_app.state, "stop_lan_listener", None))

            for host in ("127.0.0.1", "127.0.0.2"):
                if host == "127.0.0.2":
                    reader, writer = await asyncio.open_connection(
                        host, port, ssl=trusted_context, server_hostname=host
                    )
                else:
                    reader, writer = await asyncio.open_connection(host, port)
                writer.write(
                    f"GET /health HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode()
                )
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), timeout=2)
                writer.close()
                await writer.wait_closed()
                assert b"200 OK" in response

            plaintext_reader, plaintext_writer = await asyncio.open_connection("127.0.0.2", port)
            plaintext_writer.write(
                f"GET /health HTTP/1.1\r\nHost: 127.0.0.2:{port}\r\nConnection: close\r\n\r\n".encode()
            )
            await plaintext_writer.drain()
            plaintext_response = await asyncio.wait_for(plaintext_reader.read(), timeout=2)
            assert b"200 OK" not in plaintext_response
            plaintext_writer.close()
            await plaintext_writer.wait_closed()

            reader, writer = await asyncio.open_connection(
                "127.0.0.2", port, ssl=trusted_context, server_hostname="127.0.0.2"
            )
            writer.write(
                f"GET /stream HTTP/1.1\r\nHost: 127.0.0.2:{port}\r\nConnection: keep-alive\r\n\r\n".encode()
            )
            await writer.drain()
            response_head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
            assert b"200 OK" in response_head
            assert b"transfer-encoding: chunked" in response_head.lower()
            first_chunk_size = int((await asyncio.wait_for(reader.readuntil(b"\r\n"), timeout=2)).strip(), 16)
            first_chunk = await asyncio.wait_for(reader.readexactly(first_chunk_size + 2), timeout=2)
            assert first_chunk[:-2] == b"active-stream\n"

            result = await web_app.state.stop_lan_listener()
            assert result == {"listener_stopped": True, "firewall_rule_removed": True}
            assert firewall_calls == [True]
            await asyncio.wait_for(stream_cancelled.wait(), timeout=2)
            assert await asyncio.wait_for(reader.read(), timeout=2) == b""
            writer.close()
            await writer.wait_closed()

            with pytest.raises(OSError):
                await asyncio.open_connection("127.0.0.2", port)

            local_reader, local_writer = await asyncio.open_connection("127.0.0.1", port)
            local_writer.write(
                f"GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode()
            )
            await local_writer.drain()
            assert b"200 OK" in await asyncio.wait_for(local_reader.read(), timeout=2)
            local_writer.close()
            await local_writer.wait_closed()
        finally:
            if callable(getattr(web_app.state, "stop_local_listener", None)):
                await web_app.state.stop_local_listener()
            await runtime_task

    asyncio.run(run_check())


def test_pairing_attempts_are_reserved_before_concurrent_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    access = _configure_lan(monkeypatch)
    both_entered = threading.Event()
    arrival_lock = threading.Lock()
    arrivals = 0
    release_hash = threading.Event()

    def slow_digest(value: str) -> bytes:
        nonlocal arrivals
        with arrival_lock:
            arrivals += 1
            if arrivals == 2:
                both_entered.set()
        both_entered.wait(timeout=3)
        release_hash.wait(timeout=3)
        return bytes(32)

    access._digest_code = slow_digest  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(access.check_code, "192.168.40.25", "AAAAAAAAAAAA")
        second = pool.submit(access.check_code, "192.168.40.25", "AAAAAAAAAAAA")
        assert both_entered.wait(timeout=3)
        assert access.check_code("192.168.40.25", "AAAAAAAAAAAA") == (None, 1)
        release_hash.set()
        assert first.result(timeout=3) == (None, 0)
        assert second.result(timeout=3) == (None, 0)

    access._digest_code = lambda value: bytes(32)  # type: ignore[method-assign]
    for _ in range(3):
        assert access.check_code("192.168.40.25", "AAAAAAAAAAAA") == (None, 0)
    token, retry_after = access.check_code("192.168.40.25", "AAAAAAAAAAAA")
    assert token is None
    assert retry_after > 0
    assert access.check_code("192.168.40.25", "AAAAAAAAAAAA")[1] > 0


def test_device_session_metadata_idle_expiry_and_individual_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = _configure_lan(monkeypatch)
    token, retry_after = access.check_code(
        "192.168.40.25", "ABCDEFGHJKMN", "Mozilla/5.0 Android Chrome/140"
    )
    assert token is not None and retry_after == 0
    cookie = [(b"cookie", f"mrp_lan_session={token}".encode())]
    devices = access.list_sessions()
    assert len(devices) == 1
    device = devices[0]
    assert device["device_name"] == "Mozilla/5.0 Android Chrome/140"
    assert device["ip"] == "192.168.40.25"
    assert "token" not in device and "digest" not in device
    assert access.is_session_valid(cookie)

    record = access._sessions[access._digest_session(token)]
    record.last_activity_monotonic = time.monotonic() - SESSION_IDLE_TTL_SECONDS + 10
    stale_activity = record.last_activity_monotonic
    assert access.is_session_valid(cookie)
    assert record.last_activity_monotonic > stale_activity
    record.last_activity_monotonic = time.monotonic() - SESSION_IDLE_TTL_SECONDS - 1
    assert not access.is_session_valid(cookie)
    assert access.list_sessions() == []


def test_session_tiers_persist_digest_only_and_resume_on_same_interface(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = tmp_path / "lan-sessions.json"
    access = _configure_lan(monkeypatch, store)
    normal, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Private phone", remember=False)
    remembered, _ = access.check_code("192.168.40.26", "ABCDEFGHJKMN", "Own phone", remember=True)
    assert normal and remembered

    normal_session = access._sessions[access._digest_session(normal)]
    remembered_session = access._sessions[access._digest_session(remembered)]
    assert normal_session.remembered is False
    assert normal_session.idle_ttl_seconds == SESSION_NORMAL_IDLE_TTL_SECONDS
    assert normal_session.absolute_expires_at - normal_session.created_at == pytest.approx(SESSION_NORMAL_ABSOLUTE_TTL_SECONDS)
    assert remembered_session.remembered is True
    assert remembered_session.idle_ttl_seconds == SESSION_REMEMBERED_IDLE_TTL_SECONDS
    assert remembered_session.absolute_expires_at - remembered_session.created_at == pytest.approx(SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS)

    serialized = store.read_text(encoding="utf-8")
    assert normal not in serialized and remembered not in serialized
    state = json.loads(serialized)
    assert state["version"] == 2
    assert state["transport"] == "https"
    assert len(state["sessions"]) == 2
    assert all("digest" in row and "token" not in row for row in state["sessions"])

    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", "ABCDEFGHJKMN")
    restarted = _configure_lan(monkeypatch, store)
    assert restarted.is_session_valid([(b"cookie", f"mrp_lan_session={normal}".encode())])
    assert restarted.is_session_valid([(b"cookie", f"mrp_lan_session={remembered}".encode())])
    assert {row["remembered"] for row in restarted.list_sessions()} == {False, True}


def test_http_session_store_is_invalidated_after_https_upgrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = tmp_path / "lan-sessions.json"
    access = _configure_lan(monkeypatch, store)
    token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Old HTTP phone")
    assert token
    payload = json.loads(store.read_text(encoding="utf-8"))
    payload["version"] = 1
    payload.pop("transport", None)
    store.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", "ABCDEFGHJKMN")
    restarted = _configure_lan(monkeypatch, store)
    assert restarted.list_sessions() == []
    assert not restarted.is_session_valid([(b"cookie", f"mrp_lan_session={token}".encode())])
    assert not store.exists()


def test_windows_acl_write_failures_preserve_pairing_and_revocation_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import mrp.server.lan_access as lan_access_module

    store = tmp_path / "lan-sessions.json"
    access = _configure_lan(monkeypatch, store)
    existing_token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Existing phone")
    assert existing_token
    session_id = access.list_sessions()[0]["session_id"]

    temporary_fds: list[int] = []
    original_mkstemp = tempfile.mkstemp

    def record_mkstemp(*args, **kwargs):
        descriptor, path = original_mkstemp(*args, **kwargs)
        temporary_fds.append(descriptor)
        return descriptor, path

    def fail_acl(_path):
        raise LanTLSSetupError("injected ACL setup failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(lan_access_module, "_secure_session_file", fail_acl)
        patcher.setattr(tempfile, "mkstemp", record_mkstemp)

        body = json.dumps({"access_code": "ABCDEFGHJKMN", "remember": True}).encode()
        app = SimpleNamespace(state=SimpleNamespace(lan_access=access))
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/lan/pair",
                "scheme": "https",
                "headers": [
                    (b"host", b"192.168.40.10:8000"),
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
                "client": ("192.168.40.26", 50123),
                "server": ("192.168.40.10", 8000),
                "app": app,
            },
            lambda: asyncio.sleep(0, result={"type": "http.request", "body": body, "more_body": False}),
        )
        with pytest.raises(HTTPException) as caught:
            asyncio.run(pair(request))
        assert caught.value.status_code == 503
        assert access.is_session_valid([(b"cookie", f"mrp_lan_session={existing_token}".encode())])
        assert len(access.list_sessions()) == 1
        assert temporary_fds
        with pytest.raises(OSError):
            os.fstat(temporary_fds[0])

        original_unlink = Path.unlink

        def fail_store_unlink(path: Path, *args, **kwargs):
            if path == store:
                raise PermissionError("injected session-store unlink failure")
            return original_unlink(path, *args, **kwargs)

        patcher.setattr(Path, "unlink", fail_store_unlink)
        assert access.revoke_session_id(session_id)
        assert not access.last_cleanup_confirmed
        marker = store.with_name(store.name + ".invalid")
        assert marker.exists()
        assert marker.read_bytes() == b""
        assert not access.is_session_valid([(b"cookie", f"mrp_lan_session={existing_token}".encode())])


def test_persisted_sessions_expire_on_idle_and_absolute_deadlines(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = tmp_path / "lan-sessions.json"
    access = _configure_lan(monkeypatch, store)
    idle_token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", remember=False)
    assert idle_token
    payload = json.loads(store.read_text(encoding="utf-8"))
    payload["sessions"][0]["last_activity_at"] = time.time() - SESSION_NORMAL_IDLE_TTL_SECONDS - 1
    payload["saved_at"] = time.time()
    store.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", "ABCDEFGHJKMN")
    idle_restart = _configure_lan(monkeypatch, store)
    assert idle_restart.list_sessions() == []

    absolute_store = tmp_path / "absolute-sessions.json"
    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", "ABCDEFGHJKMN")
    absolute_access = _configure_lan(monkeypatch, absolute_store)
    absolute_token, _ = absolute_access.check_code("192.168.40.25", "ABCDEFGHJKMN")
    assert absolute_token
    payload = json.loads(absolute_store.read_text(encoding="utf-8"))
    payload["sessions"][0]["absolute_expires_at"] = time.time() - 1
    payload["saved_at"] = time.time()
    absolute_store.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", "ABCDEFGHJKMN")
    absolute_restart = _configure_lan(monkeypatch, absolute_store)
    assert absolute_restart.list_sessions() == []


def test_persisted_revoke_all_rotation_and_logout_survive_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = tmp_path / "lan-sessions.json"
    access = _configure_lan(monkeypatch, store)
    first, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone A")
    second, _ = access.check_code("192.168.40.26", "ABCDEFGHJKMN", "Phone B")
    assert first and second
    first_id = next(row["session_id"] for row in access.list_sessions() if row["ip"] == "192.168.40.25")
    assert access.revoke_session_id(first_id)
    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", "ABCDEFGHJKMN")
    restarted = _configure_lan(monkeypatch, store)
    assert not restarted.is_session_valid([(b"cookie", f"mrp_lan_session={first}".encode())])
    assert restarted.is_session_valid([(b"cookie", f"mrp_lan_session={second}".encode())])
    assert restarted.revoke_all_sessions() == 1

    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", "ABCDEFGHJKMN")
    after_revoke_all = _configure_lan(monkeypatch, store)
    assert after_revoke_all.list_sessions() == []
    replacement, _ = after_revoke_all.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone C")
    assert replacement
    new_code = after_revoke_all.rotate_access_code()
    assert after_revoke_all.last_cleanup_confirmed
    assert after_revoke_all.list_sessions() == []
    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", new_code.replace("-", ""))
    after_rotation = _configure_lan(monkeypatch, store, access_code=new_code.replace("-", ""))
    assert after_rotation.list_sessions() == []
    assert after_rotation.check_code("192.168.40.25", "ABCDEFGHJKMN")[0] is None
    assert after_rotation.check_code("192.168.40.25", new_code)[0] is not None


def test_store_write_and_unlink_failure_leaves_marker_and_blocks_stale_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = tmp_path / "lan-sessions.json"
    access = _configure_lan(monkeypatch, store)
    token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone")
    assert token
    session_id = access.list_sessions()[0]["session_id"]
    original_unlink = Path.unlink

    with monkeypatch.context() as patcher:
        def fail_persist(*, force=False):
            raise SessionPersistenceError("injected write failure")

        def fail_store_unlink(path: Path, *args, **kwargs):
            if path == store:
                raise PermissionError("injected unlink failure")
            return original_unlink(path, *args, **kwargs)

        patcher.setattr(access, "_persist_locked", fail_persist)
        patcher.setattr(Path, "unlink", fail_store_unlink)
        assert access.revoke_session_id(session_id)
        assert access.last_cleanup_confirmed is False
        assert access.list_sessions() == []
        assert store.with_name(store.name + ".invalid").exists()
        assert store.exists()  # The stale file remains but is protected by the marker.

    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", "ABCDEFGHJKMN")
    recovered = _configure_lan(monkeypatch, store)
    assert recovered.list_sessions() == []
    assert not recovered.is_session_valid([(b"cookie", f"mrp_lan_session={token}".encode())])
    assert not store.exists()
    assert not store.with_name(store.name + ".invalid").exists()


def test_store_write_unlink_and_marker_failure_requires_manual_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = tmp_path / "lan-sessions.json"
    marker = store.with_name(store.name + ".invalid")
    access = _configure_lan(monkeypatch, store)
    token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone")
    assert token
    session_id = access.list_sessions()[0]["session_id"]
    original_unlink = Path.unlink
    original_open = Path.open

    with monkeypatch.context() as patcher:
        def fail_persist(*, force=False):
            raise SessionPersistenceError("injected write failure")

        def fail_store_unlink(path: Path, *args, **kwargs):
            if path == store:
                raise PermissionError("injected unlink failure")
            return original_unlink(path, *args, **kwargs)

        def fail_marker_open(path: Path, *args, **kwargs):
            if path == marker:
                raise PermissionError("injected marker failure")
            return original_open(path, *args, **kwargs)

        patcher.setattr(access, "_persist_locked", fail_persist)
        patcher.setattr(Path, "unlink", fail_store_unlink)
        patcher.setattr(Path, "open", fail_marker_open)
        assert access.revoke_session_id(session_id)
        assert access.last_cleanup_confirmed is False
        assert access.status()["session_store_available"] is False
        assert access.status()["session_state_cleanup_confirmed"] is False
        assert not access.is_session_valid([(b"cookie", f"mrp_lan_session={token}".encode())])
        assert store.exists() and marker.exists()
        with original_open(marker, "rb") as marker_file:
            assert marker_file.read() == b""

    # This exceptional filesystem failure leaves no durable invalidation signal;
    # production UI/docs therefore require manual deletion before another LAN start.
    assert json.loads(store.read_text(encoding="utf-8"))["sessions"]

    replacement, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Mobile browser")
    assert replacement is not None
    replacement_device = access.list_sessions()[0]
    assert access.revoke_session_id(replacement_device["session_id"])
    assert not access.is_session_valid([(b"cookie", f"mrp_lan_session={replacement}".encode())])
    assert access.list_sessions() == []

    absolute, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Mobile browser")
    assert absolute is not None
    access._sessions[access._digest_session(absolute)].expires_at = time.monotonic() - 1
    assert not access.is_session_valid([(b"cookie", f"mrp_lan_session={absolute}".encode())])
    assert access.list_sessions() == []


def test_rotating_access_code_invalidates_sessions_and_old_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = _configure_lan(monkeypatch)
    token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone")
    assert token is not None
    code = access.rotate_access_code()
    assert len(code.replace("-", "")) == 12
    assert access.list_sessions() == []
    assert not access.is_session_valid([(b"cookie", f"mrp_lan_session={token}".encode())])
    assert access.check_code("192.168.40.25", "ABCDEFGHJKMN")[0] is None
    new_token, retry_after = access.check_code("192.168.40.25", code)
    assert new_token is not None and retry_after == 0

    real_digest = access._digest_code
    hashing_started = threading.Event()
    release_hashing = threading.Event()
    old_normalized_code = code.replace("-", "").lower()

    def delayed_digest(value: str) -> bytes:
        if value == old_normalized_code:
            hashing_started.set()
            release_hashing.wait(timeout=3)
        return real_digest(value)

    access._digest_code = delayed_digest  # type: ignore[method-assign]
    with ThreadPoolExecutor(max_workers=1) as pool:
        in_flight = pool.submit(access.check_code, "192.168.40.25", code)
        assert hashing_started.wait(timeout=3)
        access.rotate_access_code()
        release_hashing.set()
        assert in_flight.result(timeout=3) == (None, 0)
    assert access.list_sessions() == []


def test_device_management_is_only_available_to_the_local_computer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = _configure_lan(monkeypatch)
    token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone")
    assert token is not None
    expected_code = access._expected_code
    app = SimpleNamespace(state=SimpleNamespace(lan_access=access))

    def request_from(client_ip: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/lan/sessions",
                "scheme": "https",
                "headers": [(b"host", b"192.168.40.10:8000")],
                "client": (client_ip, 50123),
                "server": ("192.168.40.10", 8000),
                "app": app,
            }
        )

    with pytest.raises(HTTPException) as caught:
        lan_sessions(request_from("192.168.40.25"))
    assert caught.value.status_code == 403

    phone_session_id = access.list_sessions()[0]["session_id"]
    for admin_action in (
        lambda: revoke_lan_session(phone_session_id, request_from("192.168.40.25")),
        lambda: revoke_all_lan_sessions(request_from("192.168.40.25")),
        lambda: rotate_lan_code(request_from("192.168.40.25")),
    ):
        with pytest.raises(HTTPException) as caught:
            admin_action()
        assert caught.value.status_code == 403
    assert len(access.list_sessions()) == 1
    assert access._expected_code == expected_code

    local_request = request_from("192.168.40.10")
    status = lan_status(local_request)
    assert json.loads(status.body)["can_manage_devices"] is True
    assert status.headers["cache-control"] == "no-store"
    listed = lan_sessions(local_request)
    assert json.loads(listed.body)["sessions"][0]["ip"] == "192.168.40.25"
    session_id = access.list_sessions()[0]["session_id"]
    revoked = revoke_lan_session(session_id, local_request)
    assert json.loads(revoked.body)["ok"] is True
    assert access.list_sessions() == []

    token, _ = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone")
    assert token is not None
    revoke_all = revoke_all_lan_sessions(local_request)
    assert json.loads(revoke_all.body)["revoked"] == 1
    rotated = rotate_lan_code(local_request)
    assert json.loads(rotated.body)["sessions_revoked"] is True
    assert rotated.headers["cache-control"] == "no-store"
    assert access.list_sessions() == []
    new_code = json.loads(rotated.body)["access_code"]
    assert access.check_code("192.168.40.25", "ABCDEFGHJKMN")[0] is None
    assert access.check_code("192.168.40.25", new_code)[0] is not None


def test_logout_invalidates_the_server_session_and_deletes_cookie(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    store = tmp_path / "lan-sessions.json"
    access = _configure_lan(monkeypatch, store)
    token, retry_after = access.check_code("192.168.40.25", "ABCDEFGHJKMN", "Phone")
    assert token is not None and retry_after == 0
    headers = [(b"cookie", f"mrp_lan_session={token}".encode())]
    app = SimpleNamespace(state=SimpleNamespace(lan_access=access))
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/lan/logout",
            "scheme": "https",
            "headers": headers,
            "client": ("192.168.40.25", 50123),
            "server": ("192.168.40.10", 8000),
            "app": app,
        }
    )

    response = logout(request)

    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    assert "mrp_lan_session=" in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert not access.is_session_valid(headers)
    monkeypatch.setenv("MRP_LAN_ACCESS_CODE", "ABCDEFGHJKMN")
    after_restart = _configure_lan(monkeypatch, store)
    assert not after_restart.is_session_valid(headers)
    assert after_restart.list_sessions() == []
