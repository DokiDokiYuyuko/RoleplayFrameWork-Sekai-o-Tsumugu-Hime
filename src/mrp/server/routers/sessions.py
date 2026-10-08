"""会话路由：CRUD / 设置 / 存档 / 场景 / 辅助 / 导演确认 / 事件流 / 检查器。

纯搬迁自 app.py（URL/方法/响应/状态码不变），仅按 W1 计划做了标注的行为修复。
"""
from __future__ import annotations

import asyncio

from fastapi import Query, APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field

from mrp.orchestrator.writing_assistant import WritingRequest
from mrp.server.container import AppContainer
from mrp.server.deps import get_container, runner_or_404, state_dict, message_or_404
from mrp.server.sse import sse_stream
from mrp.orchestrator.inspections import build_inspection
from mrp.orchestrator.context_plan import plan_context
from mrp.shared.models import TurnContext
from mrp.shared.player_identity import identity_source
from mrp.server.command_ids import command_id, command_payload, conflict_detail, execute_command, command_headers
from mrp.shared.actor_labels import build_reply_frame
from mrp.shared.prompt import persona_from_card, player_name_for_context
from mrp.contracts.commands import LegacyRestoreResult

router = APIRouter()


# ---------- 请求模型 ----------


class CreateSessionReq(BaseModel):
    title: str = Field("", max_length=200)  # B17：合理上限
    character_ids: list[str] = Field(max_length=20)
    player_persona: str = Field("", max_length=20000)
    player_character_id: str | None = None
    reply_max_tokens: int | None = Field(None, ge=0, le=32768)
    lorebook_ids: list[str] = Field(default_factory=list, max_length=20)
    world_id: str | None = None
    opening_scene: str = ""
    greeting_choices: dict[str, int] = Field(default_factory=dict, max_length=20)


class PatchSessionReq(BaseModel):
    expected_branch_revision: int | None = Field(None, ge=0)
    response_style_id: str | None = Field(None, max_length=80)
    response_style_overrides: dict[str, str | None] | None = None
    options_enabled: bool | None = None
    options_style: str | None = None  # action / dialogue / mixed
    options_direct_send: bool | None = None  # M12-R41：点击候选直发（默认 False=填入待确认）
    title: str | None = Field(None, max_length=200)
    player_persona: str | None = Field(None, max_length=20000)
    player_character_id: str | None = None
    reply_max_tokens: int | None = Field(None, ge=0, le=32768)
    lorebook_ids: list[str] | None = Field(None, max_length=20)  # 会话世界书绑定（对齐酒馆：配置跟随会话，随时可改）
    streaming_enabled: bool | None = None  # R33.4 流式输出开关
    hygiene_enabled: bool | None = None  # R34.2 输出卫生校验开关
    director_mode: str | None = None  # R35.3 auto / confirm / rules
    narrative_pov: str | None = None  # R37.1 free / second / third
    narrative_density: str | None = None  # R37.2 dialogue / balanced / atmosphere
    short_input_padding: bool | None = None  # R37.4 垫场开关
    proactive_turn_limit: int | None = None  # R38.2 每回合全局主动发言上限（0-3）
    prompt_preset_id: str | None = None


class SceneSwitchReq(BaseModel):
    title: str = Field(max_length=200)
    description: str = Field("", max_length=5000)
    member_ids: list[str] | None = None
    first_speaker_ids: list[str] | None = None


class SceneImageReq(BaseModel):
    builtin_image_id: str | None = Field(..., max_length=80)
    expected_branch_revision: int = Field(ge=0)


class CharacterActionReq(BaseModel):
    action: str  # mute/unmute/force/present/absent/interject/uninterject/followup/unfollowup
    character_id: str = ""  # continue(force) 时必填


class AddParticipantReq(BaseModel):
    character_id: str = Field(min_length=1, max_length=200)
    join_current_scene: bool = True
    entry_brief: str = Field("", max_length=4000)




class DraftAssistReq(WritingRequest):
    """Independent writing task; optional fields preserve legacy clients."""


class SwitchPlayerReq(BaseModel):
    target_character_id: str = Field(min_length=1, max_length=200)
    previous_disposition: str = Field(pattern="^(npc|leave)$")
    expected_branch_revision: int = Field(ge=0)
    expected_player_identity_id: str
    idempotency_key: str = Field(min_length=1, max_length=100)
    entry_brief: str = ""


@router.post("/api/v1/sessions/{session_id}/player/switch")
async def switch_player_role(session_id: str, req: SwitchPlayerReq, container: AppContainer = Depends(get_container)):
    from mrp.orchestrator.player_control import switch_player, PlayerSwitchConflict
    r = await runner_or_404(container, session_id)
    def resolve(character_id):
        character = container.characters.get(character_id)
        if character is not None:
            character = character.model_copy(deep=True)
            container._apply_global_llm_settings(character)
        return character
    original_state = r.state.model_copy(deep=True)
    async def persist_controlled(runner):
        try:
            for character in runner.state.characters:
                container._apply_global_llm_settings(character)
            await container.persist_session(runner)
        except BaseException:
            authoritative = await container.sessions.load_state(session_id)
            if (authoritative is not None and
                    authoritative.meta.branch_revision == original_state.meta.branch_revision + 1 and
                    authoritative.meta.player_identity_id == runner.state.meta.player_identity_id):
                await container.sessions.restore_state(original_state)
            runner._committed_state = original_state.model_copy(deep=True)
            raise
    try:
        await switch_player(r, target_id=req.target_character_id, disposition=req.previous_disposition,
            expected_revision=req.expected_branch_revision, expected_identity=req.expected_player_identity_id,
            idempotency_key=req.idempotency_key, entry_brief=req.entry_brief,
            resolve_character=resolve, data_root=container.data_root, persist=persist_controlled)
    except PlayerSwitchConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "保存切换失败，已恢复原控制角色，请稍后重试") from exc
    snapshot = state_dict(r)
    snapshot["event_cursor"] = container.bus.current_cursor(session_id)
    return snapshot


@router.get("/api/v1/sessions/{session_id}/player/avatar/{identity_id}")
async def player_avatar(session_id: str, identity_id: str, container: AppContainer = Depends(get_container)):
    from mrp.storage.story_media import media_path
    r = await runner_or_404(container, session_id)
    identity = next((x for x in r.state.player_identities if x.id == identity_id), None)
    if identity is None or not identity.avatar_ref:
        raise HTTPException(404, "此身份没有头像")
    path = media_path(container.data_root, identity.avatar_ref)
    if not path.is_file():
        raise HTTPException(404, "头像资源缺失")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})


# ---------- 会话 CRUD ----------


@router.post("/api/v1/sessions")
async def create_session(req: CreateSessionReq, container: AppContainer = Depends(get_container),
                         operation_header: str | None = Header(None, alias='X-Operation-ID'),
                         response: Response = None):
    from mrp.server.creation_commands import execute_creation
    made = []
    async def create():
        r = await container.create_session(
            req.title, req.character_ids, req.player_persona, req.lorebook_ids,
            req.world_id, req.player_character_id, req.reply_max_tokens,
            opening_scene=req.opening_scene, greeting_choices=req.greeting_choices, register=False)
        made.append(r)
        await container.sessions.claim_creation_entity('branch', r.state.meta.id)
        if not req.opening_scene.strip():
            await r.open_round()
        await container.persist_session(r)
        return state_dict(r)
    try:
        result = await execute_creation(container, 'story.create', command_payload(req),
            command_id(operation_header), create, response=response)
        if made:
            await container._register_runner(made[0])
        return result
    except ValueError as e:
        raise HTTPException(409 if getattr(e, 'command_conflict', False) else 400, conflict_detail(e)) from e
    finally:
        if made and container.runners.get(made[0].state.meta.id) is not made[0]:
            await made[0].aclose()


@router.get("/api/v1/sessions")
async def list_sessions(container: AppContainer = Depends(get_container)):
    # B3：只读摘要，不再为每个会话建 runner（原实现把全部会话灌进 runners → 内存/句柄放大）
    return await container.session_summaries()


@router.get("/api/v1/sessions/{session_id}")
async def get_session(session_id: str, container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    container.spawn_warm(r.state.characters)  # R28.2：打开会话即预热（幂等）
    snapshot = state_dict(r)
    snapshot["event_cursor"] = container.bus.current_cursor(session_id)
    return snapshot


@router.delete("/api/v1/sessions/{session_id}")
async def delete_session(session_id:str,container:AppContainer=Depends(get_container),
    membership_revision:str|None=Query(None),operation_header:str|None=Header(None,alias='X-Operation-ID'),response:Response=None):
    from mrp.server.lifecycle_commands import execute_lifecycle
    story_id=await asyncio.to_thread(container.story_lifecycle.repo.story_for,session_id)
    action='delete' if story_id==session_id else 'branch_delete'
    return await execute_lifecycle(container,action,story_id,{'branch_id':None if action=='delete' else session_id,
        'membership_revision':membership_revision},command_id(operation_header),response)


@router.patch("/api/v1/sessions/{session_id}")
async def patch_session(
    session_id: str, req: PatchSessionReq, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    """Stage settings and replay the original committed response on retry."""
    r = await runner_or_404(container, session_id)
    from mrp.application.branch_commit import BranchCommitConflict
    async def mutate():
        if (("player_character_id" in req.model_fields_set and req.player_character_id != r.state.meta.player_character_id)
            or (req.player_persona is not None and req.player_persona != r.state.meta.player_persona)):
            raise HTTPException(409, "请使用聊天输入区的切换角色功能更换玩家身份")
        style_ids = {s.id for s in container.settings.response_styles if s.enabled}
        if "response_style_id" in req.model_fields_set and req.response_style_id is not None and req.response_style_id not in style_ids:
            raise HTTPException(400, "回应风格不存在或已停用")
        if req.response_style_overrides is not None:
            actors = {c.id for c in r.state.characters} | {g.id for g in r.state.groups}
            if any(actor not in actors or (value is not None and value not in style_ids) for actor, value in req.response_style_overrides.items()):
                raise HTTPException(400, "角色覆盖包含无效角色或风格")
        # Validate every enum before touching state, so a rejected PATCH cannot
        # leave an unrecorded in-memory change for the next generated message.
        if req.options_style is not None and req.options_style not in ("action", "dialogue", "mixed"):
            raise HTTPException(400, "options_style 须为 action/dialogue/mixed")
        if req.director_mode is not None and req.director_mode not in ("auto", "confirm", "rules"):
            raise HTTPException(400, "director_mode 须为 auto/confirm/rules")
        if req.narrative_pov is not None and req.narrative_pov not in ("free", "second", "third"):
            raise HTTPException(400, "narrative_pov 须为 free/second/third")
        if req.narrative_density is not None and req.narrative_density not in ("dialogue", "balanced", "atmosphere"):
            raise HTTPException(400, "narrative_density 须为 dialogue/balanced/atmosphere")
        if req.options_enabled is not None:
            r.state.meta.options_enabled = req.options_enabled
        if req.options_style is not None:
            if req.options_style not in ("action", "dialogue", "mixed"):
                raise HTTPException(400, "options_style 须为 action/dialogue/mixed")
            r.state.meta.options_style = req.options_style
        if req.options_direct_send is not None:
            r.state.meta.options_direct_send = req.options_direct_send
        if req.title is not None:
            r.state.meta.title = req.title
        if "player_character_id" in req.model_fields_set:
            if req.player_character_id:
                player_character = container.characters.get(req.player_character_id)
                if player_character is None:
                    raise HTTPException(400, "所选玩家角色卡不存在")
                if req.player_character_id in r.state.meta.character_ids:
                    raise HTTPException(400, "玩家角色卡不能同时作为参与角色")
                if req.player_persona is None:
                    from mrp.shared.prompt import player_persona_from_card
                    r.state.meta.player_persona = player_persona_from_card(player_character.card)
            r.state.meta.player_character_id = req.player_character_id or None
        if req.player_persona is not None:
            r.state.meta.player_persona = req.player_persona
        if "reply_max_tokens" in req.model_fields_set:
            r.state.meta.reply_max_tokens = req.reply_max_tokens
        if req.lorebook_ids is not None:
            r.state.meta.lorebook_ids = req.lorebook_ids
            # 全局书复制为会话快照；预设带来的本地书 ID 则沿用当前会话中的副本。
            local_books = {book.id: book for book in r.state.lorebooks}
            selected_books = []
            for book_id in req.lorebook_ids:
                book = container.lorebooks.get(book_id) or local_books.get(book_id)
                if book is not None:
                    selected_books.append(book.model_copy(deep=True))
            r.state.lorebooks = selected_books
            # runner 热更新书绑定（新回合即生效，无需重开会话）
            r.lorebooks = selected_books
        if req.streaming_enabled is not None:
            r.state.meta.streaming_enabled = req.streaming_enabled
        if req.hygiene_enabled is not None:
            r.state.meta.hygiene_enabled = req.hygiene_enabled
        if req.director_mode is not None:
            if req.director_mode not in ("auto", "confirm", "rules"):
                raise HTTPException(400, "director_mode 须为 auto/confirm/rules")
            r.state.meta.director_mode = req.director_mode
        if req.narrative_pov is not None:
            if req.narrative_pov not in ("free", "second", "third"):
                raise HTTPException(400, "narrative_pov 须为 free/second/third")
            r.state.meta.narrative_pov = req.narrative_pov
        if req.narrative_density is not None:
            if req.narrative_density not in ("dialogue", "balanced", "atmosphere"):
                raise HTTPException(400, "narrative_density 须为 dialogue/balanced/atmosphere")
            r.state.meta.narrative_density = req.narrative_density
        if req.short_input_padding is not None:
            r.state.meta.short_input_padding = req.short_input_padding
        if req.proactive_turn_limit is not None:
            r.state.meta.proactive_turn_limit = max(0, min(3, req.proactive_turn_limit))
        if "prompt_preset_id" in req.model_fields_set:
            preset = container.prompt_presets.get(req.prompt_preset_id) if req.prompt_preset_id else None
            if req.prompt_preset_id and preset is None:
                raise HTTPException(404, "提示词方案不存在")
            r.state.meta.prompt_preset_id = req.prompt_preset_id
            r.state.meta.prompt_preset_snapshot = preset.model_dump(mode="json") if preset else None
        if "response_style_id" in req.model_fields_set:
            r.state.meta.response_style_id = req.response_style_id
        if req.response_style_overrides is not None:
            r.state.meta.response_style_overrides = req.response_style_overrides
        payload = {
            "options_enabled": r.state.meta.options_enabled,
            "options_style": r.state.meta.options_style,
            "options_direct_send": r.state.meta.options_direct_send,
            "title": r.state.meta.title,
            "player_persona": r.state.meta.player_persona,
            "player_character_id": r.state.meta.player_character_id,
            "reply_max_tokens": r.state.meta.reply_max_tokens,
            "lorebook_ids": r.state.meta.lorebook_ids,
            "streaming_enabled": r.state.meta.streaming_enabled,
            "hygiene_enabled": r.state.meta.hygiene_enabled,
            "director_mode": r.state.meta.director_mode,
            "narrative_pov": r.state.meta.narrative_pov,
            "response_style_id": r.state.meta.response_style_id,
            "response_style_overrides": r.state.meta.response_style_overrides,
            "narrative_density": r.state.meta.narrative_density,
            "short_input_padding": r.state.meta.short_input_padding,
            "proactive_turn_limit": r.state.meta.proactive_turn_limit,
            "prompt_preset_id": r.state.meta.prompt_preset_id,
        }
        payload["branch_revision"] = r.state.meta.branch_revision
        return payload
    try:
        return await execute_command(container, r, "session.patch", command_payload(req), mutate,
            operation_id=command_id(operation_header), expected_revision=req.expected_branch_revision, response=response)
    except BranchCommitConflict as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc


# ---------- 回合控制 ----------


@router.post("/api/v1/sessions/{session_id}/continue")
async def force_continue(
    session_id: str, req: CharacterActionReq, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    if req.action != "force":
        raise HTTPException(400, "continue 仅接受 force")
    r = await runner_or_404(container, session_id)
    async def mutate():
        msg = await r.force_turn(req.character_id)
        if msg is None:
            raise HTTPException(400, "角色不可发言（不在会话中/离席/静音）")
        return msg.model_dump(mode="json")
    return await execute_command(container, r, f"session.force", command_payload(req), mutate,
        operation_id=command_id(operation_header), response=response, generation=True)


# ---------- R35 场景演化 ----------


@router.post("/api/v1/sessions/{session_id}/scene/switch")
async def scene_switch(
    session_id: str, req: SceneSwitchReq, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    """R35.2 玩家手动切场景（trigger=player_scene，可指定带入场者）。"""
    r = await runner_or_404(container, session_id)
    async def mutate():
        created = await r.switch_scene_manual(
            req.title, req.description, req.member_ids, req.first_speaker_ids)
        if created is None:
            raise HTTPException(400, "场景切换失败（标题为空）")
        return {"messages": [m.model_dump(mode="json") for m in created]}
    return await execute_command(container, r, f"scene.switch", command_payload(req), mutate,
        operation_id=command_id(operation_header), response=response, generation=True)


@router.patch("/api/v1/sessions/{session_id}/scenes/{scene_id}/image")
async def patch_scene_image(
    session_id: str, scene_id: str, req: SceneImageReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    """Save explicit presentation artwork without changing scene text or membership."""
    from mrp.shared.scene_images import BUILTIN_SCENE_IMAGE_IDS
    runner = await runner_or_404(container, session_id)
    async def mutate():
        scene = next((item for item in runner.state.scenes if item.id == scene_id), None)
        if scene is None:
            raise HTTPException(404, "场景不存在")
        if runner.state.active_scene_id != scene_id:
            raise HTTPException(409, "场景已切换，请重新打开场景图库")
        if req.builtin_image_id is not None and req.builtin_image_id not in BUILTIN_SCENE_IMAGE_IDS:
            raise HTTPException(422, "场景图片不在内置图库中")
        scene.builtin_image_id = req.builtin_image_id
        return scene.model_dump(mode="json")
    return await execute_command(container, runner, f"scene.image:{scene_id}", command_payload(req), mutate,
        operation_id=command_id(operation_header), expected_revision=req.expected_branch_revision, response=response)


@router.get("/api/v1/sessions/{session_id}/scenes")
async def list_scenes(session_id: str, container: AppContainer = Depends(get_container)):
    """场景列表（含进行中状态；R36 查看器复用）。"""
    r = await runner_or_404(container, session_id)
    return {
        "active_scene_id": r.public_head.active_scene_id,
        "scenes": [s.model_dump(mode="json") for s in r.public_head.scenes],
    }


# ---------- M12-R41 辅助候选（手动触发） ----------


@router.post("/api/v1/sessions/{session_id}/assist/candidates")
async def assist_candidates(session_id: str, container: AppContainer = Depends(get_container)):
    """生成一批辅助候选（玩家点击触发；唯一入口）。

    上下文自选（冷启动点 → 开场/新场景候选；否则 → 回合接话候选）；
    每次调用都是一次全新生成。失败/未启用 → options 为空 + reason（恒 200，静默）。
    """
    r = await runner_or_404(container, session_id)
    return await r.generate_candidates()


@router.post("/api/v1/sessions/{session_id}/assist/draft")
async def assist_draft(
    session_id: str, req: DraftAssistReq, container: AppContainer = Depends(get_container)
):
    """Generate reviewable writing proposals without changing story messages."""
    r = await runner_or_404(container, session_id)
    try:
        from mrp.orchestrator.writing_assistant import WritingAssistant
        return await WritingAssistant(r).generate(req)
    except ValueError as exc:
        status = 409 if "已变化" in str(exc) or "已更新" in str(exc) else 400
        raise HTTPException(status, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc



# ---------- R35.3 导演确认档 ----------


@router.post("/api/v1/sessions/{session_id}/director/pending/confirm")
async def director_pending_confirm(
    session_id: str, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None,
):
    """R35.3 确认档：执行挂起的 LLM 决策。"""
    r = await runner_or_404(container, session_id)
    from mrp.orchestrator.turn_runs import TurnRunConflict, TurnCheckpointFailure
    from mrp.application.branch_commit import BranchCommitConflict
    metadata = {}
    try:
        result = await container.checkpoint_commands.resolve_director(r, True,
            operation_id=command_id(operation_header), response_meta=metadata)
    except BranchCommitConflict as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc
    except TurnRunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except TurnCheckpointFailure as exc:
        raise HTTPException(503, str(exc)) from exc
    if result is None:
        raise HTTPException(400, "无挂起的导演决策")
    command_headers(response, metadata)
    return result


@router.post("/api/v1/sessions/{session_id}/director/pending/reject")
async def director_pending_reject(
    session_id: str, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None,
):
    """R35.3 确认档：否决 LLM 决策 → v1 轮盘重算执行。"""
    r = await runner_or_404(container, session_id)
    from mrp.orchestrator.turn_runs import TurnRunConflict, TurnCheckpointFailure
    from mrp.application.branch_commit import BranchCommitConflict
    metadata = {}
    try:
        result = await container.checkpoint_commands.resolve_director(r, False,
            operation_id=command_id(operation_header), response_meta=metadata)
    except BranchCommitConflict as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc
    except TurnRunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except TurnCheckpointFailure as exc:
        raise HTTPException(503, str(exc)) from exc
    if result is None:
        raise HTTPException(400, "无挂起的导演决策")
    command_headers(response, metadata)
    return result


# ---------- 在场 / 静音 ----------


@router.post("/api/v1/sessions/{session_id}/participants")
async def add_participant(
    session_id: str,
    req: AddParticipantReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    r = await runner_or_404(container, session_id)
    async def mutate():
        asset = container.characters.get(req.character_id)
        if asset is None:
            raise HTTPException(404, "素材库中找不到这张角色卡")
        participant = asset.model_copy(deep=True)
        container._apply_global_llm_settings(participant)
        try:
            added = await r.add_participant(participant, join_current_scene=req.join_current_scene,
                entry_brief=req.entry_brief)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if added:
            await r._emit("session.roster.changed", {"character_id": participant.id})
        return {"added": added, "state": r.state.model_dump(mode="json", exclude={
            "generation_operations", "state_revisions", "material_blobs", "usage_records",
            "director_log", "turn_runs", "conversation_runs"})}
    return await execute_command(container, r, "participant.add", command_payload(req), mutate,
        operation_id=command_id(operation_header), response=response)


@router.post("/api/v1/sessions/{session_id}/characters/{character_id}")
async def character_action(
    session_id: str,
    character_id: str,
    req: CharacterActionReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias="X-Operation-ID"),
    response: Response = None,
):
    r = await runner_or_404(container, session_id)
    async def mutate():
        ch = r.state.character(character_id)
        if ch is None:
            raise HTTPException(404, f"角色不在会话中: {character_id}")
        ch = r.state.character(character_id)
        if req.action == "mute":
            r.set_muted(character_id, True)
        elif req.action == "unmute":
            r.set_muted(character_id, False)
        elif req.action == "present":
            await r.set_presence(character_id, True)
        elif req.action == "absent":
            await r.set_presence(character_id, False)
        elif req.action == "interject":  # R38.1：主动插话开关（会话级生效值）
            ch.interject_enabled = True
        elif req.action == "uninterject":
            ch.interject_enabled = False
        elif req.action == "followup":  # R38.2：接话开关
            ch.followup_enabled = True
        elif req.action == "unfollowup":
            ch.followup_enabled = False
        else:
            raise HTTPException(400, f"未知动作: {req.action}")
        return {"character_id": character_id, "action": req.action}
    return await execute_command(container, r, f"character.action:{character_id}", command_payload(req), mutate,
        operation_id=command_id(operation_header), response=response)


# ---------- 事件流 / 检查器 / 成本 ----------


@router.get("/api/v1/sessions/{session_id}/events")
async def session_events(
    session_id: str, request: Request, after: str | None = None,
    container: AppContainer = Depends(get_container),
):
    await runner_or_404(container, session_id)
    container.bus.current_cursor(session_id)
    cursor = request.headers.get("Last-Event-ID") or after
    return StreamingResponse(
        sse_stream(container.bus, session_id, after=cursor),  # Starlette 取消生成器后退订
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/v1/sessions/{session_id}/inspections/{character_id}/{turn}")
async def inspection(
    session_id: str, character_id: str, turn: int, container: AppContainer = Depends(get_container)
):
    r = await runner_or_404(container, session_id)
    data = r.inspection(character_id, turn)
    if data is None:
        raise HTTPException(404, "无该回合注入记录（仅记录模型生成回合）")
    return data


@router.get("/api/v1/sessions/{session_id}/model-requests")
async def list_model_requests(
    session_id: str, container: AppContainer = Depends(get_container)
):
    """List the actual roleplay request bodies captured at the HTTP boundary."""
    await runner_or_404(container, session_id)
    return {"requests": await asyncio.to_thread(container.request_archive.list, session_id)}


@router.get("/api/v1/sessions/{session_id}/generation-inspections/{message_id}/{generation_id}")
async def generation_inspection(session_id: str, message_id: str, generation_id: str,
                                container: AppContainer = Depends(get_container)):
    runner = await runner_or_404(container, session_id)
    message = message_or_404(runner, message_id)
    record = runner.runtime.inspections.generation(message_id, generation_id)
    if record is None:
        raise HTTPException(404, "没有该候选的生成检查记录")
    return build_inspection(record, message.actor, message.turn)


@router.get("/api/v1/sessions/{session_id}/model-requests/{request_id}")
async def get_model_request(
    session_id: str, request_id: str, container: AppContainer = Depends(get_container)
):
    await runner_or_404(container, session_id)
    record = await asyncio.to_thread(container.request_archive.get, session_id, request_id)
    if record is None:
        raise HTTPException(404, "没有这条模型请求记录")
    return record


@router.get("/api/v1/sessions/{session_id}/context-preview/{character_id}")
async def context_preview(
    session_id: str, character_id: str,
    container: AppContainer = Depends(get_container),
):
    """Rebuild the next prompt against current settings; never label it historical."""
    r = await runner_or_404(container, session_id)
    if r.busy():
        raise HTTPException(409, "当前回合进行中，请稍后预览")
    character = r.state.character(character_id)
    if character is None:
        raise HTTPException(404, "角色不在当前路线")
    async with r.runtime.turn_lock:
        turn = r.state.current_turn() + 1
        capacity = await r.context_builder.model_capacity(character)
        visible = r.context_builder.compress_history(
            character, r.state.visible_messages_for(character_id),
            input_limit=capacity.input_limit,
        )
        injections = await r.context_builder.build_injections(character, turn, visible=visible)
        ctx = TurnContext(
            session_id=session_id, character_id=character_id, turn=turn,
            visible_messages=visible, injections=injections,
            actor_labels=build_reply_frame(r.state, character_id, turn, "preview", visible).actor_labels,
            budget_tokens=capacity.input_limit,
            capacity_source=capacity.source,
            capacity_fetched_at=capacity.fetched_at,
            model_id=character.llm.model,
            gateway=character.llm.base_url,
            model_provider=character.llm.effective_provider,
            context_limit=capacity.context_limit,
            output_reserve=capacity.output_reserve,
            world_core_brief=r.state.meta.world_core_brief,
            world_runtime_policy=r.state.meta.world_runtime_policy,
            player_persona=r.state.meta.player_persona,
            player_identity_source=identity_source(r.state),
            reply_max_tokens=r.state.meta.reply_max_tokens,
            style=r.context_builder.narrative_style(),
            prompt_transforms=r.context_builder.prompt_transforms(),
        )
        plan = plan_context(ctx, model=character.llm.model, persona=persona_from_card(character.card, player_name_for_context(ctx)))
        composed = plan.prompt
        ctx.planned_prompt = composed
        ctx.plan_id = plan.plan_id
        result = build_inspection({"ctx": ctx, "composed": composed}, character_id, turn, mode="preview")
        from mrp.orchestrator.context_comparison import source_materials
        result["source_materials"] = source_materials(ctx, composed)
        result["branch_revision"] = r.state.meta.branch_revision
        present = {item.entry_id for item in injections}
        lorebook_reasons: dict[str, str] = {}
        for book in r.lorebooks:
            _, reasons = r.lorebook_engine.scan_with_diagnostics(
                book, visible, rng_seed=r.runtime.rng(turn), budget=book.token_budget,
            )
            lorebook_reasons.update(reasons)
        result["not_triggered"] = [{
            "entry_id": f"{book.id}:{entry.uid}",
            "title": entry.comment or book.name,
            "reason": lorebook_reasons.get(f"{book.id}:{entry.uid}", "未在当前可见对话中触发"),
        } for book in r.lorebooks for entry in book.entries
            if f"{book.id}:{entry.uid}" not in present]
        pinned = next((item for item in injections if item.entry_id == "pinned_facts"), None)
        result["pinned_facts"] = [{
            "id": fact.id,
            "visible_to": fact.visible_to,
            "included": bool(pinned and fact.content.strip() in pinned.content
                             and pinned.entry_id in composed.included_entry_ids),
            "reason": (
                "对该角色不可见" if fact.visible_to != "all" and character.id not in fact.visible_to
                else "已注入" if pinned and fact.content.strip() in pinned.content
                     and pinned.entry_id in composed.included_entry_ids
                else "超出固定信息或本轮总预算"
            ),
        } for fact in r.state.pinned_facts]
        return result


@router.get("/api/v1/sessions/{session_id}/cost")
async def cost(session_id: str, container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    return r.cost_report()


# ---------- 存档（R6.5） ----------


@router.post("/api/v1/sessions/{session_id}/save")
async def save_session(
    session_id: str, name: str = "", container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None,
):
    from mrp.server.creation_commands import execute_creation
    from mrp.orchestrator.worldline_state import record_persisted_head
    async def create():
        r = await runner_or_404(container, session_id)
        if r.busy():
            raise HTTPException(409, '回合进行中，稍后再保存')
        async with r.runtime.turn_lock:
            state = r.state.model_copy(deep=True)
            watermark = container.memory_store.current_watermark(session_id)
            record_persisted_head(state, watermark)
            memory_snapshot = await asyncio.to_thread(container.memory_store.snapshot_at,
                session_id, watermark, state.messages)
            _, summary = await container.saves.create(state, name, memory_snapshot)
            return {**summary.api_dict(), 'branch_revision':state.meta.branch_revision}
    try:
        return await execute_creation(container, 'save.create', {'branch_id':session_id, 'name':name},
            command_id(operation_header), create, response=response)
    except ValueError as exc:
        raise HTTPException(409 if getattr(exc, 'command_conflict', False) else 400, conflict_detail(exc)) from exc


@router.get("/api/v1/saves")
async def list_saves(
    session_id: str | None = None, container: AppContainer = Depends(get_container)
):
    summaries = await container.saves.list(session_id or None)
    return [s.api_dict() for s in summaries]


@router.post("/api/v1/saves/{save_id}/restore", response_model=LegacyRestoreResult, response_model_exclude_none=True)
async def restore_save(save_id: str, container: AppContainer = Depends(get_container),
                       operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None):
    operation = command_id(operation_header)
    if operation:
        try:
            replay = await container.sessions.lookup_restore_command(operation, save_id)
        except ValueError as exc:
            raise HTTPException(409, conflict_detail(exc)) from exc
        if replay is not None:
            if response is not None:
                response.headers['X-Command-ID'] = operation
                response.headers['X-Branch-Revision'] = str(replay['revision'])
                response.headers['X-Memory-Restore-Status'] = replay['result'].get('memory_restore_status', 'unavailable')
            return replay['result']
    save = await container.saves.load(save_id)  # R35：v1 存档恢复时自动迁移
    if save is None:
        raise HTTPException(404, f"存档不存在: {save_id}")
    runner = await runner_or_404(container, save.state.meta.id)
    from mrp.orchestrator.message_regeneration import restore_state_in_place
    from mrp.application.branch_commit import BranchCommitConflict
    async def mutate():
        restored = save.state.model_copy(deep=True)
        restored.meta.branch_revision = runner.state.meta.branch_revision
        restore_state_in_place(runner.state, restored)
        runner.lorebooks = runner.state.lorebooks
        memory_restore_status = await container.memory_commands.restore_snapshot(runner, save)
        result = runner.state.model_dump(mode='json')
        result['memory_restore_status'] = memory_restore_status
        result['meta']['branch_revision'] += int(runner.state.schema_version >= 3)
        return result
    try:
        await container.story_lifecycle.ensure_transactional_branch(runner.state.meta.id)
        result = await execute_command(container, runner, 'save.restore', {'save_id':save_id}, mutate,
            operation_id=operation, response=response)
        if response is not None:
            response.headers['X-Memory-Restore-Status'] = result.get('memory_restore_status', 'unavailable')
        return result
    except BranchCommitConflict as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc
