"""Story-local identities. UI account visibility never grants character knowledge."""
from __future__ import annotations

import hashlib
from mrp.shared.models import Character, Message, PlayerIdentity, SessionState


def current_identity(state: SessionState) -> PlayerIdentity | None:
    return next((x for x in state.player_identities if x.id == state.meta.player_identity_id), None)


def message_identity(state: SessionState, message: Message) -> PlayerIdentity | None:
    return next((x for x in state.player_identities if x.id == message.player_identity_id), None)


def message_person(state: SessionState, message: Message) -> str | None:
    if message.person_id:
        return message.person_id
    if message.actor != "player":
        return message.actor if message.actor != "director" else None
    identity = message_identity(state, message)
    return identity.person_id if identity else None


def player_key(state: SessionState) -> str:
    identity = current_identity(state)
    return identity.person_id if identity else "player"


def message_label(state: SessionState, message: Message, labels: dict[str, str]) -> str:
    if message.actor == "player":
        identity = message_identity(state, message)
        if identity:
            return f"玩家·{identity.name}〔{identity.person_id}〕"
        if state.player_identities:
            return "玩家〔历史身份未确认〕"
    return labels.get(message.actor, "已退出角色")


def _name(persona: str) -> str:
    first = persona.strip().splitlines()[0] if persona.strip() else ""
    return first.removeprefix("姓名：").strip() if first.startswith("姓名：") else "玩家"


def ensure_player_identity(state: SessionState, character: Character | None = None) -> None:
    """Only proven checkpoints bind legacy messages; never guess from dialogue."""
    if state.player_identities:
        return
    from mrp.shared.prompt import player_persona_from_card
    head = (state.meta.player_character_id, state.meta.player_persona)
    revisions = {r.id: r for r in state.state_revisions}
    last_signature = None
    identity = None
    for message in sorted(state.messages, key=lambda m: m.seq):
        revision = revisions.get(message.post_state_revision_id)
        if revision is None:
            continue
        meta = revision.snapshot.get("meta", {})
        if "player_persona" not in meta:
            continue
        signature = (meta.get("player_character_id"), meta["player_persona"])
        if signature != last_signature:
            identity = PlayerIdentity(person_id=signature[0] or "person-" + hashlib.sha256(
                f"{state.meta.story_id or state.meta.id}:{signature[1]}".encode()).hexdigest()[:12],
                source_character_id=signature[0], name=_name(signature[1]), persona=signature[1],
                start_seq=message.seq)
            state.player_identities.append(identity)
            last_signature = signature
        message.player_identity_id = identity.id
        if message.actor == "player":
            message.person_id = identity.person_id
    if identity is None or last_signature != head:
        identity = PlayerIdentity(person_id=head[0] or "person-" + hashlib.sha256(
            f"{state.meta.story_id or state.meta.id}:{head[1]}".encode()).hexdigest()[:12],
            source_character_id=head[0], name=_name(head[1]), persona=head[1], start_seq=state.next_seq())
        state.player_identities.append(identity)
    # A library card is authoritative for a new story, not for unproven old history.
    if character is not None:
        if not state.messages or player_persona_from_card(character.card) == identity.persona:
            state.player_people[identity.person_id] = character.model_copy(deep=True)
            identity.character = character.model_copy(deep=True)
            identity.name = character.card.name
            identity.persona = player_persona_from_card(character.card)
    state.meta.player_identity_id = identity.id
    state.meta.player_persona = identity.persona
    state.character_joined_at_seq.setdefault(identity.person_id, 0 if not state.messages else identity.start_seq)


def bind_message(state: SessionState, message: Message) -> None:
    identity = current_identity(state)
    if identity is None:
        return
    if message.player_identity_id is None:
        message.player_identity_id = identity.id
    if message.person_id is None:
        message.person_id = identity.person_id if message.actor == "player" else (
            message.actor if message.actor != "director" else None)
    if message.known_to is None:
        if message.control_event:
            message.known_to = []
        elif message.kind == "inner" and message.actor == "player":
            message.known_to = [identity.person_id]
        else:
            candidates = [c.id for c in state.characters if c.present] + [identity.person_id]
            candidates += [g.id for g in state.groups if g.status == "active" and g.scene_id == state.active_scene_id]
            message.known_to = list(dict.fromkeys(candidates if message.visible_to == "all" else
                [x for x in candidates if x in message.visible_to or (x == identity.person_id and "player" in message.visible_to)]))


def before_control_boundary(state: SessionState, message: Message) -> bool:
    identity = current_identity(state)
    return bool(identity and (message.seq < identity.start_seq or
        (message.player_identity_id is not None and message.player_identity_id != identity.id)))


def current_inner(state: SessionState, message: Message) -> bool:
    identity = current_identity(state)
    return identity is None or message.player_identity_id == identity.id


def memory_participants(state: SessionState, record) -> set[str]:
    ids = set(record.participant_ids)
    if "player" not in ids:
        return ids
    ids.discard("player")
    sources = [m for m in state.messages if m.id in record.source_message_ids and m.actor == "player"]
    people = {message_person(state, m) for m in sources}
    if sources and None not in people:
        ids.update(people)
        return ids
    if len(state.player_identities) == 1:
        ids.add(state.player_identities[0].person_id)
        return ids
    ids.add("player")
    return ids


def identity_source(state: SessionState) -> dict[str, str | None]:
    identity = current_identity(state)
    return ({"control_stage_id": identity.id, "person_id": identity.person_id,
             "character_id": identity.source_character_id, "name": identity.name}
            if identity else {})
