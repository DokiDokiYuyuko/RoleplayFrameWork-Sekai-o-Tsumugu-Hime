"""Small, evidence-backed long-story and personal-library workflows."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from mrp.importers.st_chat import MAX_BYTES, StImportConflict, import_st_chat, parse_st_chat
from mrp.orchestrator.asset_updates import apply_asset_updates, asset_updates
from mrp.orchestrator.context_comparison import compare_context, correction_view, enrich_memory_materials, source_materials
from mrp.orchestrator.corrected_regeneration import CorrectedRegenerationRequest, correction_options, correction_transform
from mrp.orchestrator.inspections import build_inspection
from mrp.orchestrator.message_regeneration import RegenerationConflict, RegenerationFailure
from mrp.orchestrator.story_review import compare_branches, review_branch
from mrp.server.deps import get_container, message_or_404, runner_or_404
from mrp.storage.personal_library import library_checklist, verify_library
from mrp.server.command_ids import command_id, command_payload, execute_command

router = APIRouter()


@router.get("/api/v1/sessions/{session_id}/messages/{message_id}/correction-options")
async def get_correction_options(session_id: str, message_id: str, container=Depends(get_container)):
    runner = await runner_or_404(container, session_id)
    if runner.busy():
        raise HTTPException(409, "路线正在生成，请稍后核对纠错材料")
    try:
        async with runner.runtime.turn_lock:
            target = message_or_404(runner, message_id)
            return (await asyncio.to_thread(correction_options, runner, target, container.memory_store))[0]
    except (RegenerationConflict, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/v1/sessions/{session_id}/messages/{message_id}/regenerate-corrected")
async def regenerate_corrected(session_id: str, message_id: str, req: CorrectedRegenerationRequest,
                               container=Depends(get_container),
                               operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None):
    command_id(operation_header, req.operation_id)
    runner = await runner_or_404(container, session_id)
    from mrp.server.routers.messages import _run_regeneration
    async def backup_before_correction(message_ids):
        await container.story_backups.snapshot(runner.state.meta.story_id, reason="before_context_correction")
    return await _run_regeneration(container, runner, "one", message_id, req,
        baseline_transform=correction_transform(runner, req, container.memory_store),
        protect_extra=backup_before_correction, response=response)


@router.get("/api/v1/sessions/{session_id}/context-comparison/{message_id}/{generation_id}")
async def context_comparison(session_id: str, message_id: str, generation_id: str,
                             container=Depends(get_container)):
    from mrp.server.routers.sessions import context_preview
    runner = await runner_or_404(container, session_id)
    message = message_or_404(runner, message_id)
    record = runner.runtime.inspections.generation(message_id, generation_id)
    if record is None:
        raise HTTPException(404, "该候选的原始输入检查记录未保留；不能重建为历史实际请求")
    if runner.state.character(message.actor) is None:
        raise HTTPException(422, "群体候选暂不支持角色下一轮预览；可查看原始输入纠错材料")
    actual = build_inspection(record, message.actor, message.turn)
    materials = source_materials(record["ctx"], record["composed"])
    actual["source_materials"] = materials
    preview = await context_preview(session_id, message.actor, container)
    state = runner.state.model_copy(deep=True)
    if state.meta.branch_revision != preview["branch_revision"]:
        raise HTTPException(409, "路线在预览后已改变，请重新比较")
    result = compare_context(actual, materials, preview, state, message.actor)
    return await asyncio.to_thread(enrich_memory_materials, result, record, state, container.memory_store, message.actor)


@router.get("/api/v1/sessions/{session_id}/context-materials/{message_id}/{generation_id}")
async def context_materials(session_id: str, message_id: str, generation_id: str,
                            container=Depends(get_container)):
    runner = await runner_or_404(container, session_id)
    message = message_or_404(runner, message_id)
    record = runner.runtime.inspections.generation(message_id, generation_id)
    if record is None:
        raise HTTPException(404, "该候选的原始输入检查记录未保留；不能重建为历史实际请求")
    state = runner.state.model_copy(deep=True)
    actual = build_inspection(record, message.actor, message.turn)
    materials = source_materials(record["ctx"], record["composed"])
    actual["source_materials"] = materials
    result = correction_view(actual, materials, state, message.actor)
    return await asyncio.to_thread(enrich_memory_materials, result, record, state, container.memory_store, message.actor)


class StImportReq(BaseModel):
    jsonl: str = Field(max_length=MAX_BYTES)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    character_id: str = Field(min_length=1, max_length=120)
    user_name: str = Field(min_length=1, max_length=120)
    title: str = Field("迁入聊天", max_length=200)
    acknowledge_degraded: bool = False


class ApplyUpdatesReq(BaseModel):
    expected_revision: int = Field(ge=0)
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    selected_fields: list[str] = Field(min_length=1, max_length=1000)


async def _read_state(container, branch_id):
    runner = container.runners.get(branch_id)
    state = runner.state.model_copy(deep=True) if runner is not None else await container.sessions.load_state_readonly(branch_id)
    if state is None:
        raise HTTPException(404, "路线不存在")
    return state


@router.post("/api/v1/chat-import/st/preview")
async def st_preview(file: UploadFile = File(...)):
    try:
        return parse_st_chat(await file.read(MAX_BYTES + 1))[2]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/v1/chat-import/st")
async def st_import(req: StImportReq, container=Depends(get_container),
                    operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None):
    try:
        from mrp.server.creation_commands import execute_creation
        import hashlib
        raw = req.jsonl.encode('utf-8')
        async def create():
            return await import_st_chat(container, raw, source_sha256=req.source_sha256,
            character_id=req.character_id, user_name=req.user_name, title=req.title,
            acknowledge_degraded=req.acknowledge_degraded)
        return await execute_creation(container, 'chat.import-st', {'sha256':hashlib.sha256(raw).hexdigest(),
            **req.model_dump(exclude={'jsonl'})}, command_id(operation_header), create, response=response)
    except StImportConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409 if getattr(exc, 'command_conflict', False) else 422,
            {'code':getattr(exc, 'code', 'invalid_request'), 'message':str(exc)}) from exc


@router.post("/api/v1/chat-import/st/file")
async def st_import_file(file: UploadFile = File(...), source_sha256: str = Form(...),
                         character_id: str = Form(...), user_name: str = Form(...),
                         title: str = Form("迁入聊天"), acknowledge_degraded: bool = Form(False),
                         container=Depends(get_container),
                         operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None):
    if len(character_id) > 120 or len(title) > 200:
        raise HTTPException(422, "角色标识或标题过长")
    try:
        from mrp.server.creation_commands import execute_creation
        import hashlib
        raw = await file.read(MAX_BYTES + 1)
        async def create():
            return await import_st_chat(container, raw, source_sha256=source_sha256,
            character_id=character_id, user_name=user_name, title=title, acknowledge_degraded=acknowledge_degraded)
        return await execute_creation(container, 'chat.import-st', {'sha256':hashlib.sha256(raw).hexdigest(),
            'source_sha256':source_sha256, 'character_id':character_id, 'user_name':user_name, 'title':title,
            'acknowledge_degraded':acknowledge_degraded}, command_id(operation_header), create, response=response)
    except StImportConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409 if getattr(exc, 'command_conflict', False) else 422,
            {'code':getattr(exc, 'code', 'invalid_request'), 'message':str(exc)}) from exc


@router.get("/api/v1/branches/{branch_id}/review")
async def branch_review(branch_id: str, actor_id: str = Query("player", max_length=120),
                        anchor_message_id: str | None = Query(None, max_length=120),
                        limit: int = Query(100, ge=1, le=1000), container=Depends(get_container)):
    state = await _read_state(container, branch_id)
    try:
        return await asyncio.to_thread(review_branch, state, container.memory_store,
            actor_id=actor_id, anchor_message_id=anchor_message_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/v1/branches/{branch_id}/compare/{other_id}")
async def branch_compare(branch_id: str, other_id: str, actor_id: str = Query("player", max_length=120),
                         limit: int = Query(100, ge=1, le=1000), container=Depends(get_container)):
    left = await _read_state(container, branch_id)
    right = await _read_state(container, other_id)
    try:
        return await asyncio.to_thread(compare_branches, left, right, container.memory_store,
                                       actor_id=actor_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/v1/branches/{branch_id}/asset-updates")
async def branch_asset_updates(branch_id: str, container=Depends(get_container)):
    return asset_updates(container, await _read_state(container, branch_id))[0]


@router.post("/api/v1/branches/{branch_id}/asset-updates/apply")
async def branch_apply_updates(branch_id: str, req: ApplyUpdatesReq, container=Depends(get_container),
                               operation_header: str | None = Header(None, alias="X-Operation-ID"), response: Response = None):
    runner = await runner_or_404(container, branch_id)
    async def mutate():
        try:
            return await apply_asset_updates(container, runner, **req.model_dump(), defer_commit=True)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return await execute_command(container, runner, "assets.apply", command_payload(req), mutate,
        operation_id=command_id(operation_header), expected_revision=req.expected_revision, response=response)


@router.get("/api/v1/personal-library/backup-checklist")
async def backup_checklist(container=Depends(get_container)):
    try:
        return await asyncio.to_thread(library_checklist, container.data_root)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "数据目录无法可靠列出，请离线检查") from exc


@router.post("/api/v1/personal-library/verify")
async def verify_backup(file: UploadFile = File(...)):
    # Large libraries are verified by the offline tool; HTTP has a smaller memory limit.
    maximum = 200 * 1024 * 1024
    raw = await file.read(maximum + 1)
    if len(raw) > maximum:
        raise HTTPException(413, "超过网页校验的 200 MB 限制，请使用离线工具校验")
    try:
        return await asyncio.to_thread(verify_library, raw)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
