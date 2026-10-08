"""记忆路由：查看 / 检索 / 编辑 / 删除 / 手动固化（R5.x / R36.x）。"""
from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Response
from pydantic import BaseModel, Field
from typing import Literal
from mrp.shared.models import MemoryRecord, utcnow
from mrp.orchestrator.important_memory import visible_memory_messages

from mrp.server.container import AppContainer
from mrp.server.deps import get_container, runner_or_404
from mrp.server.command_ids import command_id, command_payload, execute_command, conflict_detail
from mrp.application.memory_commands import MemoryCommandError
from mrp.application.branch_commit import BranchCommitConflict
from mrp.server.command_ids import command_headers
from mrp.contracts.commands import MemoryRecordView, MemoryRecordsResult, MemoryDeleteResult, MemoryJobView, MemoryScheduledResult

router = APIRouter()


class MemoryPatchReq(BaseModel):
    content: str | None = None
    keywords: list[str] | None = None
    importance: int | None = None
    category: Literal["experience", "relationship", "unfinished"] | None = None
    important: bool | None = None
    matter_status: Literal["open", "completed", "cancelled", "unknown"] | None = None
    expected_revision: int | None = None
    expected_branch_revision: int | None = None


class RememberReq(BaseModel):
    character_ids: list[str] = Field(min_length=1)
    source_message_ids: list[str] = Field(min_length=1)
    source_fingerprints: dict[str, str] = Field(default_factory=dict)
    participant_ids: list[str] = Field(default_factory=list)
    content: str = Field(min_length=1)
    category: Literal["experience", "relationship", "unfinished"] = "experience"
    important: bool = True
    matter_status: Literal["open", "completed", "cancelled", "unknown"] = "unknown"
    expected_branch_revision: int | None = None


def actor_or_404(r, actor_id):
    if actor_id not in {c.id for c in r.state.characters} | {g.id for g in r.state.groups} | set(r.state.player_people):
        raise HTTPException(404, "角色不属于当前故事分支")


def visible_records(r, actor_id, records):
    visible = {m.id for m in visible_memory_messages(r.state, actor_id)}
    return [record for record in records if record.session_id == r.state.meta.id
            and (record.source_changed or not record.source_message_ids or set(record.source_message_ids) <= visible)]


@router.get("/api/v1/sessions/{session_id}/memory/records")
async def branch_memories(session_id: str, character_id: str, category: str = "",
                          container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    actor_or_404(r, character_id)
    records = await asyncio.to_thread(container.memory_store.records_for, character_id, session_id=session_id)
    records = [rec for rec in visible_records(r, character_id, records) if not rec.invalidated]
    superseded = {mid for rec in records if not rec.source_changed for mid in rec.supersedes}
    records = [rec for rec in records if rec.id not in superseded and (not category or rec.category == category)]
    return {"records": [rec.model_dump(mode="json") for rec in records]}


@router.get("/api/v1/sessions/{session_id}/memory/windows")
async def memory_windows(session_id: str, character_id: str, container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    actor_or_404(r, character_id)
    windows = await asyncio.to_thread(container.memory_store.window_rows, character_id, session_id)
    return {"windows": [{k: v for k, v in row.items() if k not in {"fingerprints", "record_ids"}} for row in windows]}


@router.post("/api/v1/sessions/{session_id}/memory/records", response_model=MemoryRecordsResult, response_model_exclude_unset=True)
async def remember_event(session_id: str, req: RememberReq, container: AppContainer = Depends(get_container),
                         operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None):
    r = await runner_or_404(container, session_id)
    await container.story_lifecycle.ensure_transactional_branch(session_id)
    async def mutate():
        return await container.memory_commands.remember(r, command_payload(req))
    try:
        return await execute_command(container, r, 'memory.remember', command_payload(req), mutate,
            operation_id=command_id(operation_header), expected_revision=req.expected_branch_revision, response=response)
    except MemoryCommandError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.get("/api/v1/sessions/{session_id}/memory/records/{record_id}/sources")
async def memory_sources(session_id: str, record_id: str, character_id: str,
                         container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    actor_or_404(r, character_id)
    records = await asyncio.to_thread(container.memory_store.records_for, character_id, session_id=session_id)
    record = next((rec for rec in records if rec.id == record_id), None)
    if record is None:
        raise HTTPException(404, "记忆不存在")
    visible = {m.id: m for m in visible_memory_messages(r.state, character_id)}
    return {"messages": [visible[mid].model_dump(mode="json") for mid in record.source_message_ids if mid in visible]}


@router.get("/api/v1/characters/{character_id}/memories")
async def search_memories(
    character_id: str,
    q: str = "",
    k: int = Query(8, ge=1, le=50),  # B17：k clamp（原可传任意大/负数）
    session_id: str = "",
    container: AppContainer = Depends(get_container),
):
    # B14：records_for / search 都是同步 SQLite 调用（cephfs 上可能慢）→ 丢线程池
    if not q:
        records = await asyncio.to_thread(
            container.memory_store.records_for,
            character_id,
            session_id=session_id or None,
        )
        return {"records": [r.model_dump(mode="json") for r in records]}
    hits = await asyncio.to_thread(
        container.memory_store.search,
        character_id,
        q,
        k=k,
        current_turn=None,  # 查看器搜索不带新近度偏置
        session_id=session_id or None,
    )
    return {"records": [r.model_dump(mode="json") for r, _score in hits]}


async def _update_legacy_memory(
    character_id: str,
    record_id: str,
    req: MemoryPatchReq,
    session_id: str = "",
    container: AppContainer = Depends(get_container),
):
    """R36.5 记忆编辑（content/keywords/importance）。"""
    # 同步 SQLite（删旧重插会触发 JSONL 镜像全量重写，cephfs 上可达数百 ms）→ 丢线程池
    r = await runner_or_404(container, session_id) if session_id else None
    if r:
        actor_or_404(r, character_id)
    # Store lock covers the revision check and write, including legacy callers.
    def revise():
        with container.memory_store._lock:
            records = container.memory_store.records_for(character_id, session_id=session_id or None)
            record = next((rec for rec in records if rec.id == record_id), None)
            if record is None:
                raise HTTPException(404, "记忆记录不存在")
            if req.expected_revision is not None and record.revision != req.expected_revision:
                raise HTTPException(409, "记忆已更新，请刷新后重试")
            if req.content is not None and not req.content.strip():
                raise HTTPException(422, "记忆正文不能为空")
            previous = record.model_dump(mode="json", include={"content", "category", "important", "matter_status", "revision"})
            previous["changed_at"] = utcnow().isoformat()
            record.revisions.append(previous)
            for key, value in req.model_dump(exclude_none=True, exclude={"expected_revision", "expected_branch_revision"}).items():
                setattr(record, key, value)
            record.importance = max(1, min(5, record.importance))
            record.revision += 1
            if any(key in req.model_fields_set for key in {"content", "category", "matter_status"}):
                record.manually_revised = True
                if r:
                    visible = {m.id: m for m in visible_memory_messages(r.state, character_id)}
                    record.source_message_ids = [mid for mid in record.source_message_ids if mid in visible]
                    record.source_fingerprints = {mid: visible[mid].fingerprint for mid in record.source_message_ids}
                    record.source_changed = False
                    record.invalidated = False
            if not container.memory_store.update_record(record):
                raise HTTPException(500, "记忆更新失败")
            return record
    if r:
        async with r.runtime.turn_lock:
            record = await asyncio.to_thread(revise)
    else:
        record = await asyncio.to_thread(revise)
    return record.model_dump(mode="json")


async def _delete_legacy_memory(
    character_id: str, record_id: str, session_id: str = "", container: AppContainer = Depends(get_container)
):
    """R36.5 记忆删除——下回合检索即 miss（角色"忘记"）。"""
    # 同上：镜像重写是同步 IO
    records = await asyncio.to_thread(container.memory_store.records_for, character_id, session_id=session_id or None)
    if not any(rec.id == record_id for rec in records):
        raise HTTPException(404, "记忆记录不存在")
    if not await asyncio.to_thread(container.memory_store.delete_record, record_id):
        raise HTTPException(404, f"记忆记录不存在: {record_id}")
    return {"ok": True}


async def _memory_runner(container, session_id, record_id):
    inferred = not session_id
    if not session_id:
        record = await asyncio.to_thread(container.memory_store.record_by_id, record_id)
        session_id = record.session_id if record else ''
    if not session_id:
        return None
    if inferred:
        # Older standalone memory records may carry a historical session label
        # with no owning story. Hidden/purged identities must never fall back.
        try:
            await asyncio.to_thread(container.sessions.require_visible_branch, session_id)
        except ValueError as exc:
            if (getattr(exc, 'code', None) == 'not_found' and
                    not await asyncio.to_thread(container.sessions.exists_sync, session_id)):
                return None
            if getattr(exc, 'command_conflict', False):
                raise HTTPException(getattr(exc, 'status_code', 409), conflict_detail(exc)) from exc
            raise
    r = await runner_or_404(container, session_id)
    await container.story_lifecycle.ensure_transactional_branch(session_id)
    return r


async def _memory_replay(container, session_id, operation, action, payload, response):
    if session_id or not operation:
        return None
    fingerprint = hashlib.sha256(json.dumps({'action':action,'payload':payload}, ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    try:
        receipt = await container.sessions.lookup_command_scope(operation, fingerprint)
    except ValueError as exc:
        if getattr(exc, 'command_conflict', False):
            raise HTTPException(409, conflict_detail(exc)) from exc
        raise
    if receipt is not None:
        command_headers(response, {'operation_id':operation,'branch_revision':receipt['revision']})
        return (receipt['result'],)
    return None


@router.patch("/api/v1/characters/{character_id}/memories/{record_id}", response_model=MemoryRecordView)
async def update_memory(character_id: str, record_id: str, req: MemoryPatchReq, session_id: str = '',
    container: AppContainer = Depends(get_container), operation_header: str | None = Header(None, alias='X-Operation-ID'),
    response: Response = None):
    payload = {'character_id':character_id,'record_id':record_id,**command_payload(req)}
    operation = command_id(operation_header)
    replay = await _memory_replay(container, session_id, operation, 'memory.revise', payload, response)
    if replay is not None:
        return replay[0]
    r = await _memory_runner(container, session_id, record_id)
    if r is None:
        # Old records without an owning story retain the store API; no story atomicity is claimed.
        return await _update_legacy_memory(character_id, record_id, req, '', container)
    async def mutate():
        return await container.memory_commands.revise(r, character_id, record_id, command_payload(req))
    try:
        return await execute_command(container, r, 'memory.revise', payload, mutate,
            operation_id=operation, expected_revision=req.expected_branch_revision, response=response)
    except MemoryCommandError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.delete("/api/v1/characters/{character_id}/memories/{record_id}", response_model=MemoryDeleteResult)
async def delete_memory(character_id: str, record_id: str, session_id: str = '', expected_revision: int | None = None,
    expected_branch_revision: int | None = None, container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None):
    payload = {'character_id':character_id,'record_id':record_id,'expected_revision':expected_revision,
        'expected_branch_revision':expected_branch_revision}
    operation = command_id(operation_header)
    replay = await _memory_replay(container, session_id, operation, 'memory.delete', payload, response)
    if replay is not None:
        return replay[0]
    r = await _memory_runner(container, session_id, record_id)
    if r is None:
        return await _delete_legacy_memory(character_id, record_id, '', container)
    async def mutate():
        return await container.memory_commands.delete(r, character_id, record_id, expected_revision)
    try:
        return await execute_command(container, r, 'memory.delete', payload, mutate,
            operation_id=operation, expected_revision=expected_branch_revision, response=response)
    except MemoryCommandError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post('/api/v1/sessions/{session_id}/memory/consolidate',
    response_model=MemoryRecordsResult | MemoryScheduledResult, response_model_exclude_unset=True)
async def consolidate_memory(session_id: str, character_id: str | None = None, async_mode: bool = False,
    turn_start: int | None = Query(None, ge=1), turn_end: int | None = Query(None, ge=1),
    container: AppContainer = Depends(get_container), operation_header: str | None = Header(None, alias='X-Operation-ID'),
    response: Response = None):
    r = await runner_or_404(container, session_id)
    await container.story_lifecycle.ensure_transactional_branch(session_id)
    payload = {'character_id':character_id,'async_mode':async_mode,'turn_start':turn_start,'turn_end':turn_end}
    metadata = {}
    try:
        result = await container.memory_commands.consolidate(r, payload,
            operation_id=command_id(operation_header), response_meta=metadata)
    except MemoryCommandError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except BranchCommitConflict as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc
    command_headers(response, metadata)
    return result


@router.get('/api/v1/sessions/{session_id}/memory/jobs/{operation_id}', response_model=MemoryJobView,
    response_model_exclude_unset=True)
async def memory_job(session_id: str, operation_id: str, container: AppContainer = Depends(get_container)):
    r = await runner_or_404(container, session_id)
    try:
        return await container.memory_commands.job_status(r, operation_id)
    except MemoryCommandError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
