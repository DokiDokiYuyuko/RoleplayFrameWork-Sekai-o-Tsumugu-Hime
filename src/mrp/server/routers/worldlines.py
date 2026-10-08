"""Worldline graph, branch, and story-event endpoints."""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from mrp.orchestrator.worldline import WorldlineError
from mrp.orchestrator.story_archive import StoryArchiveError, export_story, import_story
from mrp.orchestrator.worldline_state import BranchPointUnavailable, state_at
from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.shared.models import StoryEvent, StoryBookmark, Visibility
from mrp.storage.naming import content_disposition
from mrp.contracts.story import project_message
from mrp.server.command_ids import command_id, command_payload, conflict_detail, execute_command
from mrp.server.creation_commands import execute_creation
from mrp.server.lifecycle_commands import execute_lifecycle
from mrp.contracts.commands import (StoryListSummary, WorldlineView, StoryTrashView, StoryDeleteResult,
    StoryRestoreResult, StoryPurgeResult, StoryPresetResult)

router = APIRouter()


class ForkMessageReq(BaseModel):
    message_id: str
    title: str = Field("", max_length=200)
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=120)


class ForkSaveReq(BaseModel):
    title: str = Field("", max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=120)


class PatchBranchReq(BaseModel):
    name: str | None = Field(None, max_length=200)
    archived: bool | None = None
    expected_revision: int = Field(ge=0)


class EventReq(BaseModel):
    anchor_message_id: str
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field("", max_length=5000)
    kind: Literal["turning_point", "clue", "relationship", "scene", "note"] = "note"
    visible_to: Visibility = "all"


class EventPatchReq(BaseModel):
    anchor_message_id: str | None = None
    title: str | None = Field(None, min_length=1, max_length=200)
    summary: str | None = Field(None, max_length=5000)
    kind: Literal["turning_point", "clue", "relationship", "scene", "note"] | None = None
    visible_to: Visibility | None = None


class BookmarkReq(BaseModel):
    message_id: str
    title: str = Field("", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=20)


class BookmarkPatchReq(BaseModel):
    title: str | None = Field(None, max_length=200)
    tags: list[str] | None = Field(None, max_length=20)


class StoryPromptPresetReq(BaseModel):
    preset_id: str | None = None


def _http_error(exc: WorldlineError) -> HTTPException:
    message = str(exc)
    if exc.status_code == 404:
        code = "not_found"
    elif "备份" in message:
        code = "backup_failed"
    elif "生成" in message or "进行中" in message:
        code = "busy"
    elif "子线" in message:
        code = "has_children"
    elif "存档" in message:
        code = "has_saves"
    else:
        code = "conflict" if exc.status_code == 409 else "invalid_request"
    return HTTPException(exc.status_code, {"code": code, "message": message})


async def _branch_command(container, branch_id, action, payload, mutate, operation_header, response, expected_revision=None):
    from mrp.application.branch_commit import BranchCommitConflict
    runner = await container.load_session(branch_id)
    if runner is None:
        raise HTTPException(404, '路线不存在')
    try:
        return await execute_command(container, runner, action, payload, lambda: mutate(runner),
            operation_id=command_id(operation_header), expected_revision=expected_revision, response=response)
    except BranchCommitConflict as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc
    except WorldlineError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/stories", response_model=list[StoryListSummary])
async def list_stories(container: AppContainer = Depends(get_container)):
    rows = await container.worldlines.stories()
    for row in rows:
        row["membership_revision"] = await asyncio.to_thread(container.story_lifecycle.repo.membership,row["story_id"])
    return rows


@router.delete("/api/v1/stories/{story_id}", response_model=StoryDeleteResult)
async def delete_story(story_id: str, container: AppContainer = Depends(get_container),
    membership_revision: str | None = Query(None), operation_header: str | None = Header(None,alias='X-Operation-ID'),response:Response=None):
    return await execute_lifecycle(container,'delete',story_id,{'membership_revision':membership_revision},command_id(operation_header),response)


@router.get("/api/v1/stories/trash",response_model=list[StoryTrashView])
async def list_story_trash(container: AppContainer = Depends(get_container)):
    sql = await asyncio.to_thread(container.story_lifecycle.repo.trash)
    legacy = await container.story_backups.list_trash()
    known = await asyncio.to_thread(container.story_lifecycle.repo.known_trash_stories)
    return sql + [row for row in legacy if row['story_id'] not in known]


@router.get("/api/v1/stories/backup-status")
async def story_backup_status(container: AppContainer = Depends(get_container)):
    return {"errors": container.story_backups.errors}


@router.post("/api/v1/stories/trash/{story_id}/restore",response_model=StoryRestoreResult)
async def restore_story(story_id: str, container: AppContainer = Depends(get_container),
    generation_id:str|None=Query(None),operation_header:str|None=Header(None,alias='X-Operation-ID'),response:Response=None):
    return await execute_lifecycle(container,'restore',story_id,{'generation_id':generation_id},command_id(operation_header),response)


@router.delete("/api/v1/stories/trash/{story_id}",response_model=StoryPurgeResult)
async def purge_story(story_id: str, container: AppContainer = Depends(get_container),
    generation_id:str|None=Query(None),operation_header:str|None=Header(None,alias='X-Operation-ID'),response:Response=None):
    return await execute_lifecycle(container,'purge',story_id,{'generation_id':generation_id},command_id(operation_header),response)


@router.get("/api/v1/stories/{story_id}/backups")
async def list_story_backups(story_id: str, container: AppContainer = Depends(get_container)):
    return await container.story_backups.list_snapshots(story_id)


@router.get("/api/v1/stories/{story_id}/backups/{filename}")
async def download_story_backup(story_id: str, filename: str, container: AppContainer = Depends(get_container)):
    try:
        data = await container.story_backups.snapshot_bytes(story_id, filename)
    except FileNotFoundError as exc:
        raise HTTPException(404, "快照不存在") from exc
    except (ValueError, StoryArchiveError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": content_disposition(filename, "", fallback="story-backup.story.zip"),
    })


@router.get("/api/v1/stories/{story_id}/export")
async def export_story_zip(
    story_id: str, include_memory: bool = True,
    container: AppContainer = Depends(get_container),
):
    try:
        data = await export_story(container, story_id, include_memory)
    except StoryArchiveError as exc:
        raise HTTPException(404, str(exc)) from exc
    rows = [row for row in await container.sessions.list_summaries()
            if (row.story_id or row.id) == story_id]
    root = next((row for row in rows if row.id == story_id), rows[0] if rows else None)
    title = root.title if root is not None else story_id
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": content_disposition(title, ".story.zip", fallback=story_id),
    })


@router.post("/api/v1/stories/import")
async def import_story_zip(
    file: UploadFile = File(...),
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None,
):
    data = await file.read(200 * 1024 * 1024 + 1)
    try:
        import hashlib
        async def create():
            return await import_story(container, data)
        return await execute_creation(container, 'story.import', {'sha256':hashlib.sha256(data).hexdigest()},
            command_id(operation_header), create, response=response)
    except StoryArchiveError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc


@router.get("/api/v1/stories/{story_id}/worldline",response_model=WorldlineView)
async def get_worldline(
    story_id: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(40, ge=1, le=100),
    query: str = Query("", max_length=200),
    container: AppContainer = Depends(get_container),
):
    try:
        result = await container.worldlines.worldline(story_id, cursor, limit, query)
        result["membership_revision"] = await asyncio.to_thread(container.story_lifecycle.repo.membership,story_id)
        return result
    except WorldlineError as exc:
        raise _http_error(exc) from exc


@router.get("/api/v1/stories/{story_id}/bookmarks")
async def list_story_bookmarks(story_id: str, container: AppContainer = Depends(get_container)):
    rows = [row for row in await container.sessions.list_summaries() if (row.story_id or row.id) == story_id]
    if not rows:
        raise HTTPException(404, "故事不存在")
    bookmarks = []
    for row in rows:
        state = await container.sessions.load_state_readonly(row.id)
        if state:
            visible = {m.id for m in state.messages if m.can_see("player")}
            bookmarks.extend({**item.model_dump(mode="json"), "valid": item.message_id in visible,
                              "branch_name": state.meta.branch_name}
                             for item in state.bookmarks)
    return sorted(bookmarks, key=lambda item: item["created_at"], reverse=True)


@router.patch("/api/v1/stories/{story_id}/prompt-preset",response_model=StoryPresetResult)
async def set_story_prompt_preset(story_id:str,req:StoryPromptPresetReq,container:AppContainer=Depends(get_container),
    membership_revision:str|None=Query(None),operation_header:str|None=Header(None,alias='X-Operation-ID'),response:Response=None):
    return await execute_lifecycle(container,'preset',story_id,{'preset_id':req.preset_id,'membership_revision':membership_revision},command_id(operation_header),response)


@router.post("/api/v1/branches/{branch_id}/bookmarks")
async def create_bookmark(branch_id: str, req: BookmarkReq, container: AppContainer = Depends(get_container),
                          operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None):
    async def mutate(runner):
        message = next((m for m in runner.state.messages if m.id == req.message_id and m.can_see("player")), None)
        if message is None:
            raise HTTPException(404, "消息不存在或不可见")
        existing = next((b for b in runner.state.bookmarks if b.message_id == req.message_id), None)
        if existing:
            return existing.model_dump(mode='json')
        bookmark = StoryBookmark(story_id=runner.state.meta.story_id, branch_id=branch_id,
                                 message_id=req.message_id, title=req.title.strip() or message.content[:40],
                                 tags=[tag.strip()[:40] for tag in req.tags if tag.strip()])
        runner.state.bookmarks.append(bookmark)
        return bookmark.model_dump(mode='json')
    return await _branch_command(container, branch_id, 'bookmark.create', command_payload(req), mutate, operation_header, response)


@router.patch("/api/v1/branches/{branch_id}/bookmarks/{bookmark_id}")
async def patch_bookmark(branch_id: str, bookmark_id: str, req: BookmarkPatchReq,
                         container: AppContainer = Depends(get_container),
                         operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None):
    async def mutate(runner):
        item = next((b for b in runner.state.bookmarks if b.id == bookmark_id), None)
        if item is None:
            raise HTTPException(404, '书签不存在')
        if req.title is not None:
            item.title = req.title.strip()
        if req.tags is not None:
            item.tags = [tag.strip()[:40] for tag in req.tags if tag.strip()]
        return item.model_dump(mode='json')
    return await _branch_command(container, branch_id, 'bookmark.patch:' + bookmark_id, command_payload(req), mutate, operation_header, response)


@router.delete("/api/v1/branches/{branch_id}/bookmarks/{bookmark_id}")
async def delete_bookmark(branch_id: str, bookmark_id: str, container: AppContainer = Depends(get_container),
                          operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None):
    async def mutate(runner):
        size = len(runner.state.bookmarks)
        runner.state.bookmarks = [b for b in runner.state.bookmarks if b.id != bookmark_id]
        if len(runner.state.bookmarks) == size:
            raise HTTPException(404, '书签不存在')
        return {'ok':True}
    return await _branch_command(container, branch_id, 'bookmark.delete:' + bookmark_id, {}, mutate, operation_header, response)


@router.get("/api/v1/stories/{story_id}/search")
async def search_story(story_id: str, q: str = Query("", max_length=200),
                       branch_id: str | None = None, actor: str | None = None,
                       scene_id: str | None = None, bookmarked: bool = False, event: bool = False,
                       cursor: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100),
                       container: AppContainer = Depends(get_container)):
    rows = [row for row in await container.sessions.list_summaries() if (row.story_id or row.id) == story_id]
    if not rows:
        raise HTTPException(404, "故事不存在")
    # Legacy branches are indexed on first use; the JSON state is authoritative.
    for row in rows:
        if not await asyncio.to_thread(container.story_search.has_branch, row.id):
            state = await container.sessions.load_state_readonly(row.id)
            if state:
                await asyncio.to_thread(container.story_search.index_state, state)
    page = await asyncio.to_thread(container.story_search.search, story_id, q,
        branch_id=branch_id, actor=actor, scene_id=scene_id, bookmarked=bookmarked,
        event=event, cursor=cursor, limit=limit)
    verified = []
    state_cache = {}
    for item in page["results"]:
        branch = item["branch_id"]
        if branch not in state_cache:
            state_cache[branch] = await container.sessions.load_state_readonly(branch)
        state = state_cache[branch]
        message = next((m for m in state.messages if m.id == item["message_id"]), None) if state else None
        if message and message.can_see("player"):
            item["branch_name"] = state.meta.branch_name
            verified.append(item)
    page["results"] = verified
    return page


@router.post("/api/v1/branches/{branch_id}/fork")
async def fork_branch(
    branch_id: str, req: ForkMessageReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None,
):
    try:
        async def create():
            result = await container.worldlines.fork_message(branch_id, req.message_id, req.title,
                req.expected_revision, req.idempotency_key)
            return result.as_dict()
        return await execute_creation(container, 'branch.fork', {'branch_id':branch_id, **req.model_dump()},
            command_id(operation_header, req.idempotency_key), create, response=response)
    except WorldlineError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc


@router.post("/api/v1/saves/{save_id}/fork")
async def fork_save(
    save_id: str, req: ForkSaveReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None,
):
    try:
        async def create():
            result = await container.worldlines.fork_save(save_id, req.title, req.idempotency_key)
            return result.as_dict()
        return await execute_creation(container, 'save.fork', {'save_id':save_id, **req.model_dump()},
            command_id(operation_header, req.idempotency_key), create, response=response)
    except WorldlineError as exc:
        raise _http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(409, conflict_detail(exc)) from exc


@router.patch("/api/v1/branches/{branch_id}")
async def patch_branch(
    branch_id: str, req: PatchBranchReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None,
):
    async def mutate(runner):
        if req.name is not None:
            runner.state.meta.branch_name = req.name.strip() or runner.state.meta.branch_name
            runner.state.meta.title = runner.state.meta.branch_name
        if req.archived is not None:
            runner.state.meta.archived = req.archived
        return {'id':branch_id, 'name':runner.state.meta.branch_name,
                'archived':runner.state.meta.archived, 'branch_revision':runner.state.meta.branch_revision}
    return await _branch_command(container, branch_id, 'branch.patch', command_payload(req), mutate,
                                 operation_header, response, req.expected_revision)


@router.get("/api/v1/branches/{branch_id}/messages/{message_id}/point")
async def branch_point(
    branch_id: str, message_id: str,
    container: AppContainer = Depends(get_container),
):
    state = await container.sessions.load_state_readonly(branch_id)
    if state is None:
        raise HTTPException(404, "世界线不存在")
    message = next((item for item in state.messages if item.id == message_id), None)
    if message is None:
        raise HTTPException(404, "消息不存在")
    history = {"history_complete": False, "history_warning": "", "history_floor": 0}
    try:
        point = state_at(state, message_id)
        legacy_head = (
            point.revision.source == "legacy_head"
            and point.revision.id == state.head_state_revision_id
            and bool(state.messages) and state.messages[-1].id == message_id
        )
        # The fork command freezes the *current* legacy head's memory watermark
        # under its turn lock. Earlier legacy messages have no reliable state.
        # A reliable story-state anchor can fork without unverifiable old memories.
        # The UI presents the warning before the user submits the fork command.
        watermark = point.revision.memory_watermark
        if watermark is None and legacy_head:
            watermark = container.memory_store.current_watermark(branch_id)
        history = container.memory_store.history_status(branch_id, watermark)
        forkable = True
        reason = None
    except BranchPointUnavailable as exc:
        forkable = False
        reason = str(exc)
    return {
        "branch_id": branch_id,
        "message": project_message(message),
        "branch_revision": state.meta.branch_revision,
        "forkable": forkable,
        "reason": reason,
        **history,
    }


@router.get("/api/v1/branches/{branch_id}/events")
async def list_events(
    branch_id: str, container: AppContainer = Depends(get_container),
):
    state = await container.sessions.load_state_readonly(branch_id)
    if state is None:
        raise HTTPException(404, "世界线不存在")
    return [event.model_dump(mode="json") for event in state.story_events]


@router.post("/api/v1/branches/{branch_id}/events")
async def create_event(
    branch_id: str, req: EventReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None,
):
    async def mutate(runner):
        event = StoryEvent.model_validate(req.model_dump())
        container.worldlines._validate_event(runner.state, event)
        runner.state.story_events.append(event)
        return event.model_dump(mode='json')
    return await _branch_command(container, branch_id, 'event.create', command_payload(req), mutate, operation_header, response)


@router.patch("/api/v1/branches/{branch_id}/events/{event_id}")
async def patch_event(
    branch_id: str, event_id: str, req: EventPatchReq,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None,
):
    async def mutate(runner):
        old = next((item for item in runner.state.story_events if item.id == event_id), None)
        if old is None:
            raise HTTPException(404, '剧情事件不存在')
        updated = StoryEvent.model_validate({**old.model_dump(), **req.model_dump(exclude_unset=True)})
        container.worldlines._validate_event(runner.state, updated)
        runner.state.story_events = [updated if item.id == event_id else item for item in runner.state.story_events]
        return updated.model_dump(mode="json")
    return await _branch_command(container, branch_id, 'event.patch:' + event_id, command_payload(req), mutate, operation_header, response)


@router.delete("/api/v1/branches/{branch_id}/events/{event_id}")
async def delete_event(
    branch_id: str, event_id: str,
    container: AppContainer = Depends(get_container),
    operation_header: str | None = Header(None, alias='X-Operation-ID'), response: Response = None,
):
    async def mutate(runner):
        size = len(runner.state.story_events)
        runner.state.story_events = [item for item in runner.state.story_events if item.id != event_id]
        if len(runner.state.story_events) == size:
            raise HTTPException(404, '剧情事件不存在')
        return {"ok": True}
    return await _branch_command(container, branch_id, 'event.delete:' + event_id, {}, mutate, operation_header, response)
