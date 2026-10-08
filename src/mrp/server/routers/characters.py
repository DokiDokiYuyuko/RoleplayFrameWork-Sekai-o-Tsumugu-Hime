"""角色路由：导入 / 列表 / 详情 / 补丁 / 删除 / 头像 / 导出（R1.x / R6.2 / R31）。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, Form
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from mrp.importers.character_card import import_card_json, import_card_png, save_avatar_png
from mrp.importers.lorebook import import_lorebook
from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.shared.models import Character, CharacterCard, CharacterAuthoringSource, utcnow
from mrp.storage.avatar_ref import read_display_avatar
from mrp.storage.naming import content_disposition
from mrp.storage.paths import is_safe_name

logger = logging.getLogger("mrp.server")

router = APIRouter()


def _character_write_lock(container, character_id):
    locks = getattr(container, "_character_media_locks", None)
    if locks is None:
        locks = container._character_media_locks = {}
    return locks.setdefault(character_id, asyncio.Lock())



class PatchCharacterReq(BaseModel):
    expected_revision: int | None = Field(None, ge=1)
    source_world_id: str | None = None
    authoring_source: CharacterAuthoringSource | None = None
    aliases: list[str] | None = Field(None, max_length=20)
    talkativeness: float | None = None
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    card: dict[str, Any] | None = None  # 局部卡片（六基础字段+alternate_greetings/system_prompt/post_history_instructions/tags）
    sampling: dict[str, Any] | None = None  # 并入 llm.sampling（max_tokens 等）
    interject_enabled: bool | None = None  # R38.1 库级默认（新会话继承）
    followup_enabled: bool | None = None  # R38.2 库级默认


@router.post("/api/v1/characters/import")
async def import_character(file: UploadFile, container: AppContainer = Depends(get_container),
                           source_world_id: str | None = Form(None)):
    if source_world_id and source_world_id not in container.worlds:
        raise HTTPException(400, "来源世界不存在")
    raw = await file.read()
    if file.filename and file.filename.lower().endswith(".png"):
        try:
            card = import_card_png(raw)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    else:
        try:
            card = import_card_json(json.loads(raw.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise HTTPException(400, f"JSON 解析失败: {e}") from e
    character = Character(card=card, source_world_id=source_world_id or None)
    if file.filename and file.filename.lower().endswith(".png"):
        character.card.avatar_path = save_avatar_png(raw, character.id, container.paths.characters_dir)
    await container.save_character(character)
    # ADR-0004：提取内嵌 character_book 为独立书并绑定（原文保留在 extensions）
    embedded = card.extensions.get("character_book")
    if isinstance(embedded, dict) and embedded.get("entries"):
        book = import_lorebook(embedded)
        book.name = f"{card.name}·内嵌书"
        book.source_format = "embedded"
        for category, rows in book.import_report.items():
            card.import_report[category].extend(f"内嵌世界书：{row}" for row in rows)
        await container.save_lorebook(book)
        character.bound_lorebook_ids.append(book.id)
        await container.save_character(character)
    return character.model_dump(mode="json")


@router.post("/api/v1/characters/import-preview")
async def preview_character(file: UploadFile):
    """Read-only compatibility preflight, including the embedded world book."""
    raw = await file.read(10_000_001)
    if len(raw) > 10_000_000:
        raise HTTPException(413, "角色卡文件过大")
    try:
        card = (import_card_png(raw) if (file.filename or "").lower().endswith(".png")
                else import_card_json(json.loads(raw.decode("utf-8"))))
    except (ValueError, UnicodeDecodeError, OSError) as exc:
        raise HTTPException(400, f"角色卡解析失败: {exc}") from exc
    report = dict(card.import_report)
    embedded = card.extensions.get("character_book")
    if isinstance(embedded, dict):
        book = import_lorebook(embedded)
        for category, rows in book.import_report.items():
            report[category] = [*report[category], *(f"内嵌世界书：{row}" for row in rows)]
    return {"draft": card.model_dump(mode="json"), **report}


@router.get("/api/v1/characters")
async def list_characters(container: AppContainer = Depends(get_container)):
    return [c.model_dump(mode="json") for c in container.characters.values()]


@router.get("/api/v1/characters/{character_id}")
async def get_character(character_id: str, container: AppContainer = Depends(get_container)):
    c = container.characters.get(character_id)
    if c is None:
        raise HTTPException(404, f"角色不存在: {character_id}")
    return c.model_dump(mode="json")


@router.delete("/api/v1/characters/{character_id}")
async def delete_character(character_id: str, container: AppContainer = Depends(get_container)):
    """删除角色（库级，R50）：卡片 + 头像目录 + 该角色的记忆记录。

    会话与存档内嵌的是角色**快照**，不受库级删除影响（已开对话照常继续）。
    """
    async with _character_write_lock(container, character_id):
        if character_id not in container.characters:
            raise HTTPException(404, f"角色不存在: {character_id}")
        await container.character_registry.delete(character_id, avatar=True)  # 卡片 + 头像目录
    purged = 0
    try:
        # 同步 SQLite（记录删除 + 镜像重写）→ 丢线程池
        purged = await asyncio.to_thread(container.memory_store.purge_unscoped_character, character_id)
    except Exception:  # noqa: BLE001 —— 记忆清理失败不阻塞角色删除
        logger.warning("角色记忆清理失败（不影响删除）: %s", character_id, exc_info=True)
    return {"ok": True, "purged_memories": purged}


@router.patch("/api/v1/characters/{character_id}")
async def patch_character(
    character_id: str, req: PatchCharacterReq, container: AppContainer = Depends(get_container)
):
    async with _character_write_lock(container, character_id):
        c = container.characters.get(character_id)
        if c is None:
            raise HTTPException(404, f"角色不存在: {character_id}")
        if req.expected_revision is not None and req.expected_revision != c.revision:
            raise HTTPException(409, "角色已在另一处更新，请刷新后重试")
        if "source_world_id" in req.model_fields_set:
            if req.source_world_id and req.source_world_id not in container.worlds:
                raise HTTPException(400, "来源世界不存在")
        c = c.model_copy(deep=True)
        if "source_world_id" in req.model_fields_set:
            c.source_world_id = req.source_world_id or None
        if "authoring_source" in req.model_fields_set:
            c.authoring_source = req.authoring_source
        if req.aliases is not None:
            c.aliases = req.aliases
        if req.talkativeness is not None:
            c.talkativeness = max(0.0, min(1.0, req.talkativeness))
        if req.interject_enabled is not None:  # R38 库级默认
            c.interject_enabled = req.interject_enabled
        if req.followup_enabled is not None:
            c.followup_enabled = req.followup_enabled
        if req.model is not None:
            c.llm.model = req.model.strip()
            c.llm.inherit_model = not c.llm.model
        if req.base_url is not None:
            c.llm.base_url = req.base_url.strip().rstrip("/")
            c.llm.inherit_base_url = not c.llm.base_url
        if req.api_key_env is not None:
            c.llm.api_key_env = req.api_key_env
        if req.card:
            editable = set(CharacterCard.model_fields) - {
                "avatar_path", "spec", "spec_version", "source_format"
            }
            merged = c.card.model_dump(mode="python")
            merged.update({key: value for key, value in req.card.items() if key in editable})
            try:
                c.card = CharacterCard.model_validate(merged)
            except ValueError as exc:
                raise HTTPException(400, f"角色卡格式错误: {exc}") from exc
        if req.sampling is not None:
            c.llm.sampling = {**c.llm.sampling, **req.sampling}
        c.revision += 1
        c.updated_at = utcnow()
        await container.save_character(c)
        return c.model_dump(mode="json")


@router.get("/api/v1/characters/{character_id}/avatar")
async def character_avatar(character_id: str, container: AppContainer = Depends(get_container)):
    avatar = container.paths.characters_dir / character_id / "avatar.png"
    if not avatar.is_file():
        raise HTTPException(404, "该角色无头像")
    return FileResponse(
        avatar,
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/api/v1/characters/{character_id}/full-body")
async def character_full_body(character_id: str, container: AppContainer = Depends(get_container)):
    """读取角色卡的全身立绘侧车文件。"""
    if not is_safe_name(character_id) or character_id not in container.characters:
        raise HTTPException(404, "角色不存在")
    full_body = container.paths.characters_dir / character_id / "full_body.png"
    if not full_body.is_file():
        raise HTTPException(404, "该角色暂无全身立绘")
    return FileResponse(
        full_body,
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )


async def _upload_character_media(character_id, file, container, kind, expected_revision):
    from mrp.storage.character_media import normalize_image, write_image_atomic, preserve_image_version
    limit = (8 if kind == "avatar" else 20) * 1024 * 1024
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(413, f"图片超过 {limit // (1024 * 1024)}MB")
    try:
        image = await asyncio.to_thread(normalize_image, raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    async with _character_write_lock(container, character_id):
        c = container.characters.get(character_id)
        if c is None or not is_safe_name(character_id):
            raise HTTPException(404, "角色不存在")
        if expected_revision is not None and expected_revision != c.revision:
            raise HTTPException(409, "角色已在另一处更新，请重新读取角色后再上传")
        filename = "avatar.png" if kind == "avatar" else "full_body.png"
        dest = container.paths.characters_dir / character_id / filename
        previous = dest.read_bytes() if dest.is_file() else None
        await asyncio.to_thread(preserve_image_version, dest.parent, f"{kind}-original", raw)
        if previous is not None:
            await asyncio.to_thread(preserve_image_version, dest.parent, f"{kind}-previous", previous)
        await asyncio.to_thread(write_image_atomic, dest, image)
        if kind == "avatar":
            updated = c.model_copy(deep=True)
            updated.card.avatar_path = f"{character_id}/avatar.png"
            updated.revision += 1
            updated.updated_at = utcnow()
            try:
                await container.save_character(updated)
            except BaseException:
                if previous is None:
                    dest.unlink(missing_ok=True)
                else:
                    await asyncio.to_thread(write_image_atomic, dest, previous)
                raise
            c = updated
        return {"avatar_path": c.card.avatar_path, "revision": c.revision,
                "media_kind": kind, "image_path": f"{character_id}/{filename}"}


@router.post("/api/v1/characters/{character_id}/avatar")
async def upload_avatar(character_id: str, file: UploadFile,
                        expected_revision: int | None = Form(None, ge=1),
                        container: AppContainer = Depends(get_container)):
    return await _upload_character_media(character_id, file, container, "avatar", expected_revision)


@router.post("/api/v1/characters/{character_id}/full-body")
async def upload_full_body(character_id: str, file: UploadFile,
                           expected_revision: int | None = Form(None, ge=1),
                           container: AppContainer = Depends(get_container)):
    return await _upload_character_media(character_id, file, container, "full-body", expected_revision)


@router.get("/api/v1/characters/{character_id}/export")
async def export_character(
    character_id: str, format: str = "json", container: AppContainer = Depends(get_container)
):
    """R31.9：导出 CCv2 JSON / PNG（与导入对称）。"""
    from mrp.importers.exporters import export_card_json, export_card_png

    c = container.characters.get(character_id)
    if c is None:
        raise HTTPException(404, f"角色不存在: {character_id}")
    name = c.card.name or character_id
    if format == "png":
        avatar = read_display_avatar(container.paths.characters_dir, c.id)
        return Response(
            export_card_png(c, avatar),
            media_type="image/png",
            headers={"Content-Disposition": content_disposition(name, ".png", fallback=character_id)},
        )
    return Response(
        export_card_json(c),
        media_type="application/json",
        headers={"Content-Disposition": content_disposition(name, ".json", fallback=character_id)},
    )
