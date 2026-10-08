"""注入检查器留档：prompt 四段切分 + 查询视图（前端契约）。

从 session.py 拆出（W3）。Section 切分口径与前端 AssistInspector 对齐，
标记字符串属对外契约（勿改）。
"""
from __future__ import annotations

from typing import Any

from mrp.shared.prompt import estimate_tokens

SECTION_MARKERS: list[tuple[str, str, str]] = [
    ("world_core", "世界核心", "[世界核心]"),
    ("player", "玩家身份", "[玩家角色设定]"),
    ("archive", "世界档案", "[世界档案]"),
    ("group", "群体设定", "[群体设定]"),
    ("scene_frame", "本轮共同场景", "[本轮共享场景基线]"),
    ("parallel_plan", "本轮行动基线", "[本轮统一行动基线]"),
    ("recent_story", "最近公开剧情", "[最近公开剧情·以此为准]"),
    ("scene", "场景设定", "[场景设定]"),
    ("world", "世界设定", "[世界设定]"),
    ("memory", "相关记忆", "[相关记忆]"),
    ("system", "系统上下文", "[系统上下文]"),  # M9-0：system 锚注入（检查器可见）
    ("history", "对话记录", "=== 对话记录 ==="),
    ("instructions", "指令", "=== 现在轮到你 ==="),
]

_TOKEN_KEYS = {
    "world_core": "world_core",
    "player": "player",
    "archive": "archive",
    "group": "group",
    "scene_frame": "shared_scene_frame",
    "parallel_plan": "parallel_scene_plan",
    "recent_story": "recent_story",
    "scene": "scene",
    "world": "lorebook", "memory": "memory", "system": "system",
    "history": "history", "instructions": "instructions",
}


def split_sections(composed) -> list[dict[str, Any]]:
    """把组装后的 prompt 按四段标记切分（注入检查器前端契约）。"""
    text = composed.text
    sections: list[dict[str, Any]] = []
    positions = [(text.find(m), kind, title, m) for kind, title, m in SECTION_MARKERS]
    found = [(pos, kind, title, m) for pos, kind, title, m in positions if pos >= 0]
    found.sort()
    for i, (pos, kind, title, marker) in enumerate(found):
        start = pos + len(marker)
        end = found[i + 1][0] if i + 1 < len(found) else len(text)
        token_key = _TOKEN_KEYS[kind]
        sections.append(
            {
                "kind": kind,
                "title": title,
                "content": text[start:end].strip(),
                "tokens": composed.tokens_by_section.get(token_key, 0)
                + (composed.tokens_by_section.get("near", 0) if kind == "world" else 0),
            }
        )
    return sections


def build_inspection(record: dict[str, Any], character_id: str, turn: int, *, mode: str = "actual") -> dict[str, Any]:
    """留档记录 → 检查器查询视图。record: {"ctx": TurnContext, "composed": ComposedPrompt}。"""
    ctx = record["ctx"]
    composed = record["composed"]
    included = set(composed.included_entry_ids)
    return {
        "character_id": character_id,
        "turn": turn,
        "mode": mode,
        "branch_id": ctx.session_id,
        "plan_id": ctx.plan_id,
        "message_id": ctx.message_id,
        "generation_id": ctx.generation_id,
        "operation_id": ctx.operation_id,
        "attempt_id": ctx.attempt_id,
        "request_ids": ctx.request_ids,
        "provenance": ctx.provenance.model_dump(mode="json") if ctx.provenance else None,
        "player_identity_source": ctx.player_identity_source,
        "prompt": composed.text,
        "tokens_by_section": composed.tokens_by_section,
        "sections": split_sections(composed),
        "total_tokens": composed.total_tokens,
        "budget_tokens": ctx.budget_tokens,
        "context_limit": ctx.context_limit,
        "capacity_source": ctx.capacity_source,
        "capacity_fetched_at": ctx.capacity_fetched_at,
        "model_id": ctx.model_id,
        "gateway": ctx.gateway,
        "model_provider": ctx.model_provider,
        "output_reserve": ctx.output_reserve,
        "injected_entry_ids": composed.included_entry_ids,
        "memory_recall": record.get("memory_recall", []),
        "visible_message_ids": composed.included_message_ids,
        "sources": ([{
            "entry_id": "world_core",
            "source": "world",
            "anchor": "system",
            "order": 0,
            "reason": "开局时保存的世界核心摘要快照；每轮投递",
            "tokens": estimate_tokens(ctx.world_core_brief),
            "included": True,
            "excluded_reason": None,
        }] if ctx.world_core_brief.strip() and ctx.world_runtime_policy != 'raw' else []) + [{
            "entry_id": inj.entry_id,
            "source": inj.source,
            "anchor": inj.anchor,
            "order": inj.order,
            "section": composed.entry_sections.get(inj.entry_id, inj.placement if inj.entry_id in included else "omitted"),
            "reason": inj.reason or "常驻或上下文命中",
            "tokens": estimate_tokens(inj.content),
            "included": inj.entry_id in included,
            "excluded_reason": None if inj.entry_id in included else composed.omitted_reasons.get(inj.entry_id, "超出本轮预算"),
        } for inj in ctx.injections],
        "omitted_message_ids": [m.id for m in ctx.visible_messages
                                if m.id not in composed.included_message_ids],
    }
