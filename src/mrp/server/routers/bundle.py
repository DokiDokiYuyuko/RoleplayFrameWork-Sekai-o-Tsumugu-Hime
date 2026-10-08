"""F10.3 迁移包路由（v5 首发切片）：整包导入/导出。"""
from __future__ import annotations

import posixpath
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from mrp.importers.character_card import save_avatar_png
from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.shared.models import Character, CharacterAuthoringSource, ModelConfig, new_id, utcnow
from mrp.storage.avatar_ref import AvatarLoad, load_bundle_avatar
from mrp.storage.naming import content_disposition

router = APIRouter()


class SelectedAssetReq(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)


class SelectedBundleReq(BaseModel):
    characters: list[SelectedAssetReq] = Field(default_factory=list, max_length=200)
    lorebooks: list[SelectedAssetReq] = Field(default_factory=list, max_length=200)
    include_bound_lorebooks: bool = True


def _selected_assets(container: AppContainer, req: SelectedBundleReq, *, check_revisions: bool = True):
    if not req.characters and not req.lorebooks:
        raise HTTPException(400, "请先选择角色或世界书")
    characters = []
    books = []
    requested_books = {item.id for item in req.lorebooks}
    for refs, registry, result, label in (
        (req.characters, container.characters, characters, "角色"),
        (req.lorebooks, container.lorebooks, books, "世界书"),
    ):
        if len({item.id for item in refs}) != len(refs):
            raise HTTPException(400, f"{label}选择有重复，请重新选择")
        for ref in refs:
            asset = registry.get(ref.id)
            if asset is None:
                raise HTTPException(409, f"所选{label}已不存在，请刷新后重新选择")
            if check_revisions and asset.revision != ref.expected_revision:
                title = asset.card.name if label == "角色" else asset.name
                raise HTTPException(409, f"{label}「{title}」已更新，请重新预览后导出")
            result.append(asset.model_copy(deep=True))
    if req.include_bound_lorebooks:
        included = {book.id for book in books}
        for character in characters:
            for book_id in character.bound_lorebook_ids:
                if book_id in included:
                    continue
                book = container.lorebooks.get(book_id)
                if book is None:
                    raise HTTPException(409, f"角色「{character.card.name}」绑定的世界书已缺失，请先修复绑定")
                books.append(book.model_copy(deep=True))
                included.add(book_id)
    included = {book.id for book in books}
    for character in characters:
        character.bound_lorebook_ids = [book_id for book_id in character.bound_lorebook_ids if book_id in included]
        character.source_world_id = None
    avatars = _bundle_avatar_map(container, characters, strict=True)
    if len(characters) + len(books) + len(avatars) > 200:
        raise HTTPException(400, "所选素材及头像超过迁移包的 200 条导入上限，请减少选择或分批导出")
    return characters, books, requested_books, avatars


def _bundle_avatar_map(container: AppContainer, characters, *, strict: bool) -> dict[str, bytes]:
    """标准头像文件算一张，不看 avatar_path 是否已填。strict 时坏路径直接失败。"""
    avatars: dict[str, bytes] = {}
    root = container.paths.characters_dir
    for character in characters:
        loaded = load_bundle_avatar(root, character.id, character.card.avatar_path)
        if loaded.status == AvatarLoad.FOUND and loaded.data:
            avatars[character.id] = loaded.data
            continue
        if not strict:
            continue
        if loaded.status == AvatarLoad.INVALID:
            raise HTTPException(409, f"角色「{character.card.name}」的头像路径无效，请修复后重新导出")
        if loaded.status == AvatarLoad.UNREADABLE:
            raise HTTPException(409, f"角色「{character.card.name}」的头像无法读取，请修复后重新导出")
    return avatars


def _selected_preview(container: AppContainer, req: SelectedBundleReq, *, check_revisions: bool = True):
    from mrp.importers.bundle import share_character_copies, share_lorebook_copies
    characters, books, requested_books, avatars = _selected_assets(container, req, check_revisions=check_revisions)
    share_books, exclusions = share_lorebook_copies(books)
    _, embedded_exclusions = share_character_copies(characters)
    for key, value in embedded_exclusions.items():
        exclusions[key] += value
    notices = ["只包含清单中的角色、头像与世界书。", "不包含私人创作原稿、世界原稿、角色全身图、故事与记忆。",
               f"将排除 {exclusions['author_entries']} 条作者专用词条（含角色内嵌世界书）。",
               f"将移除 {exclusions['provenance_entries']} 条词条的原稿引用与整理记录（含角色内嵌世界书）；可分享正文、编号与规则保留。",
               "此包不包含世界；角色的来源世界关系会清空，本地角色与世界书不变。"]
    for original, shared in zip(books, share_books):
        if original.entries and not shared.entries:
            notices.append(f"世界书「{original.name}」分享后没有词条，包内仍保留书名、标签与设置。")
    return characters, books, {
        "items": [{"kind": "character", "id": item.id, "title": item.card.name, "revision": item.revision, "dependency": False} for item in characters]
        + [{"kind": "lorebook", "id": item.id, "title": item.name, "revision": item.revision, "dependency": item.id not in requested_books} for item in books],
        "notices": notices,
        "exclusions": exclusions,
        "avatars": len(avatars),
    }, avatars


@router.post("/api/v1/bundle/export-selected/preview")
async def preview_selected_bundle(req: SelectedBundleReq, container: AppContainer = Depends(get_container)):
    return _selected_preview(container, req, check_revisions=False)[2]


@router.post("/api/v1/bundle/export-selected")
async def export_selected_bundle(req: SelectedBundleReq, container: AppContainer = Depends(get_container)):
    from mrp.importers.bundle import build_bundle
    characters, books, _, avatars = _selected_preview(container, req)
    return Response(build_bundle(characters, books, avatars=avatars, share=True), media_type="application/zip",
                    headers={"Content-Disposition": content_disposition("织界之姬-所选角色与世界书", ".zip", fallback="selected-assets")})


async def _apply_bundle(container: AppContainer, entries: list) -> dict:
    """把 scan_bundle 的条目落盘（世界书 → 角色 → 头像资产），返回逐项报告。

    冲突策略：同 id 已存在 → 换新 id 导入（绝不静默覆盖现有内容）。
    """
    book_map: dict[str, str] = {}
    char_map: dict[str, str] = {}
    pending_avatars: list[tuple[str, bytes]] = []
    out: dict[str, list] = {"characters": [], "lorebooks": [], "errors": [], "skipped": []}

    # 1) 世界书先建（角色绑定才好按旧 id 重映射）
    for e in entries:
        if e.kind != "lorebook":
            continue
        if e.error or e.lorebook is None:
            out["errors"].append({"filename": e.filename, "error": e.error or "世界书缺失"})
            continue
        book = e.lorebook
        old_id = book.id
        if old_id in container.lorebooks:
            book.id = new_id("book")
        side = e.sidecar or {}
        book.source_asset_id = str(side.get("asset_id") or old_id)
        book.source_asset_revision = side.get("revision") if isinstance(side.get("revision"), int) else None
        book.revision = 1
        known_sidecar = {"mrp", "asset_id", "schema_version", "revision", "name", "description", "tags",
                         "scan_depth", "token_budget", "recursive_scanning", "authoring_source"}
        for key, value in side.items():
            if key not in known_sidecar and key not in (book.model_extra or {}):
                setattr(book, key, value)
        if isinstance(side.get("name"), str) and side["name"]:
            book.name = side["name"]
        if isinstance(side.get("description"), str):
            book.description = side["description"]
        if isinstance(side.get("tags"), list):
            book.tags = [str(tag).strip() for tag in side["tags"] if str(tag).strip()][:30]
        for key in ("scan_depth", "token_budget", "recursive_scanning"):
            if isinstance(side.get(key), type(getattr(book, key))):
                setattr(book, key, side[key])
        await container.save_lorebook(book)
        book_map[old_id] = book.id
        if isinstance(side.get("asset_id"), str):
            book_map[side["asset_id"]] = book.id
        out["lorebooks"].append({"filename": e.filename, "name": book.name, "id": book.id,
                                 "import_report": getattr(book, "import_report", None)})

    # 2) 角色（PNG 卡自带图像直接落头像；纯头像资产延后按旧 id 关联）
    for e in entries:
        if e.kind == "unsupported" and e.avatar_png:
            parts = e.filename.split("/")
            pending_avatars.append((parts[-2] if len(parts) >= 2 else "", e.avatar_png))
            continue
        if e.kind != "character":
            if e.error:
                out["errors"].append({"filename": e.filename, "error": e.error})
            elif e.kind == "unsupported":
                out["skipped"].append({"filename": e.filename, "reason": "非角色/世界书内容"})
            continue
        if e.error or e.card is None:
            out["errors"].append({"filename": e.filename, "error": e.error or "角色卡缺失"})
            continue
        c = Character(card=e.card)
        side = e.sidecar or {}
        source_path_id = posixpath.basename(e.filename)
        if source_path_id.endswith(".json"):
            source_path_id = source_path_id[:-5]
        c.source_asset_id = str(side.get("asset_id") or source_path_id)
        c.source_asset_revision = side.get("revision") if isinstance(side.get("revision"), int) else None
        warnings: list[str] = []
        if side.get("authoring_source") is not None:
            try:
                c.authoring_source = CharacterAuthoringSource.model_validate(side["authoring_source"])
            except (ValueError, TypeError):
                out["errors"].append({"filename": e.filename, "error": "私人创作原稿格式无效，未导入此角色；请核对迁移包后重试"})
                continue
        if isinstance(side.get("source_world_id"), str) and side["source_world_id"]:
            # This migration format carries metadata, but no worlds to remap.
            warnings.append("此包未包含世界，来源世界关系无法恢复，请为导入角色重新指定来源世界")
        if isinstance(side.get("aliases"), list):
            c.aliases = [str(a).strip() for a in side["aliases"] if str(a).strip()]
        if isinstance(side.get("talkativeness"), (int, float)):
            c.talkativeness = max(0.0, min(1.0, float(side["talkativeness"])))
        for key in ("interject_enabled", "followup_enabled"):
            if isinstance(side.get(key), bool):
                setattr(c, key, side[key])
        if isinstance(side.get("llm"), dict):
            try:
                c.llm = ModelConfig.model_validate(side["llm"])
            except Exception:  # noqa: BLE001 —— 坏采样配置回落默认
                pass
        if isinstance(side.get("bound_lorebook_ids"), list):
            c.bound_lorebook_ids = [
                book_map[b] for b in side["bound_lorebook_ids"] if b in book_map
            ]
        known_sidecar = {"mrp", "asset_id", "schema_version", "revision", "aliases", "talkativeness",
                         "interject_enabled", "followup_enabled", "llm", "bound_lorebook_ids", "source_world_id", "authoring_source"}
        for key, value in side.items():
            if key not in known_sidecar and key not in (c.model_extra or {}):
                setattr(c, key, value)
        if e.avatar_png:
            try:
                c.card.avatar_path = save_avatar_png(
                    e.avatar_png, c.id, container.paths.characters_dir
                )
            except Exception:  # noqa: BLE001 —— 头像落盘失败不影响卡片导入
                pass
        await container.save_character(c)
        stem = posixpath.basename(e.filename)
        char_map[stem[: -len(".json")] if stem.endswith(".json") else stem] = c.id
        out["characters"].append({"filename": e.filename, "name": c.card.name, "id": c.id,
                                  "warnings": warnings,
                                  "import_report": getattr(c.card, "import_report", None)})

    # 3) 待挂头像（characters/<旧 id>/avatar.png）
    for old_id, png in pending_avatars:
        target = char_map.get(old_id) or (old_id if old_id in container.characters else "")
        if not target:
            out["skipped"].append(
                {"filename": f"characters/{old_id}/avatar.png", "reason": "未找到对应角色"}
            )
            continue
        try:
            c = container.characters[target]
            c.card.avatar_path = save_avatar_png(png, c.id, container.paths.characters_dir)
            c.revision += 1
            c.updated_at = utcnow()
            await container.save_character(c)
        except Exception:  # noqa: BLE001 —— 头像落盘失败跳过
            pass
    return {"ok": True, **out}


@router.post("/api/v1/bundle/import")
async def import_bundle_api(file: UploadFile, container: AppContainer = Depends(get_container)):
    """迁移包导入（F10.3）：zip → 逐条目落盘，返回逐项报告；坏包/空包 400。"""
    from mrp.importers.bundle import scan_bundle

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    if len(raw) > 50 * 1024 * 1024:
        raise HTTPException(400, "迁移包超过 50MB")
    entries = scan_bundle(raw)
    if not entries:
        raise HTTPException(400, "不是有效的迁移包（无内容或不是 zip）")
    return await _apply_bundle(container, entries)


@router.get("/api/v1/bundle/export")
async def export_bundle_api(container: AppContainer = Depends(get_container)):
    """私密迁移：角色库、角色原稿、世界书与头像；世界实体不在此格式内。"""
    from mrp.importers.bundle import build_bundle

    avatars = _bundle_avatar_map(container, list(container.characters.values()), strict=False)
    data = build_bundle(
        list(container.characters.values()), list(container.lorebooks.values()), avatars=avatars
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"世界を紡ぐ姫-角色与世界书-{stamp}", ".zip", fallback="sekai-o-tsumugu-hime-library")},
    )
