"""世界书导入器：ST 独立格式 / 卡内嵌 character_book / RisuAI → 归一化 Lorebook（R4.1）。

三格式字段名各异、语义有坑（ST 的 disable 与 enabled 反向；Risu 的 key 是逗号分隔串），
统一收敛到 Lorebook/LorebookEntry。全字段容错：缺字段给默认值，未映射字段收 extensions。
"""
from __future__ import annotations

from typing import Any

from mrp.shared.models import InsertAnchor, Lorebook, LorebookEntry
from mrp.importers.compatibility import unsupported_entry_rules, lorebook_report

# ---------- 基础容错取值 ----------


def _int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _str(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) else default


def _str_list(v: Any) -> list[str]:
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


# ---------- ST 独立格式 ----------

# ST 条目中已映射到 LorebookEntry 标准字段的键；其余一律进 extensions
_ST_MAPPED = {
    "uid",
    "key",
    "keysecondary",
    "content",
    "comment",
    "constant",
    "disable",
    "selective",
    "selective_logic",
    "selectiveLogic",
    "order",
    "depth",
    "probability",
    "useProbability",
}


def _anchor_from_position(position: Any) -> InsertAnchor:
    """ST position 0..7 → 三档锚：0/1/2/3/5/6→system，4→at_depth，7→near。"""
    pos = _int(position, -1)
    if pos == 4:
        return "at_depth"
    if pos == 7:
        return "near"
    return "system"


def _entry_st(raw: dict[str, Any], fallback_uid: int) -> LorebookEntry:
    extensions = {k: v for k, v in raw.items() if k not in _ST_MAPPED}

    use_probability = _bool(raw.get("useProbability"), default=True)
    probability = _int(raw.get("probability"), 100) if use_probability else 100
    selective_logic = min(max(_int(raw.get("selectiveLogic", raw.get("selective_logic")), 0), 0), 3)
    enabled = _guard_entry(raw, extensions, not _bool(raw.get("disable")))

    return LorebookEntry(
        uid=_int(raw.get("uid"), fallback_uid),
        keys=_str_list(raw.get("key")),
        secondary_keys=_str_list(raw.get("keysecondary")),
        content=_str(raw.get("content")),
        comment=_str(raw.get("comment")),
        enabled=enabled,
        constant=_bool(raw.get("constant")),
        selective=_bool(raw.get("selective")),
        selective_logic=selective_logic,
        order=_int(raw.get("order"), 100),
        anchor=_anchor_from_position(raw.get("position")),
        depth=_int(raw.get("depth"), 4),
        probability=probability,
        extensions=extensions,
    )


def _guard_entry(raw: dict[str, Any], extensions: dict[str, Any], enabled: bool) -> bool:
    rules = unsupported_entry_rules(raw)
    if rules:
        previous = extensions.get("mrp.import_compatibility")
        previous = previous if isinstance(previous, dict) else {}
        extensions["mrp.import_compatibility"] = {
            "unexecuted_rules": rules, "original_enabled": previous.get("original_enabled", enabled),
            "original_entry": previous.get("original_entry", raw), "disabled_on_import": True,
        }
        return False
    return enabled


def _with_report(book: Lorebook) -> Lorebook:
    book.import_report = lorebook_report(book)
    return book


def import_lorebook_st(obj: dict[str, Any]) -> Lorebook:
    """ST 独立世界书：{entries: {"0": {...}, ...}}（数字字符串键的对象）。"""
    raw_entries = obj.get("entries") if isinstance(obj.get("entries"), dict) else {}
    entries: list[LorebookEntry] = []
    for i, (key, raw) in enumerate(raw_entries.items()):
        if not isinstance(raw, dict):
            continue
        entries.append(_entry_st(raw, fallback_uid=_int(key, i)))
    return _with_report(Lorebook(
        name=_str(obj.get("name"), "未命名世界书"),
        description=_str(obj.get("description")),
        tags=[tag.strip() for tag in obj.get("tags", []) if isinstance(tag, str) and tag.strip()][:30] if isinstance(obj.get("tags"), list) else [],
        entries=entries,
        scan_depth=_int(obj.get("scan_depth"), 2),
        token_budget=_int(obj.get("token_budget"), 1024),
        recursive_scanning=_bool(obj.get("recursive_scanning"), True),
        source_format="st",
    ))


# ---------- 卡内嵌 character_book ----------

_EMBEDDED_MAPPED = {
    "id",
    "keys",
    "secondary_keys",
    "enabled",
    "insertion_order",
    "constant",
    "position",
    "selective",
    "extensions",
}


def import_lorebook_embedded(book: dict[str, Any]) -> Lorebook:
    """卡内嵌 character_book：entries 是数组，字段名与 ST 不同（enabled 正向）。"""
    raw_entries = book.get("entries") if isinstance(book.get("entries"), list) else []
    entries: list[LorebookEntry] = []
    for i, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            continue
        extensions = {**(raw.get("extensions") if isinstance(raw.get("extensions"), dict) else {}),
                      **{k: v for k, v in raw.items() if k not in _EMBEDDED_MAPPED}}
        position = extensions.get("position", raw.get("position"))
        extensions["position"] = position
        entries.append(
            LorebookEntry(
                uid=_int(raw.get("id"), i),
                keys=_str_list(raw.get("keys")),
                secondary_keys=_str_list(raw.get("secondary_keys")),
                content=_str(raw.get("content")),
                comment=_str(raw.get("comment")),
                enabled=_guard_entry(raw, extensions, _bool(raw.get("enabled"), True)),
                constant=_bool(raw.get("constant")),
                selective=_bool(raw.get("selective")),
                selective_logic=min(max(_int(raw.get("selectiveLogic", raw.get("selective_logic", extensions.get("selectiveLogic"))), 0), 0), 3),
                order=_int(raw.get("insertion_order"), 100),
                anchor=_anchor_from_position(position),
                depth=_int(extensions.get("depth"), 4),
                probability=_int(extensions.get("probability"), 100) if _bool(extensions.get("useProbability"), True) else 100,
                extensions=extensions,
            )
        )
    return _with_report(Lorebook(
        name=_str(book.get("name"), "未命名世界书"),
        description=_str(book.get("description")),
        entries=entries,
        scan_depth=_int(book.get("scan_depth"), 2),
        token_budget=_int(book.get("token_budget"), 1024),
        recursive_scanning=_bool(book.get("recursive_scanning"), True),
        source_format="embedded",
    ))


# ---------- RisuAI ----------

_RISU_MAPPED = {
    "id",
    "key",
    "secondkey",
    "content",
    "comment",
    "insertorder",
    "alwaysActive",
    "selective",
    "probability",
}


def _risu_keys(v: Any) -> list[str]:
    """Risu 的 key 是逗号分隔字符串（'/.../' 开头为正则）：拆分、去空白、丢空串。"""
    if isinstance(v, list):
        return _str_list(v)
    if not isinstance(v, str):
        return []
    return [part.strip() for part in v.split(",") if part.strip()]


def import_lorebook_risu(obj: dict[str, Any]) -> Lorebook:
    """RisuAI 格式：entries 为数组，key/secondkey 是逗号分隔串。"""
    raw_entries = obj.get("entries") if isinstance(obj.get("entries"), list) else []
    entries: list[LorebookEntry] = []
    for i, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            continue
        extensions = {k: v for k, v in raw.items() if k not in _RISU_MAPPED}
        entries.append(
            LorebookEntry(
                uid=_int(raw.get("id"), i),
                keys=_risu_keys(raw.get("key")),
                secondary_keys=_risu_keys(raw.get("secondkey")),
                content=_str(raw.get("content")),
                comment=_str(raw.get("comment")),
                enabled=_guard_entry(raw, extensions, _bool(raw.get("enabled"), True)),
                constant=_bool(raw.get("alwaysActive")),
                selective=_bool(raw.get("selective")),
                order=_int(raw.get("insertorder"), 100),
                probability=_int(raw.get("probability"), 100),
                extensions=extensions,
            )
        )
    return _with_report(Lorebook(
        name=_str(obj.get("name"), "未命名世界书"),
        description=_str(obj.get("description")),
        entries=entries,
        source_format="risu",
    ))


# ---------- 自动探测 ----------


def import_lorebook(obj: dict[str, Any]) -> Lorebook:
    """按 entries 形态自动探测格式：dict→ST；list→按字段名区分 Risu/embedded。"""
    if not isinstance(obj, dict):
        raise ValueError("世界书根对象必须为 JSON 对象")
    raw_entries = obj.get("entries")
    if isinstance(raw_entries, dict):
        return import_lorebook_st(obj)
    if isinstance(raw_entries, list):
        sample = next((e for e in raw_entries if isinstance(e, dict)), None)
        if sample is not None and ("insertorder" in sample or "alwaysActive" in sample):
            return import_lorebook_risu(obj)
        return import_lorebook_embedded(obj)
    # 空书 / 畸形输入：给默认空 Lorebook，不炸
    return _with_report(Lorebook(name=_str(obj.get("name"), "未命名世界书"), entries=[], source_format="st"))
