"""A bounded, shared scene frame for simultaneous speakers.

This is director context, not a character memory. It is rebuilt from the same
pre-reply snapshot for every participant and never persisted into chat history.
"""
from __future__ import annotations

from mrp.shared.actor_labels import build_actor_labels
from mrp.shared.models import SessionState
from mrp.shared.prompt import estimate_tokens
from mrp.shared.player_identity import message_label, current_identity


FRAME_TOKEN_LIMIT = 650


def _public(message):
    # Presence-scoped public messages are lists; inner and private actor-only
    # messages do not become shared director material.
    return message.visible_to == "all" or ("player" in message.visible_to and len(message.visible_to) > 1)


def shared_scene_source_messages(state, turn, *, exclude_message_id=None, exclude_message_ids=None, viewer_ids=None):
    """The public material read by the scene-frame builder, with the same permissions."""
    public = [m for m in state.messages if m.status == "final" and not m.dependency_stale
              and m.scene_id == state.active_scene_id and m.turn <= turn and _public(m)
              and m.kind in {"roleplay", "scene"} and not m.control_event
              and m.id != exclude_message_id and m.id not in (exclude_message_ids or set())
              and (not viewer_ids or all(m.can_see(cid) for cid in viewer_ids))]
    return ([m for m in public if m.turn < turn][-4:]
            + [m for m in public if m.turn == turn and m.actor == "player"][-2:])


def _short(text: str, limit: int) -> str:
    value = " ".join(text.split())
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def _fit(text: str, budget: int) -> str:
    if budget <= 0:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle] + "…") <= budget:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "…" if low else ""


def recent_public_scene_context(
    state: SessionState, turn: int, *, limit: int = 10,
    exclude_message_id: str | None = None,
    exclude_message_ids: set[str] | None = None,
    viewer_ids: list[str] | None = None,
) -> str:
    """Public story so far, without private lines or group administration events."""
    scene_id = state.active_scene_id
    messages = [
        message for message in state.messages
        if message.status == "final" and not message.dependency_stale and message.scene_id == scene_id
        and message.turn <= turn and _public(message)
        and message.kind in ("roleplay", "scene")
        and message.id != exclude_message_id
        and message.id not in (exclude_message_ids or set())
        and (not viewer_ids or all(message.can_see(actor) for actor in viewer_ids))
    ][-limit:]
    names = build_actor_labels(state, messages)
    return "\n".join(
        f"{('旁白' if message.kind == 'scene' else message_label(state, message, names))}："
        f"{_short(message.content, 220)}"
        for message in messages
    )


def build_shared_scene_frame(
    state: SessionState, turn: int, *, exclude_message_id: str | None = None,
    exclude_message_ids: set[str] | None = None, exclude_group_ids: set[str] | None = None,
    include_world_core: bool = True,
    viewer_ids: list[str] | None = None,
) -> str:
    """Give parallel actors one public stage without granting character memories.

    Prior public lines are director-level context: a newly joined group may use
    them to understand the current situation, but may not claim to have heard
    those earlier conversations. Private and inner messages are never included.
    """
    scene = next((item for item in state.scenes if item.id == state.active_scene_id), None)
    if scene is None:
        return ""

    present = [
        character.id for character in state.characters
        if character.id in scene.member_ids and character.present
    ]
    excluded_groups = exclude_group_ids or set()
    groups = [
        group for group in state.groups
        if group.id in scene.group_ids and group.status == "active" and group.id not in excluded_groups
    ]
    present.extend(group.id for group in groups)
    controlled = current_identity(state)
    if controlled:
        present.append(controlled.person_id)

    public = [
        message for message in state.messages
        if message.status == "final" and not message.dependency_stale and message.scene_id == scene.id
        and message.turn <= turn and _public(message)
        and message.kind in ("roleplay", "scene")
        and message.id != exclude_message_id
        and message.id not in (exclude_message_ids or set())
        and (not viewer_ids or all(message.can_see(actor) for actor in viewer_ids))
    ]
    names = build_actor_labels(state, public, actor_ids=present)
    current = [message for message in public if message.turn == turn and message.actor == "player"]
    earlier = [message for message in public if message.turn < turn][-4:]

    lines = [
        "[本轮共享场景基线]",
        f"地点：{_short(scene.title or '未命名场景', 80)}",
        "以已经发生的消息为准；不要预设他人尚未生成的台词或动作。",
        "公开前情是导演提供的当前局势线索；加入前的内容不是你的亲历记忆。",
    ]
    if scene.description:
        lines.append(f"时间与环境：{_short(scene.description, 180)}")
    if present:
        lines.append(f"在场：{_short('、'.join(names.get(actor_id, '已退出角色') for actor_id in present), 180)}")
    if current:
        lines.append(names.get("player", "玩家") + "本轮行动：" + " / ".join(
            _short(message.content, 160) for message in current[-2:]
        ))
    if earlier:
        lines.append("此前公开局势：")
        lines.extend(
            f"- {('旁白' if message.kind == 'scene' else message_label(state, message, names))}：{_short(message.content, 160)}"
            for message in earlier
        )
    for group in groups:
        if group.public_brief:
            lines.append(f"{names.get(group.id, group.label)}的可观察特征：{_short(group.public_brief, 130)}")
    if include_world_core and state.meta.world_core_brief and state.meta.world_runtime_policy != 'raw':
        lines.append(f"世界背景：{_short(state.meta.world_core_brief, 260)}")

    kept: list[str] = []
    remaining = FRAME_TOKEN_LIMIT
    for line in lines:
        fitted = _fit(line, remaining - estimate_tokens("\n"))
        if not fitted:
            break
        kept.append(fitted)
        remaining -= estimate_tokens(fitted + "\n")
    return "\n".join(kept)
