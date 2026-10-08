"""消息路由：发送 / 重roll / 编辑 / 删除 / 变体切换 / 续写 / 重生成 / 卫生复查。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator

from mrp.server.container import AppContainer
from mrp.server.deps import get_container, message_or_404, runner_or_404
from mrp.orchestrator.message_ops import MessageEditConflict
from mrp.orchestrator.message_regeneration import RegenerationRequest, RegenerationConflict, RegenerationFailure
from mrp.shared.models import new_id
from mrp.server.command_ids import command_id, command_payload, conflict_detail, execute_command
from mrp.contracts.story import project_message

router = APIRouter()


# ---------- 请求模型 ----------


class SendMessageReq(BaseModel):
    content: str = Field(max_length=20000)  # B17：防超长正文打爆引擎/日志
    operation_id: str | None = Field(None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    client_message_id: str | None = None  # 与前端即时气泡共用 id，SSE/HTTP 均可原位确认
    force_character: str | None = None
    mentions: list[str] = Field(default_factory=list, max_length=20)  # R30/D5：显式 @ 的角色 id
    channel: str = "dialogue"  # R22 玩家三通道：dialogue / inner / narration
    reply_mode: str | None = Field(None, pattern="^(auto|parallel|serial|free)$")
    max_replies: int = Field(6, ge=1, le=30)
    conversation_directive: str = Field("", max_length=4000)
    expected_player_identity_id: str | None = None


class PatchMessageReq(BaseModel):
    content: str | None = Field(None, max_length=20000)
    active_variant: int | None = None
    expected_branch_revision: int | None = Field(None, ge=0)
    expected_fingerprint: str | None = None
    expected_player_identity_id: str | None = None
    operation_id: str | None = None


class InputGroupPartReq(BaseModel):
    message_id: str
    expected_fingerprint: str
    content: str = Field(max_length=20000)


class PatchInputGroupReq(BaseModel):
    expected_branch_revision: int = Field(ge=0)
    parts: list[InputGroupPartReq] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_total_length(self):
        if sum(len(part.content) for part in self.parts) > 20000:
            raise ValueError("逻辑输入总长度不能超过 20000 字符")
        return self


# ---------- 端点 ----------


@router.post("/api/v1/sessions/{session_id}/messages")
async def send_message(
    session_id: str, req: SendMessageReq, container: AppContainer = Depends(get_container)
):
    """发送玩家消息并等待整回合结束（同步语义）。

    B16：原声明 202 但实现 `await` 完整回合（假异步）；前端 `jfetch` 只判断
    `res.ok`（200/202 等价）→ 改为 200 与真实语义一致。
    """
    r = await runner_or_404(container, session_id)
    send_operation = req.operation_id or req.client_message_id
    if send_operation and await container.sessions.lookup_command(session_id, send_operation):
        raise HTTPException(409, "操作 ID 已用于消息或设置命令")
    if send_operation and await container.sessions.lookup_generation(session_id, send_operation) is not None:
        raise HTTPException(409, {"code": "operation_conflict", "message": "操作 ID 已用于另一生成尝试"})
    if send_operation and await container.sessions.lookup_memory_job(session_id, send_operation) is not None:
        raise HTTPException(409, {"code": "operation_conflict", "message": "操作 ID 已用于记忆整理任务"})
    if send_operation in r.state.generation_operations.get("__mrp_pending_commands_v1__", {}):
        raise HTTPException(409, "操作 ID 已用于进行中的导演命令")
    if req.client_message_id is not None and not re.fullmatch(r"msg-[0-9a-f]{12,32}", req.client_message_id):
        raise HTTPException(400, "无效的消息 ID")
    if req.reply_mode == "free":
        from mrp.orchestrator.conversation_context import ConversationConflict
        from mrp.orchestrator.player_control import PlayerSwitchConflict
        try:
            return await container.conversation_runs.send_free(r, req)
        except (ConversationConflict, PlayerSwitchConflict) as exc:
            raise HTTPException(409, str(exc)) from exc
    from mrp.orchestrator.turn_runs import TurnRunConflict, TurnCheckpointFailure
    from mrp.orchestrator.conversation_context import ConversationConflict
    from mrp.orchestrator.player_control import PlayerSwitchConflict
    try:
        return await container.turn_runs.send(r, req)
    except (TurnRunConflict, ConversationConflict, PlayerSwitchConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except TurnCheckpointFailure as exc:
        raise HTTPException(503, str(exc)) from exc


class ResumeTurnReq(BaseModel):
    expected_branch_revision: int | None = Field(None, ge=0)
    expected_player_identity_id: str | None = None


@router.post("/api/v1/sessions/{session_id}/turn-runs/{operation_id}/resume")
async def resume_turn(session_id: str, operation_id: str, req: ResumeTurnReq,
                      container: AppContainer = Depends(get_container)):
    from mrp.orchestrator.turn_runs import TurnRunConflict, TurnCheckpointFailure
    runner = await runner_or_404(container, session_id)
    try:
        return await container.turn_runs.resume(runner, operation_id,
            expected_branch_revision=req.expected_branch_revision,
            expected_player_identity_id=req.expected_player_identity_id)
    except TurnRunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except TurnCheckpointFailure as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/v1/sessions/{session_id}/turn-runs/{operation_id}/stop")
async def stop_turn(session_id: str, operation_id: str,
                    container: AppContainer = Depends(get_container)):
    from mrp.orchestrator.turn_runs import TurnRunConflict, TurnCheckpointFailure
    runner = await runner_or_404(container, session_id)
    try:
        return await container.turn_runs.stop(runner, operation_id)
    except TurnRunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except TurnCheckpointFailure as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/v1/sessions/{session_id}/messages/{message_id}/swipe")
async def swipe_message(
    session_id: str, message_id: str, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None,
):
    r = await runner_or_404(container, session_id)
    async def mutate():
        require_current_control(r, message_id)
        target = message_or_404(r, message_id)
        response = await _run_regeneration(container, r, "one", message_id, RegenerationRequest(
            operation_id=command_id(operation_header) or new_id("op"), expected_branch_revision=r.state.meta.branch_revision,
            expected_fingerprint=target.fingerprint, expected_player_identity_id=r.state.meta.player_identity_id), defer_commit=True)
        return next(message for message in response["messages"] if message["id"] == message_id)
    return await execute_command(container, r, f"message.swipe:{message_id}", {}, mutate,
        operation_id=command_id(operation_header), memory=True, response=response, generation=True)


async def _run_regeneration(container, runner, action, message_id, req, *, variant_index=None,
                            baseline_transform=None, protect_extra=None, defer_commit=False, response=None):
    async def generate():
        result = await container.story_generation.regenerate(runner, action, message_id, req,
            variant_index=variant_index, baseline_transform=baseline_transform, protect_extra=protect_extra)
        return {**result, "messages": [project_message(message) for message in result.get("messages", [])]}
    try:
        if defer_commit:
            return await generate()
        # Frozen legacy operations remain replayable without adding a revision.
        if (req.operation_id in runner.state.generation_operations and
                await container.sessions.lookup_command(runner.state.meta.id, req.operation_id) is None):
            result = await generate()
            if response is not None:
                response.headers["X-Command-ID"] = req.operation_id
                response.headers["X-Branch-Revision"] = str(result.get("branch_revision", runner.state.meta.branch_revision))
            return result
        return await execute_command(container, runner, f"generation.{action}:{message_id}",
            {**command_payload(req), "variant_index": variant_index}, generate,
            operation_id=req.operation_id, memory=True, response=response,
            expected_revision=req.expected_branch_revision, generation=action in {"one", "dependents"})
    except RegenerationConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except RegenerationFailure as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/api/v1/sessions/{session_id}/messages/{message_id}/regenerate-one")
async def regenerate_one(session_id: str, message_id: str, req: RegenerationRequest,
                         container: AppContainer = Depends(get_container),
                         operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None):
    command_id(operation_header, req.operation_id)
    runner = await runner_or_404(container, session_id)
    return await _run_regeneration(container, runner, "one", message_id, req, response=response)


@router.post("/api/v1/sessions/{session_id}/messages/{message_id}/regenerate-dependents")
async def regenerate_dependents(session_id: str, message_id: str, req: RegenerationRequest,
                                container: AppContainer = Depends(get_container),
                                operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None):
    command_id(operation_header, req.operation_id)
    runner = await runner_or_404(container, session_id)
    return await _run_regeneration(container, runner, "dependents", message_id, req, response=response)


@router.post("/api/v1/sessions/{session_id}/messages/{message_id}/accept-dependencies")
async def accept_dependencies(session_id: str, message_id: str, req: RegenerationRequest,
                              container: AppContainer = Depends(get_container),
                              operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None):
    command_id(operation_header, req.operation_id)
    runner = await runner_or_404(container, session_id)
    return await _run_regeneration(container, runner, "accept", message_id, req, response=response)


@router.patch("/api/v1/sessions/{session_id}/messages/{message_id}")
async def patch_message(
    session_id: str, message_id: str, req: PatchMessageReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    """Edit or select a candidate with a durable optional command identity."""
    r = await runner_or_404(container, session_id)
    operation = command_id(operation_header, req.operation_id)
    from mrp.application.branch_commit import BranchCommitConflict
    async def mutate():
        if req.content is not None and req.active_variant is not None:
            raise HTTPException(400, "content 与 active_variant 只能传一个")
        if req.content is None and req.active_variant is None:
            raise HTTPException(400, "必须提供 content 或 active_variant")
        target = message_or_404(r, message_id)
        if req.content is not None:
            msg = await r.message_ops.edit_message_locked(message_id, req.content,
                expected_fingerprint=req.expected_fingerprint)
            if msg is None:
                raise HTTPException(400, "消息不可编辑（不存在/pending/内容为空）")
            await r._emit("message.updated", {"message": msg.model_dump(mode="json")})
            return project_message(msg)
        if not 0 <= req.active_variant < len(target.variants):
            raise HTTPException(400, "候选切换失败（无候选/索引越界）")
        await _run_regeneration(container, r, "switch", message_id, RegenerationRequest(
            operation_id=operation or new_id("op"),
            expected_branch_revision=req.expected_branch_revision if req.expected_branch_revision is not None else r.state.meta.branch_revision,
            expected_fingerprint=req.expected_fingerprint or target.fingerprint,
            expected_player_identity_id=(req.expected_player_identity_id
                if "expected_player_identity_id" in req.model_fields_set else r.state.meta.player_identity_id)),
            variant_index=req.active_variant, defer_commit=True)
        return project_message(message_or_404(r, message_id))
    try:
        return await execute_command(container, r, "message.patch:" + message_id,
            command_payload(req), mutate, operation_id=operation,
            expected_revision=req.expected_branch_revision, memory=True, response=response)
    except (MessageEditConflict, BranchCommitConflict) as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "无法确认保存结果，请复用同一操作 ID 重试") from exc


@router.patch("/api/v1/sessions/{session_id}/messages/{message_id}/input-group")
async def patch_input_group(
    session_id: str, message_id: str, req: PatchInputGroupReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    """Atomically edit every visible segment and persist the original result."""
    r = await runner_or_404(container, session_id)
    from mrp.application.branch_commit import BranchCommitConflict
    async def mutate():
        updated = await r.message_ops.edit_input_group_locked(message_id,
            [(part.message_id, part.expected_fingerprint, part.content) for part in req.parts],
            expected_branch_revision=req.expected_branch_revision)
        if not updated:
            raise HTTPException(400, "逻辑输入不存在或不可编辑")
        for msg in updated:
            await r._emit("message.updated", {"message": msg.model_dump(mode="json")})
        return {"messages": [project_message(msg) for msg in updated],
                "branch_revision": r.state.meta.branch_revision}
    try:
        return await execute_command(container, r, "message.input-group:" + message_id,
            command_payload(req), mutate, operation_id=command_id(operation_header),
            expected_revision=req.expected_branch_revision, memory=True, response=response)
    except (MessageEditConflict, BranchCommitConflict) as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "无法确认保存结果，请复用同一操作 ID 重试") from exc


@router.delete("/api/v1/sessions/{session_id}/messages/{message_id}")
async def delete_message(
    session_id: str, message_id: str, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    expected_branch_revision: int | None = Query(None, ge=0),
    response: Response = None,
):
    """Delete once; retries replay success even after the message disappears."""
    r = await runner_or_404(container, session_id)
    from mrp.application.branch_commit import BranchCommitConflict
    # Direct Python callers retain the old three-argument API.
    revision = expected_branch_revision if isinstance(expected_branch_revision, int) else None
    async def mutate():
        message_or_404(r, message_id)
        if await container.sessions.has_child_at(session_id, message_id):
            raise HTTPException(409, "该消息是子世界线的起点，不能删除")
        if not await r.delete_message(message_id):
            raise HTTPException(400, "消息不可删除（未完成或被剧情事件、固定信息引用）")
        return {"ok": True}
    try:
        return await execute_command(container, r, "message.delete:" + message_id,
            {"expected_branch_revision": revision}, mutate,
            operation_id=command_id(operation_header), expected_revision=revision, memory=True, response=response)
    except BranchCommitConflict as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc


@router.post("/api/v1/sessions/{session_id}/messages/{message_id}/regenerate")
async def regenerate_message(
    session_id: str, message_id: str, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None,
):
    """R45 重跑最后一轮：编辑"我的消息"后，基于改后文本重新生成角色回复。

    仅限最后一条玩家消息；旧回复被物理删除，失败自动回滚（502 返回时可读错误）。
    """
    r = await runner_or_404(container, session_id)
    async def mutate():
        require_current_control(r, message_id)
        target_index = next(i for i, item in enumerate(r.state.messages) if item.id == message_id)
        tail_ids = {item.id for item in r.state.messages[target_index + 1:]}
        if tail_ids:
            children = await container.sessions.list_summaries()
            if any(row.parent_branch_id == session_id and row.fork_message_id in tail_ids
                   for row in children):
                raise HTTPException(409, "本轮回复已被子世界线引用，不能重生成")
        try:
            msgs = await r.regenerate_turn(message_id)
            if msgs is None:
                raise HTTPException(400, "不可重新生成（仅最后一轮我的消息）")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, "生成或保存失败，已恢复原回复，请重试") from exc
        errors = list(r.runtime.turn_errors)
        return {
            "messages": [m.model_dump(mode="json") for m in msgs],
            "errors": errors,
        }
    return await execute_command(container, r, f"message.regenerate-turn:{message_id}", {}, mutate,
        operation_id=command_id(operation_header), memory=True, response=response, generation=True)


@router.post("/api/v1/sessions/{session_id}/messages/{message_id}/hygiene/recheck")
async def recheck_hygiene(
    session_id: str, message_id: str, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None,
):
    """R34「可再试」：对消息当前内容重跑输出卫生校验并回写角标。"""
    r = await runner_or_404(container, session_id)
    async def mutate():
        message_or_404(r, message_id)
        msg = await r.recheck_hygiene(message_id)
        if msg is None:
            raise HTTPException(400, "消息不可校验（玩家消息/pending/未启用校验）")
        return msg.model_dump(mode="json")
    return await execute_command(container, r, f"message.hygiene:{message_id}", {}, mutate,
        operation_id=command_id(operation_header), memory=True, response=response, generation=True)


@router.post("/api/v1/sessions/{session_id}/messages/{message_id}/continue")
async def continue_message(
    session_id: str, message_id: str, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None,
):
    """R37.3 续写：最后一条角色消息自然接续（拼接语义，variants 可切回撤销）。"""
    r = await runner_or_404(container, session_id)
    async def mutate():
        require_current_control(r, message_id)
        msg = await r.continue_message(message_id)
        if msg is None:
            raise HTTPException(400, "消息不可续写（玩家/导演消息/pending/非最后一条）")
        return msg.model_dump(mode="json")
    return await execute_command(container, r, f"message.continue:{message_id}", {}, mutate,
        operation_id=command_id(operation_header), memory=True, response=response, generation=True)


def require_current_control(r, message_id):
    from mrp.shared.player_identity import before_control_boundary
    message = message_or_404(r, message_id)
    if before_control_boundary(r.state, message):
        raise HTTPException(409, "已切换控制角色，切换前的消息不能重新生成、新增候选或续写；仍可编辑和选择已有候选")
