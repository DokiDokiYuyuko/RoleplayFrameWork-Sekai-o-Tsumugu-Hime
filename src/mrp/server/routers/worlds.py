"""World library: long-form archive and links to existing runtime lorebooks."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.shared.models import Lorebook, new_id, utcnow
from mrp.storage.naming import content_disposition
from mrp.worlds.biology_import import apply_biology_import
from mrp.worlds.schema import ArchiveRecord, World
from mrp.worlds.covers import cover_catalog, cover_image

router = APIRouter()
def _get(container: AppContainer, world_id: str) -> World:
    world = container.worlds.get(world_id)
    if world is None:
        raise HTTPException(404, "世界不存在")
    return world


async def _revise(container: AppContainer, world_id: str, expected: int, change) -> World:
    try:
        return await container.world_registry.revise(world_id, expected, change)
    except KeyError as exc:
        raise HTTPException(404, "世界不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc)) from exc


class CreateWorldReq(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    cover_id: str | None = Field(None, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    core_brief: str = ""
    runtime_policy: Literal["legacy_full", "raw", "compiled"] = "raw"
    manuscript_body: str | None = None
    manuscript_visibility: Literal["public", "private"] = "public"


class PatchWorldReq(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    cover_id: str | None = Field(None, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    core_brief: str | None = None
    archived: bool | None = None
    runtime_policy: Literal["legacy_full", "raw", "compiled"] | None = None


class ManuscriptReq(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str = Field("世界原稿", min_length=1, max_length=200)
    body: str
    visibility: Literal["public", "private"] = "private"


class ArchivePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["background", "biology"]
    subtype: str = ""
    title: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)
    summary: str = Field("", max_length=5000)
    body: str = ""
    visibility: Literal["public", "private"] = "public"
    kind_data: dict[str, Any] = Field(default_factory=dict)


class CreateArchiveReq(BaseModel):
    expected_revision: int = Field(ge=1)
    record: ArchivePayload


class PatchArchiveReq(BaseModel):
    expected_revision: int = Field(ge=1)
    record: ArchivePayload


class ImportBiologyItem(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    mode: Literal["copy", "overwrite"]


class ImportBiologyReq(BaseModel):
    expected_revision: int = Field(ge=1)
    source_world_id: str = Field(min_length=1, max_length=200)
    items: list[ImportBiologyItem] = Field(min_length=1, max_length=200)


class ExpectedRevisionReq(BaseModel):
    expected_revision: int = Field(ge=1)


class LinkLorebookReq(ExpectedRevisionReq):
    book_id: str


class ImportWorldReq(BaseModel):
    format: Literal["mrp.world"]
    version: Literal[1]
    world: World
    lorebooks: list[Lorebook] = Field(default_factory=list, max_length=100)


@router.get("/api/v1/worlds")
async def list_worlds(container: AppContainer = Depends(get_container)):
    return sorted(
        (world.summary() for world in container.worlds.values()),
        key=lambda item: item["updated_at"], reverse=True,
    )


@router.get("/api/v1/world-covers")
async def list_world_covers(container: AppContainer = Depends(get_container)):
    return [{key: value for key, value in row.items() if key != "filename"}
            for row in cover_catalog(container.data_root)]


@router.get("/api/v1/world-covers/{cover_id}/image")
async def get_world_cover_image(cover_id: str, container: AppContainer = Depends(get_container)):
    path = cover_image(container.data_root, cover_id)
    if path is None:
        raise HTTPException(404, "封面图片不存在")
    media_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[path.suffix.lower()]
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.get("/api/v1/worlds/lorebook-owners")
async def lorebook_owners(container: AppContainer = Depends(get_container)):
    return {
        book_id: {"world_id": world.id, "world_title": world.title}
        for world in container.worlds.values() for book_id in world.lorebook_ids
    }


@router.post("/api/v1/worlds")
async def create_world(req: CreateWorldReq, container: AppContainer = Depends(get_container)):
    if req.cover_id and cover_image(container.data_root, req.cover_id) is None:
        raise HTTPException(400, "所选封面不存在，请重新选择")
    world = World(
        title=req.title.strip(), description=req.description.strip(),
        core_brief=req.core_brief.strip(),
        cover_id=req.cover_id,
        runtime_policy=req.runtime_policy,
    )
    if not world.title:
        raise HTTPException(400, "世界名称不能为空")
    if req.manuscript_body is not None:
        record = ArchiveRecord(kind="background", subtype="原稿", title="世界原稿", body=req.manuscript_body,
                               visibility=req.manuscript_visibility, kind_data={"section": "overview"})
        world.archive_records.append(record)
        world.manuscript_archive_id = record.id
    await container.world_registry.create(world)
    return world.model_dump(mode="json")


@router.get("/api/v1/worlds/{world_id}")
async def get_world(world_id: str, container: AppContainer = Depends(get_container)):
    return _get(container, world_id).model_dump(mode="json")


@router.patch("/api/v1/worlds/{world_id}")
async def patch_world(world_id: str, req: PatchWorldReq, container: AppContainer = Depends(get_container)):
    if req.cover_id and cover_image(container.data_root, req.cover_id) is None:
        raise HTTPException(400, "所选封面不存在，请重新选择")

    def change(world: World) -> None:
        if req.title is not None:
            if not req.title.strip():
                raise ValueError("世界名称不能为空")
            world.title = req.title.strip()
        if req.description is not None:
            world.description = req.description.strip()
        if "cover_id" in req.model_fields_set:
            world.cover_id = req.cover_id
        if req.core_brief is not None:
            world.core_brief = req.core_brief.strip()
        if req.archived is not None:
            world.archived = req.archived
        if req.runtime_policy is not None:
            world.runtime_policy = req.runtime_policy

    world = await _revise(container, world_id, req.expected_revision, change)
    return world.model_dump(mode="json")


@router.post("/api/v1/worlds/{world_id}/manuscript")
async def save_manuscript(world_id: str, req: ManuscriptReq, container: AppContainer = Depends(get_container)):
    """Save exact author prose and its runtime scope without calling a model."""
    def change(world: World) -> None:
        if world.archived:
            raise ValueError("归档世界不能修改原稿")
        old = next((row for row in world.archive_records if row.id == world.manuscript_archive_id), None)
        if old is None:
            record = ArchiveRecord(kind="background", subtype="原稿", title=req.title,
                                   body=req.body, visibility=req.visibility,
                                   kind_data={"section": "overview"})
            world.archive_records.append(record)
            world.manuscript_archive_id = record.id
        else:
            old.body = req.body
            old.title = req.title
            old.visibility = req.visibility
            old.revision += 1
            old.updated_at = utcnow()
    world = await _revise(container, world_id, req.expected_revision, change)
    return world.model_dump(mode="json")


@router.post("/api/v1/worlds/{world_id}/archive")
async def create_archive(world_id: str, req: CreateArchiveReq, container: AppContainer = Depends(get_container)):
    try:
        record = ArchiveRecord.model_validate(req.record.model_dump(mode="python"))
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    world = await _revise(
        container, world_id, req.expected_revision,
        lambda draft: draft.archive_records.append(record),
    )
    return world.model_dump(mode="json")


@router.post("/api/v1/worlds/{world_id}/archive/import-biology")
async def import_biology(world_id: str, req: ImportBiologyReq, container: AppContainer = Depends(get_container)):
    source = _get(container, req.source_world_id)

    def change(world: World) -> None:
        apply_biology_import(world, source, [(item.id, item.mode) for item in req.items])

    world = await _revise(container, world_id, req.expected_revision, change)
    return world.model_dump(mode="json")


@router.patch("/api/v1/worlds/{world_id}/archive/{record_id}")
async def patch_archive(world_id: str, record_id: str, req: PatchArchiveReq, container: AppContainer = Depends(get_container)):
    def change(world: World) -> None:
        index = next((i for i, item in enumerate(world.archive_records) if item.id == record_id), None)
        if index is None:
            raise ValueError("档案条目不存在")
        old = world.archive_records[index]
        if req.record.kind != old.kind:
            raise ValueError("档案类别不能直接改变；请新建对应类别的条目")
        # The editor payload does not carry provenance. Keep the old copy link
        # even when a client sends these keys as null.
        record = ArchiveRecord.model_validate({
            **req.record.model_dump(mode="python"),
            "id": old.id, "revision": old.revision + 1,
            "created_at": old.created_at, "updated_at": utcnow(),
            "copied_from_world_id": old.copied_from_world_id,
            "copied_from_archive_id": old.copied_from_archive_id,
            "copied_from_revision": old.copied_from_revision,
        })
        world.archive_records[index] = record

    world = await _revise(container, world_id, req.expected_revision, change)
    return world.model_dump(mode="json")


@router.delete("/api/v1/worlds/{world_id}/archive/{record_id}")
async def delete_archive(world_id: str, record_id: str, req: ExpectedRevisionReq, container: AppContainer = Depends(get_container)):
    def change(world: World) -> None:
        if not any(item.id == record_id for item in world.archive_records):
            raise ValueError("档案条目不存在")
        world.archive_records = [item for item in world.archive_records if item.id != record_id]
        if world.manuscript_archive_id == record_id:
            world.manuscript_archive_id = None

    world = await _revise(container, world_id, req.expected_revision, change)
    return world.model_dump(mode="json")


@router.post("/api/v1/worlds/{world_id}/lorebooks")
async def link_lorebook(world_id: str, req: LinkLorebookReq, container: AppContainer = Depends(get_container)):
    if req.book_id not in container.lorebooks:
        raise HTTPException(404, "世界书不存在")

    def change(world: World) -> None:
        owner = next(
            (item.title for item in container.worlds.values()
             if item.id != world.id and req.book_id in item.lorebook_ids), None,
        )
        if owner is not None:
            raise ValueError(f"这本世界书已属于「{owner}」，请先复制再关联")
        if req.book_id not in world.lorebook_ids:
            world.lorebook_ids.append(req.book_id)
        else:
            raise ValueError("这本世界书已关联到当前世界")

    world = await _revise(container, world_id, req.expected_revision, change)
    return world.model_dump(mode="json")


@router.delete("/api/v1/worlds/{world_id}/lorebooks/{book_id}")
async def unlink_lorebook(world_id: str, book_id: str, req: ExpectedRevisionReq, container: AppContainer = Depends(get_container)):
    world = await _revise(
        container, world_id, req.expected_revision,
        lambda draft: draft.lorebook_ids.remove(book_id) if book_id in draft.lorebook_ids else None,
    )
    return world.model_dump(mode="json")


@router.get("/api/v1/worlds/{world_id}/export")
async def export_world(world_id: str, container: AppContainer = Depends(get_container)):
    world = _get(container, world_id)
    payload = {
        "format": "mrp.world", "version": 1,
        "world": world.model_dump(mode="json"),
        "lorebooks": [container.lorebooks[book_id].model_dump(mode="json")
                      for book_id in world.lorebook_ids if book_id in container.lorebooks],
    }
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": content_disposition(world.title, ".world.json", fallback=world.id)},
    )


@router.post("/api/v1/worlds/import")
async def import_world(req: ImportWorldReq, container: AppContainer = Depends(get_container)):
    book_map: dict[str, str] = {}
    books: list[Lorebook] = []
    for source in req.lorebooks:
        if source.id in book_map:
            raise HTTPException(400, "导入包中的世界书 ID 重复")
        book = source.model_copy(deep=True)
        book.source_asset_id = source.id
        book.source_asset_revision = source.revision
        book.id = new_id("lb")
        book.revision = 1
        book.created_at = utcnow()
        book.updated_at = book.created_at
        book_map[source.id] = book.id
        books.append(book)
    if set(req.world.lorebook_ids) - set(book_map):
        raise HTTPException(400, "导入包缺少世界关联的世界书")
    world = req.world.model_copy(deep=True)
    world.source_asset_id = req.world.id
    world.source_asset_revision = req.world.revision
    world.id = new_id("world")
    world.revision = 1
    world.lorebook_ids = [book_map[book_id] for book_id in req.world.lorebook_ids]
    world.created_at = utcnow()
    world.updated_at = world.created_at
    for book in books:
        for entry in book.entries:
            source = entry.extensions.get("mrp.archive_source")
            if isinstance(source, dict) and source.get("world_id") == req.world.id:
                source["world_id"] = world.id
    saved_books: list[Lorebook] = []
    try:
        for book in books:
            await container.save_lorebook(book)
            saved_books.append(book)
        await container.world_registry.create(world)
    except Exception:
        for book in saved_books:
            await container.lorebook_registry.delete(book.id)
        raise
    return world.model_dump(mode="json")
