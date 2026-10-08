"""导出器：与导入器对称的出口（R23.3/R29.4 硬标准——导出→导入无损往返）。

- 角色卡：CCv2 JSON / PNG（tEXt 'chara' = base64 JSON）
- 世界书：ST 独立格式 JSON（entries 数字键对象，字段名按 ST 约定）
"""
from __future__ import annotations

import base64
import io
import json
from typing import Any

from PIL import Image

from mrp.shared.models import Character, CharacterCard, Lorebook


def card_to_ccv2_dict(card: CharacterCard, character: Character | None = None) -> dict[str, Any]:
    """归一化卡 → CCv2 dict（顶层平铺 + data 包装，导入器可无损读回）。"""
    # CCv2 没有独立外貌字段；合并进标准 description 供其他酒馆阅读，
    # 同时在扩展字段保留本项目的独立分区，回导时可以无损拆开。
    appearance_suffix = f"\n\n[身体与外貌]\n{card.appearance}" if card.appearance else ""
    traits_label = card.traits_label.strip() or "能力与实力"
    traits_suffix = f"\n\n[{traits_label}]\n{card.traits}" if card.traits else ""
    portable_description = card.description + appearance_suffix + traits_suffix
    extensions = dict(card.extensions or {})
    extensions.pop("authoring_source", None)
    extensions.pop("mrp.authoring_source", None)
    if card.appearance:
        extensions["mrp_appearance"] = card.appearance
    if card.traits:
        extensions["mrp_traits_label"] = traits_label
        extensions["mrp_traits"] = card.traits
    data: dict[str, Any] = {
        "name": card.name,
        "description": portable_description,
        "appearance": card.appearance,
        "traits_label": traits_label,
        "traits": card.traits,
        "personality": card.personality,
        "scenario": card.scenario,
        "first_mes": card.first_mes,
        "mes_example": card.mes_example,
        "alternate_greetings": card.alternate_greetings,
        "system_prompt": card.system_prompt or "",
        "post_history_instructions": card.post_history_instructions or "",
        "creator_notes": card.creator_notes,
        "creator": card.creator,
        "character_version": card.character_version,
        "tags": card.tags,
        # extensions 原样带回（含导入时的 character_book 原文——往返无损的关键）
        "extensions": extensions,
    }
    top = {k: v for k, v in data.items() if k in (
        "name", "description", "personality", "scenario", "first_mes", "mes_example"
    )}
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data, **top}


def export_card_json(character: Character) -> bytes:
    return json.dumps(
        card_to_ccv2_dict(character.card, character), ensure_ascii=False, indent=2
    ).encode("utf-8")


def export_card_png(character: Character, avatar_png: bytes | None = None) -> bytes:
    """卡 → PNG：头像原图打底（若有），tEXt 写 'chara' 关键字。

    无头像时生成 512×512 纯色底图（角色名首字太复杂，纯色即可）。
    """
    card_json = json.dumps(
        card_to_ccv2_dict(character.card, character), ensure_ascii=False
    ).encode("utf-8")
    encoded = base64.b64encode(card_json).decode("ascii")

    if avatar_png:
        img = Image.open(io.BytesIO(avatar_png)).convert("RGBA")
    else:
        img = Image.new("RGBA", (512, 512), (99, 102, 241, 255))

    # PNG 规范：tEXt 值为 Latin-1——base64 恰好是 ASCII，安全
    from PIL.PngImagePlugin import PngInfo

    meta = PngInfo()
    meta.add_text("chara", encoded)
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


# ---------- 世界书 ST 导出 ----------

_ANCHOR_TO_ST_POSITION = {"system": 0, "at_depth": 4, "near": 7}


def lorebook_to_st_dict(book: Lorebook) -> dict[str, Any]:
    """统一模型 → ST 独立格式（entries 数字键对象；字段名/语义与 ST 对齐）。"""
    entries: dict[str, Any] = {}
    for i, e in enumerate(book.entries):
        entries[str(i)] = {
            **(e.extensions or {}),
            "uid": e.uid,
            "key": e.keys,
            "keysecondary": e.secondary_keys,
            "comment": e.comment,
            "content": e.content,
            "constant": e.constant,
            "selective": e.selective,
            "selectiveLogic": e.selective_logic,
            "order": e.order,
            "position": _ANCHOR_TO_ST_POSITION.get(e.anchor, 0),
            "depth": e.depth,
            "probability": e.probability,
            "useProbability": True,
            "disable": not e.enabled,
            "excludeRecursion": (e.extensions or {}).get("excludeRecursion", False),
            "preventRecursion": (e.extensions or {}).get("preventRecursion", False),
            "delayUntilRecursion": (e.extensions or {}).get("delayUntilRecursion", False),
        }
    return {"name": book.name, "description": book.description,
            "tags": getattr(book, "tags", []),
            "scan_depth": book.scan_depth, "token_budget": book.token_budget,
            "recursive_scanning": book.recursive_scanning, "entries": entries}


def export_lorebook_st(book: Lorebook) -> bytes:
    return json.dumps(lorebook_to_st_dict(book), ensure_ascii=False, indent=2).encode("utf-8")
