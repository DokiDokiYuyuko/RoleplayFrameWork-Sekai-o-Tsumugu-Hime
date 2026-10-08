"""角色卡导入器：PNG tEXt chunk / CCv1 / CCv2 / CCv3 → 归一化 CharacterCard（R1.1）。

纯函数，无副作用（save_avatar_png 除外——它就是副作用本身）。
全字段容错：缺字段用默认值，未知字段尽量塞 extensions，绝不因畸形输入炸掉。
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image

from mrp.shared.models import CharacterCard
from mrp.importers.compatibility import card_report
from mrp.storage.paths import default_data_root

# 归一化模型已承载的 data 层字段——之外的进 extensions
_KNOWN_DATA_FIELDS = {
    "name",
    "description",
    "appearance",
    "traits_label",
    "traits",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "alternate_greetings",
    "system_prompt",
    "post_history_instructions",
    "creator_notes",
    "creator",
    "character_version",
    "tags",
    "extensions",
}

# 头部级字段（v2/v3），不进 extensions
_KNOWN_TOP_FIELDS = {"spec", "spec_version", "data"}

def _b64_to_json(raw: str) -> dict[str, Any]:
    """base64 字符串 → dict。容错：补 padding、忽略换行；失败再试原始 UTF-8 JSON。"""
    raw = raw.strip()
    try:
        padded = raw + "=" * (-len(raw) % 4)
        obj = json.loads(base64.b64decode(padded).decode("utf-8"))
    except Exception:
        obj = json.loads(raw)  # 有些实现直接塞明文 JSON
    if not isinstance(obj, dict):
        raise ValueError(f"卡片 JSON 不是对象: {type(obj).__name__}")
    return obj


def _str(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) else default


def _str_list(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return [str(x) for x in v if isinstance(x, str) and x.strip()]
    return []


def _opt_str(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def import_card_json(obj: dict[str, Any]) -> CharacterCard:
    """识别 CCv1（平铺）/ CCv2 / CCv3 并归一化为 CharacterCard。"""
    if not isinstance(obj, dict):
        raise ValueError(f"卡片必须是 dict，得到 {type(obj).__name__}")

    spec = obj.get("spec")
    nested = obj.get("data")
    if isinstance(nested, dict):
        # ST 导出的 CCv2/v3：字段在 data 包装内
        source_format = "ccv3" if spec == "chara_card_v3" else "ccv2"
        data: dict[str, Any] = nested
    else:
        # 平铺卡（内部模型 / 工坊产物 / CCv1）：即使声明 v2/v3 spec 也按平铺字段读
        # （2026-09-25 修复：此前声明 spec=chara_card_v2 但无 data 的卡会被读成空卡 → "未命名角色"）
        source_format = "ccv3" if spec == "chara_card_v3" else ("ccv2" if spec == "chara_card_v2" else "ccv1")
        data = obj

    # extensions：卡自带 extensions 打底，未知字段追加（data 层 + 头部层）
    extensions: dict[str, Any] = {}
    card_ext = data.get("extensions")
    if isinstance(card_ext, dict):
        extensions.update(card_ext)
    for k, v in data.items():
        if k not in _KNOWN_DATA_FIELDS and k not in extensions:
            extensions[k] = v
    if data is not obj:
        for k, v in obj.items():
            if k not in _KNOWN_TOP_FIELDS and k not in extensions:
                extensions[k] = v

    # spec 原样存；缺失时按来源格式给基线值
    if isinstance(spec, str) and spec:
        out_spec = spec
        out_spec_ver = _str(obj.get("spec_version"), "")
    elif source_format == "ccv1":
        out_spec, out_spec_ver = "chara_card_v1", "1.0"
    else:
        out_spec, out_spec_ver = "chara_card_v2", "2.0"

    appearance = _str(data.get("appearance")) or _str(extensions.get("mrp_appearance")) or _str(extensions.get("appearance"))
    traits_label = _str(data.get("traits_label")) or _str(extensions.get("mrp_traits_label")) or "能力与实力"
    traits = _str(data.get("traits")) or _str(extensions.get("mrp_traits"))
    description = _str(data.get("description"))
    portable_suffix = f"\n\n[身体与外貌]\n{appearance}" if appearance else ""
    traits_suffix = f"\n\n[{traits_label}]\n{traits}" if traits else ""
    portable_suffix += traits_suffix
    if portable_suffix and description.endswith(portable_suffix):
        description = description[: -len(portable_suffix)]

    card = CharacterCard(
        spec=out_spec,
        spec_version=out_spec_ver or ("1.0" if source_format == "ccv1" else "2.0"),
        name=_str(data.get("name")) or "未命名角色",
        description=description,
        appearance=appearance,
        traits_label=traits_label,
        traits=traits,
        personality=_str(data.get("personality")),
        scenario=_str(data.get("scenario")),
        first_mes=_str(data.get("first_mes")),
        mes_example=_str(data.get("mes_example")),
        alternate_greetings=_str_list(data.get("alternate_greetings")),
        system_prompt=_opt_str(data.get("system_prompt")),
        post_history_instructions=_opt_str(data.get("post_history_instructions")),
        creator_notes=_str(data.get("creator_notes")),
        creator=_str(data.get("creator")),
        character_version=_str(data.get("character_version")),
        tags=_str_list(data.get("tags")),
        extensions=extensions,
        source_format=source_format,
    )
    card.import_report = card_report(card)
    return card


def import_card_png(data: bytes) -> CharacterCard:
    """从 PNG 字节读角色卡：tEXt chunk 'ccv3' 优先，其次 'chara'（base64 JSON）。"""
    img = Image.open(io.BytesIO(data))
    # Pillow 10+ 提供 img.text；旧版/异常时退回 img.info
    text_chunks: dict[str, Any] = getattr(img, "text", None) or img.info or {}

    for keyword in ("ccv3", "chara"):
        raw = text_chunks.get(keyword)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            return import_card_json(_b64_to_json(raw))
        except Exception:
            if keyword == "chara":  # 两个关键字都试过才放弃
                raise
            continue  # ccv3 坏了还有 chara 兜底

    raise ValueError(f"PNG 中没有角色卡 tEXt chunk（'ccv3'/'chara'），现有关键字: {sorted(text_chunks)}")


def save_avatar_png(data: bytes, character_id: str, root: Path | str | None = None) -> str:
    """把原始 PNG 字节存为 root/<character_id>/avatar.png，返回相对路径字符串。"""
    if not character_id or "/" in character_id or "\\" in character_id or character_id in {".", ".."}:
        raise ValueError(f"非法 character_id: {character_id!r}")
    root_path = Path(root) if root is not None else default_data_root() / "characters"
    dest_dir = root_path / character_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "avatar.png").write_bytes(data)
    return f"{character_id}/avatar.png"
