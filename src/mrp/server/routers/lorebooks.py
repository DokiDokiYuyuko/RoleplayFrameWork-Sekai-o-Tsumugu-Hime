"""世界书路由：导入 / CRUD / 条目补丁 / 导出（R6.3 / R29.4）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from mrp.importers.lorebook import import_lorebook
from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.shared.models import Lorebook, LorebookEntry, new_id, utcnow
from mrp.storage.naming import content_disposition

router = APIRouter()


async def _revise(container: AppContainer, book_id: str, expected_revision: int, change):
    try:
        return await container.lorebook_registry.revise(book_id, expected_revision, change)
    except KeyError as exc:
        raise HTTPException(404, f"世界书不存在: {book_id}") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class PatchBookReq(BaseModel):
    expected_revision: int | None = Field(None, ge=1)
    name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=10000)
    tags: list[str] | None = Field(None, max_length=30)
    entries: list[LorebookEntry] | None = None


class PatchEntryReq(BaseModel):
    expected_revision: int | None = Field(None, ge=1)
    keys: list[str] | None = Field(None, max_length=50)
    secondary_keys: list[str] | None = Field(None, max_length=50)
    content: str | None = Field(None, max_length=100000)
    comment: str | None = Field(None, max_length=2000)
    enabled: bool | None = None
    constant: bool | None = None
    order: int | None = None
    anchor: str | None = None
    depth: int | None = None
    probability: int | None = None


class CreateLorebookReq(BaseModel):
    book: Lorebook


class CreateBlankLorebookReq(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=30)


class CopyLorebookReq(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=10000)
    tags: list[str] | None = Field(None, max_length=30)


@router.post("/api/v1/lorebooks/import")
async def import_lorebook_api(file: UploadFile, container: AppContainer = Depends(get_container)):
    raw = await file.read()
    try:
        obj = json.loads(raw.decode("utf-8"))
        book = import_lorebook(obj)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        raise HTTPException(400, f"世界书解析失败: {e}") from e
    await container.save_lorebook(book)
    return book.model_dump(mode="json")


@router.get("/api/v1/lorebooks")
async def list_lorebooks(container: AppContainer = Depends(get_container)):
    return [b.model_dump(mode="json") for b in container.lorebooks.values()]


@router.post("/api/v1/lorebooks/import-preview")
async def preview_lorebook(file: UploadFile):
    raw = await file.read(10_000_001)
    if len(raw) > 10_000_000:
        raise HTTPException(413, "世界书文件过大")
    try:
        book = import_lorebook(json.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError, TypeError) as exc:
        raise HTTPException(400, f"世界书解析失败: {exc}") from exc
    return {"draft": book.model_dump(mode="json"), **book.import_report}


@router.get("/api/v1/lorebooks/{book_id}")
async def get_lorebook(book_id: str, container: AppContainer = Depends(get_container)):
    b = container.lorebooks.get(book_id)
    if b is None:
        raise HTTPException(404, f"世界书不存在: {book_id}")
    return b.model_dump(mode="json")


@router.patch("/api/v1/lorebooks/{book_id}")
async def patch_lorebook(
    book_id: str, req: PatchBookReq, container: AppContainer = Depends(get_container)
):
    b = container.lorebooks.get(book_id)
    if b is None:
        raise HTTPException(404, f"世界书不存在: {book_id}")
    expected = req.expected_revision if req.expected_revision is not None else b.revision
    def change(draft: Lorebook) -> None:
        if req.name is not None:
            draft.name = req.name
        if req.description is not None:
            draft.description = req.description
        if req.tags is not None:
            draft.tags = list(dict.fromkeys(tag.strip() for tag in req.tags if tag.strip()))
        if req.entries is not None:
            draft.entries = req.entries
    updated = await _revise(container, book_id, expected, change)
    return updated.model_dump(mode="json")


@router.patch("/api/v1/lorebooks/{book_id}/entries/{uid}")
async def patch_entry(
    book_id: str, uid: int, req: PatchEntryReq, container: AppContainer = Depends(get_container)
):
    b = container.lorebooks.get(book_id)
    if b is None:
        raise HTTPException(404, f"世界书不存在: {book_id}")
    expected = req.expected_revision if req.expected_revision is not None else b.revision
    updated_entry: dict = {}
    def change(draft: Lorebook) -> None:
        entry = next((e for e in draft.entries if e.uid == uid), None)
        if entry is None:
            raise ValueError(f"条目不存在: {uid}")
        for field, value in req.model_dump(exclude_none=True).items():
            if field != "expected_revision":
                setattr(entry, field, value)
        updated_entry.update(entry.model_dump(mode="json"))
    await _revise(container, book_id, expected, change)
    return updated_entry


@router.delete("/api/v1/lorebooks/{book_id}/entries/{uid}")
async def delete_entry(book_id: str, uid: int, container: AppContainer = Depends(get_container)):
    b = container.lorebooks.get(book_id)
    if b is None:
        raise HTTPException(404, f"世界书不存在: {book_id}")
    expected = b.revision
    def change(draft: Lorebook) -> None:
        old_count = len(draft.entries)
        draft.entries = [entry for entry in draft.entries if entry.uid != uid]
        if old_count == len(draft.entries):
            raise ValueError(f"条目不存在: {uid}")
    await _revise(container, book_id, expected, change)
    return {"ok": True}


@router.post("/api/v1/lorebooks")
async def create_lorebook(req: CreateLorebookReq, container: AppContainer = Depends(get_container)):
    """R29.2：一键入库（工坊确认后保存）。"""
    await container.save_lorebook(req.book)
    return req.book.model_dump(mode="json")


@router.post("/api/v1/lorebooks/blank")
async def create_blank_lorebook(
    req: CreateBlankLorebookReq, container: AppContainer = Depends(get_container)
):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "世界书名称不能为空")
    book = Lorebook(name=name, description=req.description.strip(), tags=list(dict.fromkeys(tag.strip() for tag in req.tags if tag.strip())))
    await container.save_lorebook(book)
    return book.model_dump(mode="json")


@router.post("/api/v1/lorebooks/{book_id}/copy")
async def copy_lorebook(
    book_id: str, req: CopyLorebookReq, container: AppContainer = Depends(get_container)
):
    source = container.lorebooks.get(book_id)
    if source is None:
        raise HTTPException(404, f"世界书不存在: {book_id}")
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "世界书名称不能为空")
    book = source.model_copy(deep=True, update={
        "id": new_id("book"),
        "source_asset_id": source.id,
        "source_asset_revision": source.revision,
        "revision": 1,
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "name": name,
        "description": source.description if req.description is None else req.description.strip(),
        "tags": source.tags if req.tags is None else list(dict.fromkeys(tag.strip() for tag in req.tags if tag.strip())),
    })
    await container.save_lorebook(book)
    return book.model_dump(mode="json")


@router.delete("/api/v1/lorebooks/{book_id}")
async def delete_lorebook(book_id: str, container: AppContainer = Depends(get_container)):
    book = container.lorebooks.get(book_id)
    if book is None:
        raise HTTPException(404, f"世界书不存在: {book_id}")

    references = [
        f"角色「{character.card.name}」"
        for character in container.characters.values()
        if book_id in character.bound_lorebook_ids
    ]
    references.extend(
        f"世界「{world.title}」"
        for world in container.worlds.values() if book_id in world.lorebook_ids
    )
    for summary in await container.sessions.list_summaries():
        runner = container.runners.get(summary.id)
        state = runner.state if runner is not None else await container.sessions.load_state(summary.id)
        if state is not None and book_id in state.meta.lorebook_ids:
            references.append(f"会话「{state.meta.title or summary.title}」")
    for summary in await container.saves.list():
        saved = await container.saves.load(summary.id)
        if saved is not None and book_id in saved.state.meta.lorebook_ids:
            references.append(f"存档「{summary.name or summary.id}」")

    if references:
        joined = "、".join(references[:5])
        more = f"等 {len(references)} 处" if len(references) > 5 else ""
        raise HTTPException(409, f"该世界书仍被{joined}{more}引用，请先解除绑定")

    await container.lorebook_registry.delete(book_id)
    return {"ok": True}


@router.get("/api/v1/lorebooks/{book_id}/export")
async def export_lorebook(book_id: str, container: AppContainer = Depends(get_container)):
    """R29.4：导出 ST 格式 JSON（硬标准：v1 导入器无损往返）。"""
    from mrp.importers.exporters import export_lorebook_st

    book = container.lorebooks.get(book_id)
    if book is None:
        raise HTTPException(404, f"世界书不存在: {book_id}")
    return Response(
        export_lorebook_st(book),
        media_type="application/json",
        headers={"Content-Disposition": content_disposition(book.name, ".json", fallback=book_id)},
    )
