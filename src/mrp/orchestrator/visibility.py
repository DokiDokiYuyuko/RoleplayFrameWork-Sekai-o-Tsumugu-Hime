"""可见性计算（R3.1/R3.2）：新消息可见范围 + 角色视角上下文 + 在场变化通知。

纯函数、消息创建时定死（visible_to 不回溯）：
- 离席角色看不到离席期间创建的消息
- 回归不回溯——回归后新消息才重新对他可见
"""
from __future__ import annotations

from typing import Literal

from mrp.shared.models import ActorId, Character, Message, SessionState


def compute_visible_to(
    characters: list[Character], include_player: bool = True,
    group_ids: list[str] | None = None,
) -> list[ActorId] | Literal["all"]:
    """新消息的可见性（创建时定死，R3.1 两档）。

    全部角色 present → "all"；否则返回 present 角色 id 列表（含 "player"，
    除非 include_player=False）。角色间按传入顺序，player 追加在末尾。
    """
    if all(c.present for c in characters):
        return "all"
    ids: list[ActorId] = [c.id for c in characters if c.present]
    ids.extend(group_ids or [])
    if include_player:
        ids.append("player")
    return ids


def active_group_ids(session: SessionState) -> list[str]:
    """Group participants attached to the current scene and still active."""
    scene = next((item for item in session.scenes if item.id == session.active_scene_id), None)
    if scene is None:
        return []
    active = {item.id for item in session.groups if item.status == "active"}
    return [group_id for group_id in scene.group_ids if group_id in active]


def character_context(
    session: SessionState,
    character_id: str,
    scan_turns: int | None = None,
) -> list[Message]:
    """某角色视角的消息流（session.visible_messages_for 的封装）。

    scan_turns 给定时只保留最近 N 回合：以会话当前 turn 为基准，
    cutoff = current - N + 1，保留 turn >= cutoff 的可见消息（角色离席
    期间的消息本就不可见，不在此额外补偿）。
    """
    visible = session.visible_messages_for(character_id)
    if scan_turns is None:
        return visible
    current = session.current_turn()
    cutoff = current - scan_turns + 1
    return [m for m in visible if m.turn >= cutoff]


def presence_change(
    session: SessionState, character_id: str, present: bool
) -> list[str]:
    """角色离席/回归时，需要通知的其他角色 id 列表（v1 纯计算）。

    在场变化只影响"当时在场"的其他角色——离席与回归的通知集相同
    （present 参数保留给 v1+ 语义分化，如回归时仅通知共同在场者）。
    会话循环据此插入 kind="system_event" 消息（回归不回溯，R3.2）。
    """
    return [
        c.id for c in session.characters if c.id != character_id and c.present
    ]
