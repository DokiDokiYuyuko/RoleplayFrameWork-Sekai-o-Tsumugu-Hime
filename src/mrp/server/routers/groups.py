"""Scene-local group actors: source drafts, lifecycle and one-message replies."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from mrp.server.container import AppContainer
from mrp.server.deps import get_container, runner_or_404
from mrp.orchestrator.scene_frame import recent_public_scene_context
from mrp.shared.models import GroupActor, Message, TokenUsage, utcnow
from mrp.shared.prompt import estimate_tokens

from mrp.server.command_ids import command_id, command_payload, execute_command
from mrp.contracts.story import project_message

router = APIRouter()
MAX_ACTIVE_GROUPS = 8


class DraftReq(BaseModel):
    archive_id: str
    archive_revision: int = Field(ge=1)


class GroupFields(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    count: int | None = Field(default=None, ge=0, le=1000000)
    public_brief: str = Field("", max_length=4000)
    current_state: str = Field("", max_length=2000)
    director_note: str = Field("", max_length=4000)
    participation: Literal["on_cue", "occasional"] = "on_cue"


class CreateGroupReq(GroupFields):
    expected_scene_id: str
    expected_branch_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128)
    source_archive_id: str | None = None
    source_archive_revision: int | None = Field(default=None, ge=1)
    source_snapshot: str = Field("", max_length=8000)


class PatchGroupReq(BaseModel):
    expected_branch_revision: int = Field(ge=0)
    label: str | None = Field(None, min_length=1, max_length=120)
    aliases: list[str] | None = Field(None, max_length=30)
    count: int | None = Field(None, ge=0, le=1000000)
    public_brief: str | None = Field(None, max_length=4000)
    current_state: str | None = Field(None, max_length=2000)
    director_note: str | None = Field(None, max_length=4000)
    participation: Literal["on_cue", "occasional"] | None = None


class ReplyGroupReq(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)


def _current_scene(runner):
    scene = runner.active_scene()
    if scene is None:
        raise HTTPException(409, "当前故事没有活动场景")
    return scene


def _source_record(container: AppContainer, runner, archive_id: str, revision: int | None = None):
    world_id = runner.state.meta.source_world_id
    world = container.worlds.get(world_id) if world_id else None
    if world is None:
        raise HTTPException(409, "当前故事没有可用的来源世界，请改用手动填写")
    record = next((item for item in world.archive_records if item.id == archive_id), None)
    if record is None or record.kind != "biology":
        raise HTTPException(404, "生物设定不存在或已删除")
    if record.visibility != "public":
        raise HTTPException(403, "私密生物设定不能用于群体草稿")
    if revision is not None and record.revision != revision:
        raise HTTPException(409, "生物设定已更新，请重新预览")
    return world, record


def _event_content(action: str, group: GroupActor) -> str:
    count = "数量未定" if group.count is None else f"×{group.count}"
    lines = [f"[群体{action}]", f"{group.label} {count}"]
    if group.public_brief:
        lines.append(f"可观察信息：{group.public_brief[:500]}")
    if group.current_state:
        lines.append(f"当前状态：{group.current_state[:300]}")
    return "\n".join(lines)


def _append_event(runner, group: GroupActor, action: str):
    scene = runner.active_scene()
    message = Message(
        session_id=runner.state.meta.id,
        seq=runner.state.next_seq(),
        turn=runner.state.current_turn(),
        actor="director",
        content=_event_content(action, group),
        kind="system_event",
        visible_to="all",
        scene_id=scene.id if scene else group.scene_id,
    )
    runner._append_message(message)
    return message


@router.get("/api/v1/sessions/{session_id}/groups")
async def list_groups(session_id: str, container: AppContainer = Depends(get_container)):
    runner = await runner_or_404(container, session_id)
    return [group.model_dump(mode="json") for group in runner.public_head.groups]


@router.get("/api/v1/sessions/{session_id}/groups/sources")
async def list_group_sources(session_id: str, container: AppContainer = Depends(get_container)):
    runner = await runner_or_404(container, session_id)
    world_id = runner.state.meta.source_world_id
    world = container.worlds.get(world_id) if world_id else None
    if world is None:
        return {"world_id": None, "world_title": None, "world_revision": None, "sources": []}
    sources = []
    for record in world.archive_records:
        if record.kind != "biology" or record.visibility != "public":
            continue
        excerpt = runner.group_responder.biology_excerpt(record)
        # Keep a predictable preview/request budget even for long archive fields.
        while estimate_tokens(excerpt) > 1100:
            excerpt = excerpt[: max(400, int(len(excerpt) * 0.86))]
        sources.append({
            "id": record.id, "title": record.title, "aliases": record.aliases,
            "summary": record.summary, "revision": record.revision,
            "world_revision": world.revision, "excerpt": excerpt,
            "estimated_tokens": estimate_tokens(excerpt),
        })
    return {"world_id": world.id, "world_title": world.title, "world_revision": world.revision, "sources": sources}


@router.post("/api/v1/sessions/{session_id}/groups/draft")
async def draft_group(session_id: str, req: DraftReq, container: AppContainer = Depends(get_container)):
    runner = await runner_or_404(container, session_id)
    _current_scene(runner)
    world, record = _source_record(container, runner, req.archive_id, req.archive_revision)
    scene = runner.active_scene()
    draft, usage, excerpt = await runner.group_responder.draft(
        record, scene.title, scene.description,
        recent_public_scene_context(runner.state, runner.state.current_turn(), limit=10),
    )
    while estimate_tokens(excerpt) > 1100:
        excerpt = excerpt[: max(400, int(len(excerpt) * 0.86))]
    if usage.get("input_tokens") or usage.get("output_tokens"):
        runner.turns.track_purpose_cost("group_draft", TokenUsage(**usage))
    return {
        "draft": draft,
        "source": {"kind": "biology", "world_id": world.id, "world_title": world.title,
                   "archive_id": record.id, "archive_title": record.title,
                   "archive_revision": record.revision},
        "source_snapshot": excerpt,
        "estimated_tokens": estimate_tokens(excerpt),
        "usage": usage,
    }


@router.post("/api/v1/sessions/{session_id}/groups")
async def create_group(session_id: str, req: CreateGroupReq, container: AppContainer = Depends(get_container), operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None):
    runner = await runner_or_404(container, session_id)
    request_hash = hashlib.sha256(json.dumps(
        req.model_dump(mode="json", exclude={"idempotency_key"}),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    async def mutate():
        previous = next((item for item in runner.state.groups if item.creation_key == req.idempotency_key), None)
        if previous is not None:
            if previous.creation_request_hash != request_hash:
                raise HTTPException(409, "幂等键已用于另一条群体记录")
            return previous.model_dump(mode="json")
        scene = _current_scene(runner)
        if scene.id != req.expected_scene_id:
            raise HTTPException(409, "场景已切换，请刷新群体表单")
        if runner.state.meta.branch_revision != req.expected_branch_revision:
            raise HTTPException(409, "世界线已更新，请刷新群体表单")
        if not req.label.strip():
            raise HTTPException(400, "群体名称不能为空")
        active = [item for item in runner.state.groups if item.status == "active" and item.scene_id == scene.id]
        if len(active) >= MAX_ACTIVE_GROUPS:
            raise HTTPException(409, f"当前场景最多容纳 {MAX_ACTIVE_GROUPS} 个活跃群体")
        source: dict[str, Any] = {"kind": "manual"}
        source_snapshot = req.public_brief.strip()
        if req.source_archive_id:
            if req.source_archive_revision is None:
                raise HTTPException(400, "缺少生物设定修订号")
            world, record = _source_record(container, runner, req.source_archive_id, req.source_archive_revision)
            source = {"kind": "biology", "world_id": world.id, "world_revision": world.revision,
                      "archive_id": record.id, "archive_title": record.title,
                      "archive_revision": record.revision}
            authoritative_excerpt = runner.group_responder.biology_excerpt(record)
            requested_excerpt = req.source_snapshot.strip()
            authoritative_lines = [line.strip() for line in authoritative_excerpt.splitlines() if line.strip()]
            if requested_excerpt and any(
                not any(line.strip() in source_line for source_line in authoritative_lines)
                for line in requested_excerpt.splitlines() if line.strip()
            ):
                raise HTTPException(400, "来源摘录必须来自当前公开生物设定")
            source_snapshot = requested_excerpt or authoritative_excerpt
            while estimate_tokens(source_snapshot) > 1100:
                source_snapshot = source_snapshot[: max(400, int(len(source_snapshot) * 0.86))]
        group = GroupActor(
            label=req.label.strip(), aliases=[item.strip() for item in req.aliases if item.strip()],
            count=req.count, scene_id=scene.id, joined_seq=runner.state.next_seq(), status="active",
            public_brief=req.public_brief.strip(), current_state=req.current_state.strip(),
            director_note=req.director_note.strip(), participation=req.participation,
            source=source, source_snapshot=source_snapshot, creation_key=req.idempotency_key,
            creation_request_hash=request_hash,
        )
        if group.count == 0:
            raise HTTPException(400, "群体人数为 0 时不能加入场景")
        runner.state.groups.append(group)
        scene.group_ids.append(group.id)
        event = _append_event(runner, group, "加入场景")
        await runner._emit("message.final", {"message": event.model_dump(mode="json")})
        return group.model_dump(mode="json")
    return await execute_command(container, runner, f"group.create", command_payload(req), mutate,
        operation_id=command_id(operation_header), response=response)


@router.patch("/api/v1/sessions/{session_id}/groups/{group_id}")
async def patch_group(session_id: str, group_id: str, req: PatchGroupReq, container: AppContainer = Depends(get_container), operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None):
    runner = await runner_or_404(container, session_id)
    async def mutate():
        group = next((item for item in runner.state.groups if item.id == group_id), None)
        if group is None:
            raise HTTPException(404, "群体不存在")
        if runner.state.meta.branch_revision != req.expected_branch_revision:
            raise HTTPException(409, "世界线已更新，请刷新后重试")
        changes = req.model_dump(exclude={"expected_branch_revision"}, exclude_unset=True)
        if not changes:
            raise HTTPException(400, "没有需要保存的修改")
        if "label" in changes:
            changes["label"] = changes["label"].strip()
            if not changes["label"]:
                raise HTTPException(400, "群体名称不能为空")
        if "aliases" in changes:
            changes["aliases"] = [item.strip() for item in changes["aliases"] if item.strip()]
        for key, value in changes.items():
            setattr(group, key, value)
        group.updated_at = utcnow()
        action = "已离场" if group.count == 0 and group.status == "active" else "状态更新"
        if action == "已离场":
            group.status = "left"
        scene = next((item for item in runner.state.scenes if item.id == group.scene_id), None)
        if group.status == "left" and scene is not None:
            scene.group_ids = [item for item in scene.group_ids if item != group.id]
        event = _append_event(runner, group, action)
        await runner._emit("message.final", {"message": event.model_dump(mode="json")})
        return group.model_dump(mode="json")
    return await execute_command(container, runner, f"group.patch:{group_id}", command_payload(req), mutate,
        operation_id=command_id(operation_header), response=response)


@router.post("/api/v1/sessions/{session_id}/groups/{group_id}/leave")
async def leave_group(session_id: str, group_id: str, expected_branch_revision: int, container: AppContainer = Depends(get_container), operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None):
    runner = await runner_or_404(container, session_id)
    async def mutate():
        group = next((item for item in runner.state.groups if item.id == group_id), None)
        if group is None:
            raise HTTPException(404, "群体不存在")
        if runner.state.meta.branch_revision != expected_branch_revision:
            raise HTTPException(409, "世界线已更新，请刷新后重试")
        if group.status != "left":
            group.status = "left"
            group.updated_at = utcnow()
            scene = next((item for item in runner.state.scenes if item.id == group.scene_id), None)
            if scene is not None:
                scene.group_ids = [item for item in scene.group_ids if item != group.id]
            event = _append_event(runner, group, "离开场景")
            await runner._emit("message.final", {"message": event.model_dump(mode="json")})
            return group.model_dump(mode="json")
        return group.model_dump(mode="json")
    return await execute_command(container, runner, f"group.leave:{group_id}", {"expected_branch_revision": expected_branch_revision}, mutate,
        operation_id=command_id(operation_header), response=response)


@router.post("/api/v1/sessions/{session_id}/groups/{group_id}/reply")
async def reply_as_group(session_id: str, group_id: str, req: ReplyGroupReq, container: AppContainer = Depends(get_container), operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None):
    runner = await runner_or_404(container, session_id)
    async def mutate():
        existing = next(
            (
                message for message in reversed(runner.state.messages)
                if message.actor == group_id
                and message.idempotency_key == req.idempotency_key
                and message.status == "final"
            ),
            None,
        )
        if existing is not None:
            return existing.model_dump(mode="json")
        group = next((item for item in runner.state.groups if item.id == group_id), None)
        if group is None:
            raise HTTPException(404, "群体不存在")
        if group.status != "active" or group.id not in (_current_scene(runner).group_ids):
            raise HTTPException(409, "该群体已离开当前场景")
        try:
            return await container.story_generation.group_reply(runner, group, req.idempotency_key)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, "群体回应生成失败，请稍后重试") from exc

    return await execute_command(container, runner, f"group.reply:{group_id}", command_payload(req), mutate,
        operation_id=command_id(operation_header), response=response, generation=True)


@router.post("/api/v1/sessions/{session_id}/groups/{group_id}/messages/{message_id}/swipe")
async def swipe_group_message(session_id: str, group_id: str, message_id: str, container: AppContainer = Depends(get_container), operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None):
    runner = await runner_or_404(container, session_id)
    async def mutate():
        message = next((item for item in runner.state.messages if item.id == message_id and item.actor == group_id), None)
        if message is None:
            raise HTTPException(404, "群体消息不存在")
        from mrp.orchestrator.message_regeneration import RegenerationRequest
        from mrp.server.routers.messages import _run_regeneration
        from mrp.shared.models import new_id
        response = await _run_regeneration(container, runner, "one", message_id, RegenerationRequest(
            operation_id=command_id(operation_header) or new_id("op"), expected_branch_revision=runner.state.meta.branch_revision,
            expected_fingerprint=message.fingerprint, expected_player_identity_id=runner.state.meta.player_identity_id), defer_commit=True)
        return next(item for item in response["messages"] if item["id"] == message_id)
    return await execute_command(container, runner, f"group.swipe:{group_id}:{message_id}", {}, mutate,
        operation_id=command_id(operation_header), memory=True, response=response, generation=True)
