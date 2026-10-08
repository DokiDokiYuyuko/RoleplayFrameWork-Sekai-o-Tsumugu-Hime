"""自包含迁移包（zip）：角色卡 + 世界书打包/扫描，用于换机、备份、批量迁移。

为什么是 zip：迁移场景要"一个文件带走全部内容"，而 zip 是标准库可读写的
通用容器，JSON 卡、PNG 卡、头像原字节都能原样装走。本模块只做
"bytes → 条目列表"与"条目列表 → bytes"的转换——不碰网络、不碰文件系统；
落盘、去重、建库（谁是新角色、头像写哪里）由 server 层接线决定，这样
打包/解包逻辑可单测、可复用。

包内布局（build_bundle 写出，scan_bundle 可读回）::

    manifest.json                  包元数据（版本/条目数/导出时间）
    characters/<id>.json           CCv2 卡面 JSON（export_card_json）
    characters/<id>.mrp.json       运行时侧车（别名/主动性/采样配置/世界书绑定）
    characters/<id>/avatar.png     可选，头像原始 PNG 字节
    lorebooks/<id>.json            ST 独立世界书 JSON（export_lorebook_st）
    lorebooks/<id>.mrp.json        世界书侧车（name/description/扫描参数）

侧车（2026-09-25 补）：ST 卡面格式不承载 mrp 运行时字段（别名用于 @提及匹配、
talkativeness、interject/followup 开关、llm 采样配置、绑定的世界书 id；世界书的
name/description 也不在 ST 格式里）。迁移包要"无损换机"，故另写一条同名
`*.mrp.json` 侧车，由 scan_bundle 挂到对应条目上（侧车自身不产生条目）。

扫描语义（刻意的取舍）：

- 单条失败只在该条上标 error，不影响其它条目——批量迁移不能因一张坏卡全盘失败；
- 坏 zip 返回 []（调用方据此提示"不是有效迁移包"，无需再包 try/except）；
- 角色卡与世界书的判定见 _scan_json_entry：卡面永远没有顶层 entries，
  ST 书永远没有 first_mes，两特征互斥；
- characters/<id>/avatar.png 是"头像载体"：能解析出卡就顺带产出卡，纯图片
  则给 unsupported + avatar_png（不报 error），避免自带包导出再导入时报坏；
- 根 manifest.json 是包自身元数据，不参与扫描（否则每次导入自带包都会
  多出一条"无法识别"的噪音条目）。
"""
from __future__ import annotations

import io
import json
import posixpath
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mrp.importers.character_card import import_card_json, import_card_png
from mrp.importers.exporters import export_card_json, export_lorebook_st
from mrp.importers.lorebook import import_lorebook
from mrp.shared.models import Character, CharacterCard, Lorebook

_BUNDLE_VERSION = 1
_MANIFEST_NAME = "manifest.json"
_SIDECAR_SUFFIX = ".mrp.json"  # 运行时侧车后缀（不产生独立条目）

# 角色卡识别字段：CCv1 平铺 / CCv2-v3 data 包装都会命中至少一个
_CARD_FIELDS = {
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "data",
    "spec",
}


@dataclass
class BundleEntry:
    """zip 内一个可导入条目（或一条失败记录）。

    失败条目也返回（error 非空），让接线层能把"哪张卡坏了、为什么"报给
    用户，而不是静默吞掉。
    """

    filename: str  # zip 内路径（报告/落盘归属用）
    kind: str  # "character" | "lorebook" | "unsupported"
    card: CharacterCard | None = None
    lorebook: Lorebook | None = None
    avatar_png: bytes | None = None  # PNG 原始字节：PNG 卡或 characters/<id>/avatar.png
    sidecar: dict | None = None  # 运行时侧车（角色：别名/采样…；世界书：name/description…）
    error: str | None = None


# ---------- 扫描 ----------


def scan_bundle(data: bytes, *, max_items: int = 200) -> list[BundleEntry]:
    """解析迁移包 zip → 逐条目结果；绝不抛异常（坏 zip/截断 → 返回 []）。

    跳过目录项、隐藏路径（`.` 开头，顺带挡下 zip-slip 的 `../`）、
    `__MACOSX/`、根 `manifest.json` 与 .json/.png 之外的扩展名。
    条目数达到 max_items 后只读取侧车，剩余内容不产生条目。
    """
    entries: list[BundleEntry] = []
    accepted_names: set[str] = set()
    sidecars: list[tuple[str, bytes]] = []  # (filename, raw)，第二遍挂到主条目
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return entries  # 不是 zip / 空字节：当作"不是迁移包"，不往上抛

    with zf:
        # 排序保证同一份包每次扫描顺序一致（报告与测试可比对）
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            name = info.filename
            if info.is_dir() or name.endswith("/"):
                continue
            parts = name.split("/")
            if any(p.startswith(".") or p == "__MACOSX" for p in parts):
                continue
            if name == _MANIFEST_NAME:
                continue  # 仅根：包自身元数据，不是用户内容
            ext = posixpath.splitext(name)[1].lower()
            if ext not in (".json", ".png"):
                continue
            is_sidecar = ext == ".json" and name.endswith(_SIDECAR_SUFFIX)
            if len(entries) >= max_items:
                base = name[: -len(_SIDECAR_SUFFIX)] if is_sidecar else ""
                if not is_sidecar or not ({f"{base}.json", f"{base}.png"} & accepted_names):
                    continue

            try:
                raw = zf.read(info)
            except Exception as exc:
                entries.append(BundleEntry(filename=name, kind="unsupported", error=f"条目读取失败: {exc}"))
                continue
            if is_sidecar:
                sidecars.append((name, raw))
                continue  # 侧车不是独立条目
            entries.append(_scan_json_entry(name, raw) if ext == ".json" else _scan_png_entry(name, raw))
            accepted_names.add(name)

    _attach_sidecars(entries, sidecars)
    return entries


def _attach_sidecars(entries: list[BundleEntry], sidecars: list[tuple[str, bytes]]) -> None:
    """把 `<base>.mrp.json` 侧车挂到同基名的条目上；坏侧车静默忽略（条目本身仍可用）。"""
    by_name = {e.filename: e for e in entries}
    for name, raw in sidecars:
        base = name[: -len(_SIDECAR_SUFFIX)]
        target = by_name.get(f"{base}.json") or by_name.get(f"{base}.png")
        if target is None:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            target.sidecar = payload


def _scan_json_entry(filename: str, raw: bytes) -> BundleEntry:
    """JSON 条目：先判世界书，再判角色卡，都不像则 unsupported + error。"""
    try:
        obj = json.loads(raw)  # 直接喂 bytes：json 自带 BOM/UTF-16 探测
    except Exception as exc:
        return BundleEntry(filename=filename, kind="unsupported", error=f"JSON 解析失败: {exc}")
    if not isinstance(obj, dict):
        return BundleEntry(
            filename=filename, kind="unsupported", error=f"JSON 顶层不是对象: {type(obj).__name__}"
        )

    # 世界书：entries 为 dict（ST）/ list（embedded/Risu），且无 first_mes。
    # 已知角色卡格式都不把 entries 放顶层，两者互斥，先判书避免"书被读成空卡"。
    if "first_mes" not in obj and isinstance(obj.get("entries"), (dict, list)):
        try:
            return BundleEntry(filename=filename, kind="lorebook", lorebook=import_lorebook(obj))
        except Exception as exc:
            return BundleEntry(filename=filename, kind="lorebook", error=f"世界书解析失败: {exc}")

    # 角色卡兜底：连一个卡面字段都没有的 JSON（如 {"foo": 1}）不硬塞给导入器——
    # import_card_json 会把空卡兜成"未命名角色"，那种成功是假象，不如报 error。
    if not (_CARD_FIELDS & obj.keys()):
        return BundleEntry(
            filename=filename, kind="unsupported", error="既非世界书也非角色卡（无 name/data/first_mes 等卡面字段）"
        )
    try:
        return BundleEntry(filename=filename, kind="character", card=import_card_json(obj))
    except Exception as exc:
        return BundleEntry(filename=filename, kind="character", error=f"角色卡解析失败: {exc}")


def _is_avatar_asset(filename: str) -> bool:
    """是否打包器约定的头像位 characters/<id>/avatar.png（允许外层再套目录）。"""
    parts = filename.split("/")
    return len(parts) >= 3 and parts[-3] == "characters" and parts[-1].lower() == "avatar.png"


def _scan_png_entry(filename: str, raw: bytes) -> BundleEntry:
    """PNG 条目：PNG 卡 → character + 原字节；纯头像/坏图见下。"""
    try:
        card = import_card_png(raw)
    except Exception as exc:
        if _is_avatar_asset(filename):
            # 打包器写的头像位可能只是纯图片（无 chara tEXt）：不是错误，
            # 原字节照常交给接线层落盘——否则自带包往返必报坏条目。
            return BundleEntry(filename=filename, kind="unsupported", avatar_png=raw)
        return BundleEntry(filename=filename, kind="unsupported", error=f"PNG 角色卡解析失败: {exc}")
    return BundleEntry(filename=filename, kind="character", card=card, avatar_png=raw)


def _character_sidecar(character: Character, *, share: bool = False) -> dict:
    """角色运行时侧车：卡面之外的 mrp 字段（迁移后由接线层按需应用）。"""
    sidecar = {
        "mrp": 1,
        "asset_id": character.id,
        "schema_version": character.schema_version,
        "revision": character.revision,
        "aliases": character.aliases,
        "talkativeness": character.talkativeness,
        "interject_enabled": character.interject_enabled,
        "followup_enabled": character.followup_enabled,
        "llm": character.llm.model_dump(mode="json"),
        "bound_lorebook_ids": character.bound_lorebook_ids,
        "source_world_id": None if share else character.source_world_id,
    }
    if not share:
        sidecar["authoring_source"] = character.authoring_source.model_dump(mode="json") if character.authoring_source else None
    return sidecar


def _lorebook_sidecar(book: Lorebook) -> dict:
    """世界书侧车：ST 格式不承载 name/description 与扫描参数。"""
    return {
        "mrp": 1,
        "asset_id": book.id,
        "schema_version": book.schema_version,
        "revision": book.revision,
        "name": book.name,
        "description": book.description,
        "tags": getattr(book, "tags", []),
        "scan_depth": book.scan_depth,
        "token_budget": book.token_budget,
        "recursive_scanning": book.recursive_scanning,
    }


# ---------- 打包 ----------


_AUTHOR_METADATA = frozenset({
    "mrp.archive_source", "mrp.archive_sources", "mrp.generated",
    "mrp.authoring_source", "authoring_source",
})


def _without_author_metadata(value: Any) -> tuple[Any, bool]:
    """Remove known provenance, including duplicate copies in import snapshots.

    Unknown rules and vendor extensions retain their names, values and layout.
    """
    if isinstance(value, dict):
        cleaned = {}
        removed = False
        for key, item in value.items():
            if key in _AUTHOR_METADATA:
                removed = True
                continue
            cleaned[key], child_removed = _without_author_metadata(item)
            removed = removed or child_removed
        return cleaned, removed
    if isinstance(value, list):
        cleaned = []
        removed = False
        for item in value:
            child, child_removed = _without_author_metadata(item)
            cleaned.append(child)
            removed = removed or child_removed
        return cleaned, removed
    return value, False


def share_lorebook_copies(lorebooks: list[Lorebook]) -> tuple[list[Lorebook], dict[str, int]]:
    """Portable share copies: author-only entries never reach an external runtime."""
    copies = []
    excluded = {"author_entries": 0, "provenance_entries": 0}
    for original in lorebooks:
        book = original.model_copy(deep=True)
        entries = []
        for entry in book.entries:
            if entry.extensions.get("mrp.runtime_scope") == "author":
                excluded["author_entries"] += 1
                continue
            entry.extensions, removed = _without_author_metadata(entry.extensions)
            excluded["provenance_entries"] += int(removed)
            entries.append(entry)
        book.entries = entries
        copies.append(book)
    return copies, excluded


def share_character_copies(characters: list[Character]) -> tuple[list[Character], dict[str, int]]:
    """Clean the known embedded-book copy without discarding foreign card rules."""
    copies = []
    excluded = {"author_entries": 0, "provenance_entries": 0}
    for original in characters:
        character = original.model_copy(deep=True)
        embedded = character.card.extensions.get("character_book")
        if isinstance(embedded, dict):
            entries = embedded.get("entries")
            if isinstance(entries, (list, dict)):
                cleaned = {} if isinstance(entries, dict) else []
                pairs = entries.items() if isinstance(entries, dict) else enumerate(entries)
                for key, entry in pairs:
                    if isinstance(entry, dict):
                        extensions = entry.get("extensions")
                        scope = entry.get("mrp.runtime_scope")
                        if scope is None and isinstance(extensions, dict):
                            scope = extensions.get("mrp.runtime_scope")
                        if scope == "author":
                            excluded["author_entries"] += 1
                            continue
                    entry, removed = _without_author_metadata(entry)
                    excluded["provenance_entries"] += int(removed)
                    if isinstance(cleaned, dict):
                        cleaned[key] = entry
                    else:
                        cleaned.append(entry)
                embedded["entries"] = cleaned
        character.card.extensions, _ = _without_author_metadata(character.card.extensions)
        character.authoring_source = None
        character.source_world_id = None
        copies.append(character)
    return copies, excluded


def build_bundle(
    characters: list[Character],
    lorebooks: list[Lorebook],
    *,
    avatars: dict[str, bytes] | None = None,
    share: bool = False,
) -> bytes:
    """把角色（卡面）与世界书打包成 zip bytes；布局与 scan_bundle 对称可往返。

    avatars 按 character.id 提供头像原始 PNG 字节；没有的就不写头像。
    卡面走 ST 格式（兼容外部工具），运行时字段（别名/主动性/采样配置/世界书绑定、
    世界书 name/description）写进同名 `.mrp.json` 侧车。私密迁移保留角色原稿，
    分享导出剔除原稿、来源世界、作者专用词条及世界书的原稿溯源记录；
    此格式不携带世界，导入时需重设世界关系。
    """
    if share:
        characters, _ = share_character_copies(characters)
        lorebooks, _ = share_lorebook_copies(lorebooks)
    avatar_map = avatars or {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for character in characters:
            zf.writestr(f"characters/{character.id}.json", export_card_json(character))
            zf.writestr(
                f"characters/{character.id}{_SIDECAR_SUFFIX}",
                json.dumps(_character_sidecar(character, share=share), ensure_ascii=False, indent=2).encode("utf-8"),
            )
            avatar = avatar_map.get(character.id)
            if avatar:
                zf.writestr(f"characters/{character.id}/avatar.png", avatar)
        for book in lorebooks:
            zf.writestr(f"lorebooks/{book.id}.json", export_lorebook_st(book))
            zf.writestr(
                f"lorebooks/{book.id}{_SIDECAR_SUFFIX}",
                json.dumps(_lorebook_sidecar(book), ensure_ascii=False, indent=2).encode("utf-8"),
            )

        manifest = {
            "version": _BUNDLE_VERSION,
            "characters": len(characters),
            "lorebooks": len(lorebooks),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "share" if share else "private_migration",
            "excludes": ["world_manuscripts", "full_body_images", "stories"] + (["authoring_source", "source_world_id", "author_lorebook_entries", "lorebook_author_provenance"] if share else []),
            "resources": [
                {
                    "kind": "character",
                    "id": character.id,
                    "revision": character.revision,
                    "title": character.card.name,
                    "files": [
                        f"characters/{character.id}.json",
                        f"characters/{character.id}{_SIDECAR_SUFFIX}",
                        *([f"characters/{character.id}/avatar.png"] if avatar_map.get(character.id) else []),
                    ],
                }
                for character in characters
            ] + [
                {
                    "kind": "lorebook",
                    "id": book.id,
                    "revision": book.revision,
                    "title": book.name,
                    "files": [
                        f"lorebooks/{book.id}.json",
                        f"lorebooks/{book.id}{_SIDECAR_SUFFIX}",
                    ],
                }
                for book in lorebooks
            ],
        }
        zf.writestr(_MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return buf.getvalue()
