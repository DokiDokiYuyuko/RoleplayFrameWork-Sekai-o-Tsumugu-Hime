"""Request-local display labels for speakers in prompt history."""
from __future__ import annotations

from collections import defaultdict
import re

from mrp.shared.models import Message, ReplyFrame, SessionState


def _player_name(state: SessionState) -> str:
    first = (state.meta.player_persona or "").strip().splitlines()[0] if state.meta.player_persona else ""
    if first.startswith("姓名："):
        return first.removeprefix("姓名：").strip() or "玩家"
    return "玩家"


def build_actor_labels(
    state: SessionState,
    messages: list[Message],
    *,
    speaker_id: str | None = None,
    actor_ids: list[str] | None = None,
) -> dict[str, str]:
    """Resolve IDs from the current state to readable, collision-safe labels.

    Only actors present in the supplied visible messages and the current speaker
    are returned. Name collisions are resolved from roster order, not message
    content, so a renamed card naturally updates the label for its stable ID.
    """
    roster: dict[str, tuple[str, str]] = {
        item.id: ("character", item.card.name.strip() or "未命名角色")
        for item in state.characters
    }
    for person_id, character in state.player_people.items():
        roster.setdefault(person_id, ("character", character.card.name.strip() or "未命名角色"))
    roster.update({
        item.id: ("group", item.label.strip() or "未命名群体")
        for item in state.groups
    })
    collisions: dict[tuple[str, str], list[str]] = defaultdict(list)
    for actor_id, (kind, name) in roster.items():
        collisions[(kind, name.casefold())].append(actor_id)

    numbered: dict[str, str] = {}
    for (kind, normalized), collision_ids in collisions.items():
        name = roster[collision_ids[0]][1]
        prefix = "角色" if kind == "character" else "群体"
        for index, actor_id in enumerate(collision_ids, start=1):
            suffix = f"〔{index}〕" if len(collision_ids) > 1 else ""
            numbered[actor_id] = f"{prefix}·{name}{suffix}"

    requested = {message.actor for message in messages}
    requested.update(actor_ids or [])
    if speaker_id:
        requested.add(speaker_id)
    labels: dict[str, str] = {}
    for actor_id in requested:
        if actor_id == "player":
            name = _player_name(state)
            labels[actor_id] = "玩家" if name == "玩家" else f"玩家·{name}"
        elif actor_id == "director":
            labels[actor_id] = "旁白"
        elif actor_id in numbered:
            labels[actor_id] = numbered[actor_id]

    unknown = sorted(actor_id for actor_id in requested if actor_id not in labels)
    labels.update({actor_id: f"已退出角色〔{index}〕" for index, actor_id in enumerate(unknown, start=1)})
    from mrp.shared.player_identity import message_label
    for message in messages:
        if message.actor == "player" and state.player_identities:
            labels[f"message:{message.id}"] = message_label(state, message, labels)
    return labels


def _addressed_inputs(state: SessionState, messages: list[Message], turn: int,
                      speaker_id: str, labels: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """Recognize explicit vocatives, not names mentioned inside a question.

    Uncertain, collective and narrated text stays shared. Only already-visible
    player messages are used; another card's private description is never read.
    Duplicate names/aliases do not establish a target.
    """
    aliases: dict[str, set[str]] = defaultdict(set)
    for character in state.characters:
        names = [character.card.name, *character.aliases]
        # Cards often use “title-name” as their display name. Recognize the
        # explicit name suffix, but never invent nicknames from descriptions.
        suffix = re.split(r'[-—－]', character.card.name)[-1].strip()
        if suffix != character.card.name.strip() and len(suffix) >= 2:
            names.append(suffix)
        for name in names:
            if name.strip():
                aliases[name.strip()].add(character.id)
    for group in state.groups:
        if group.label.strip():
            aliases[group.label.strip()].add(group.id)
    unique = {name: next(iter(ids)) for name, ids in aliases.items() if len(ids) == 1}
    names = sorted(unique, key=len, reverse=True)
    if not names:
        return [], [], []
    pattern = re.compile(
        r"^(?:然后|接着|随后|再)?\s*(?:我(?:转向|看向|问|对)|请问|请|@)?\s*(?P<name>"
        + "|".join(re.escape(name) for name in names)
        + r")(?=\s*(?:[，,:：]|[，,:：]?\s*(?:请|你|您|只|先|等|接着|问|说|回答)))"
    )
    own, others, shared = [], [], []
    for message in messages:
        if message.turn != turn or message.actor != 'player' or message.status != 'final':
            continue
        if message.kind == 'scene':
            shared.append(message.content)
            continue
        if message.kind != 'roleplay':
            continue
        # Split explicit vocatives outside quotes. Unaddressed sentences remain
        # shared rather than silently inheriting another sentence's recipient.
        parts = []
        start = 0
        quotes = []
        closing = {'“': '”', '「': '」', '『': '』', '"': '"'}
        for index, char in enumerate(message.content):
            if quotes and char == quotes[-1]:
                quotes.pop()
            elif char in closing:
                quotes.append(closing[char])
            if not quotes and (char in '。！？!?；;\n' or (
                    char in '，, ' and pattern.match(message.content[index + 1:].lstrip()))):
                parts.append(message.content[start:index + 1])
                start = index + 1
        parts.append(message.content[start:])
        for part in parts:
            part = part.strip()
            if not part:
                continue
            match = pattern.match(part)
            target = None
            if match:
                target = unique[match.group('name')]
            if target == speaker_id:
                # Keep each coordinated obligation visible, so the final
                # “and tell me ...” is not swallowed by a longer first task.
                own.extend(piece.strip() for piece in re.split(
                    r'(?=以及|(?:再|并且|并|同时)(?:请|说|回答|说明|告诉|复述|提出|提议|给出|确认|决定))', part
                ) if piece.strip())
            elif target:
                others.append(f"{labels.get(target, '其他角色')}：{part}")
            else:
                shared.append(part)
    return own, others, shared


def build_reply_frame(
    state: SessionState,
    speaker_id: str,
    turn: int,
    mode: str,
    visible_messages: list[Message],
    participants: list[str] | None = None,
    *, provenance=None,
) -> ReplyFrame:
    """Build a frame from already-filtered messages, never from hidden history."""
    labels = build_actor_labels(state, visible_messages, speaker_id=speaker_id,
                                actor_ids=participants)
    speaker_kind = "group" if any(group.id == speaker_id for group in state.groups) else (
        "character" if any(character.id == speaker_id for character in state.characters) else "other"
    )
    trigger_ids = [
        message.id for message in visible_messages
        if message.turn == turn and message.actor == "player"
        and message.kind in ("roleplay", "scene") and message.status == "final"
    ]
    reply_ids = []
    if mode == "free":
        visible_ids = {message.id for message in visible_messages}
        trigger_ids = [mid for mid in getattr(provenance, "trigger_message_ids", []) if mid in visible_ids]
        reply_ids = [mid for mid in getattr(provenance, "reply_to_message_ids", []) if mid in visible_ids]
        if provenance is None:
            trigger_ids = [visible_messages[-1].id] if visible_messages else []
            reply_ids = trigger_ids.copy()
    participant_ids = set(participants or [speaker_id])
    prior_ids = []
    prior_speakers = []
    if mode == "serial":
        prior_messages = [
            message for message in visible_messages
            if message.turn == turn and message.actor in participant_ids
            and message.actor != speaker_id and message.kind == "roleplay"
            and message.status == "final"
        ]
        prior_ids = [message.id for message in prior_messages]
        prior_speakers = list(dict.fromkeys(labels.get(message.actor, "前序发言者") for message in prior_messages))
    own, others, shared = _addressed_inputs(state, visible_messages, turn, speaker_id, labels)
    return ReplyFrame(
        speaker_id=speaker_id,
        speaker_kind=speaker_kind,
        speaker_label=labels.get(speaker_id, "当前发言者"),
        mode=mode if mode in ("single", "serial", "parallel", "preview", "free") else "single",
        turn=turn,
        trigger_message_ids=trigger_ids,
        reply_to_message_ids=reply_ids,
        visible_prior_reply_ids=prior_ids,
        visible_prior_speaker_labels=prior_speakers,
        actor_labels=labels,
        addressed_inputs=own,
        other_addressed_inputs=others,
        shared_inputs=shared,
    )

