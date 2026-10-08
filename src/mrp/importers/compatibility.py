"""Explain which imported declarative behavior actually runs in this app."""
from __future__ import annotations

import re
from typing import Any

SUPPORTED_MACROS = {"char", "character", "user", "original", "story"}


def unknown_macros(text: str) -> list[str]:
    return sorted({match.strip() for match in re.findall(r"\{\{([^{}]+)\}\}", text)
                   if match.strip().lower() not in SUPPORTED_MACROS})


def blank_report() -> dict[str, list[str]]:
    return {"converted": [], "partial": [], "unsupported": []}


def unsupported_entry_rules(raw: dict[str, Any]) -> list[str]:
    """Only active foreign controls block an entry; zero/default flags do not."""
    fields = {**(raw.get("extensions") if isinstance(raw.get("extensions"), dict) else {}), **raw}
    rules = []
    role = fields.get("role")
    if role not in (None, "system", 0, "0"):
        rules.append(f"role={role}")
    for key in ("sticky", "cooldown", "delay", "delayUntilRecursion", "characterFilter",
                "character_filter", "activationDelay", "activateOnlyAfter", "caseSensitive",
                "matchWholeWords", "useRegex", "scanDepth"):
        value = fields.get(key)
        if isinstance(value, dict):
            active = any(value.get(part) for part in ("names", "tags", "characters", "ids"))
        elif isinstance(value, str):
            active = value.strip().lower() not in {"", "0", "false", "none"}
        else:
            active = bool(value)
        if active:
            rules.append(key)
    # Risu directives encode execution, exclusion and timing semantics, not prose.
    rules.extend(f"@@{directive}" for directive in sorted(set(re.findall(
        r"(?m)^\s*@@@?([\w-]+)", str(fields.get("content") or "")))))
    strings = [str(fields.get("content") or "")]
    for key in ("key", "keys", "secondkey", "keysecondary", "secondary_keys"):
        value = fields.get(key)
        strings.extend(value if isinstance(value, list) else [str(value or "")])
    rules.extend(f"宏 {{{{{macro}}}}}" for macro in unknown_macros("\n".join(map(str, strings))))
    return rules


def card_report(card) -> dict[str, list[str]]:
    report = blank_report()
    report["converted"].append("人物资料、开场与行为指令")
    if card.mes_example.strip():
        report["converted"].append("示例对话进入每轮人设并计入容量；仅作口吻参考，不作为已发生剧情")
    report["converted"].append("{{char}}、{{user}} 按当前发言者与玩家身份展开；{{original}} 使用本项目基础扮演指令")
    if card.creator_notes:
        report["partial"].append("作者备注仅保存，供阅读，不发送给模型")
    for key in ("depth_prompt", "regex_scripts", "scripts", "script", "assets", "triggers"):
        if card.extensions.get(key):
            report["unsupported"].append(f"{key}：仅保存原文，不执行或加载")
    content = "\n".join(str(getattr(card, key) or "") for key in
                        ("description", "personality", "scenario", "system_prompt", "post_history_instructions", "mes_example"))
    for macro in unknown_macros(content):
        report["unsupported"].append(f"宏 {{{{{macro}}}}}：保持原文，未展开；请手动改写")
    return report


def lorebook_report(book) -> dict[str, list[str]]:
    report = blank_report()
    report["converted"].append("关键词、四种二级条件、常驻开关、概率、启用状态及书级扫描/容量设置")
    for entry in book.entries:
        compatibility = entry.extensions.get("mrp.import_compatibility")
        compatibility = compatibility if isinstance(compatibility, dict) else {}
        rules = compatibility.get("unexecuted_rules") or []
        label = entry.comment or f"条目 {entry.uid}"
        if rules:
            report["unsupported"].append(f"{label}：{', '.join(map(str, rules))} 未执行，导入时已停用；完整原稿保留在条目扩展中")
        position = entry.extensions.get("position")
        if position not in (None, 0, 4, 7, "before_char"):
            report["partial"].append(f"{label}：原插入位置 {position} 转为 {entry.anchor}；不保留外部人物定义区的精确相对位置")
    report["partial"].append("递归扫描仅支持一层；外部多层递归与分支时效状态不迁入")
    return report
