"""R22 通道语法解析（酒馆社区约定：一条消息混合多通道）。

从 session.py 拆出（W3）：纯函数，零依赖，message_ops/turn_engine 共用。
`mrp.orchestrator.session.parse_channels` 仍可用（再导出，兼容既有调用点）。
"""
from __future__ import annotations

import re as _re

_INNER_RE = _re.compile(r"[（(]\s*内心\s*[：:]\s*(.*?)[）)]", _re.DOTALL)
_NARR_RE = _re.compile(r"[（(]\s*(?:画外音|旁白)\s*[：:]\s*(.*?)[）)]", _re.DOTALL)


def parse_channels(content: str, default_kind: str = "roleplay") -> list[tuple[str, str]]:
    """解析输入文本，拆分为 (kind, text) 列表。

    语法（对齐 RP 社区约定，中英文括号/冒号都支持）：
    - （内心：...）(内心:...) → inner（只有玩家可见，注入潜台词）
    - （画外音：...）（旁白：...）→ scene（场景事件，角色会反应）
    - 其余文本 → default_kind（旧调用默认 roleplay；故事写作传 scene）
    """
    if default_kind not in ("roleplay", "scene"):
        raise ValueError(f"unsupported default channel: {default_kind}")
    matches: list[tuple[int, int, str, str]] = []
    for m in _INNER_RE.finditer(content):
        matches.append((m.start(), m.end(), "inner", m.group(1).strip()))
    for m in _NARR_RE.finditer(content):
        matches.append((m.start(), m.end(), "scene", m.group(1).strip()))
    matches.sort(key=lambda x: x[0])

    parts: list[tuple[str, str]] = []
    pos = 0
    for start, end, kind, text in matches:
        if start > pos:
            dialogue = content[pos:start].strip()
            if dialogue:
                parts.append((default_kind, dialogue))
        if text:
            parts.append((kind, text))
        pos = end
    if pos < len(content):
        dialogue = content[pos:].strip()
        if dialogue:
            parts.append((default_kind, dialogue))
    return parts if parts else [(default_kind, content)]
