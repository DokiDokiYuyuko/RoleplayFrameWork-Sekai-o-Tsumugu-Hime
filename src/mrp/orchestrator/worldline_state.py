"""Branch-local generation-state checkpoints and read-only anchor lookup.

Snapshots deliberately exclude message bodies; a branch point pairs one
checkpoint with the message prefix that was visible at that point. Memory
watermarks are added by M17 before historical forks become writable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from mrp.shared.models import (
    Character, GroupActor, Lorebook, Message, PinnedFact, Scene, SessionState,
    StateRevision, StoryEvent,
)


from mrp.shared.session_policy import GENERATION_MATERIAL_META_FIELDS

_META_FIELDS = GENERATION_MATERIAL_META_FIELDS


def current_state_projection(state: SessionState) -> dict:
    """Capture values that can affect future story generation, without messages."""
    meta = state.meta.model_dump(mode="json", include=set(_META_FIELDS))
    return {
        "meta": meta,
        "characters": [item.model_dump(mode="json") for item in state.characters],
        "lorebooks": [item.model_dump(mode="json") for item in state.lorebooks],
        "pinned_facts": [item.model_dump(mode="json") for item in state.pinned_facts],
        "scenes": [item.model_dump(mode="json") for item in state.scenes],
        "active_scene_id": state.active_scene_id,
        "groups": [item.model_dump(mode="json") for item in state.groups],
        "character_joined_at_seq": dict(state.character_joined_at_seq),
        "character_entry_briefs": dict(state.character_entry_briefs),
        "player_identities": [x.model_dump(mode="json") for x in state.player_identities],
        "player_people": {k: v.model_dump(mode="json") for k, v in state.player_people.items()},
    }


def projection_hash(snapshot: dict[str, Any]) -> str:
    """Hash a canonical JSON projection, independent of dict insertion order."""
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apply_projection(state: SessionState, snapshot: dict[str, Any]) -> None:
    """Restore exactly the generation state held by a branch checkpoint."""
    state.meta.world_runtime_policy = snapshot.get('meta', {}).get('world_runtime_policy', 'legacy_full')
    for name, value in snapshot.get("meta", {}).items():
        if name in _META_FIELDS:
            setattr(state.meta, name, value)
    state.characters = [Character.model_validate(item) for item in snapshot.get("characters", [])]
    state.lorebooks = [Lorebook.model_validate(item) for item in snapshot.get("lorebooks", [])]
    state.pinned_facts = [PinnedFact.model_validate(item) for item in snapshot.get("pinned_facts", [])]
    state.scenes = [Scene.model_validate(item) for item in snapshot.get("scenes", [])]
    state.active_scene_id = snapshot.get("active_scene_id")
    # Older snapshots hash as stored; only after their raw hash is checked do we
    # materialize the additive group collection with an empty default.
    state.groups = [GroupActor.model_validate(item) for item in snapshot.get("groups", [])]
    state.character_joined_at_seq = {
        str(key): int(value) for key, value in snapshot.get("character_joined_at_seq", {}).items()
    }
    state.character_entry_briefs = {
        str(key): str(value) for key, value in snapshot.get("character_entry_briefs", {}).items()
    }
    from mrp.shared.models import PlayerIdentity
    state.player_identities = [PlayerIdentity.model_validate(x) for x in snapshot.get("player_identities", [])]
    state.player_people = {k: Character.model_validate(v) for k, v in snapshot.get("player_people", {}).items()}
    state.meta.player_identity_id = snapshot.get("meta", {}).get("player_identity_id")


def _head_revision(state: SessionState) -> StateRevision | None:
    return next(
        (revision for revision in state.state_revisions
         if revision.id == state.head_state_revision_id),
        None,
    )


def record_message_revision(
    state: SessionState, message: Message, memory_watermark: int | None = None,
) -> None:
    """Bind a final message to the state actually in force after it lands.

    Adjacent messages reuse the same runtime revision when no generation state
    changed. Legacy/head-only revisions are not reused: they do not prove a
    real per-message checkpoint.
    """
    if state.schema_version < 3 or message.status != "final":
        return
    snapshot = current_state_projection(state)
    digest = projection_hash(snapshot)
    current = _head_revision(state)
    if (current is not None and current.source == "runtime"
            and current.snapshot == snapshot
            and current.memory_watermark == memory_watermark):
        revision = current
    else:
        revision = StateRevision(
            after_message_id=message.id,
            snapshot=snapshot,
            content_hash=digest,
            memory_watermark=memory_watermark,
            source="runtime",
        )
        state.state_revisions.append(revision)
    message.post_state_revision_id = revision.id
    state.head_state_revision_id = revision.id


def record_persisted_head(
    state: SessionState, memory_watermark: int | None = None,
) -> None:
    """Capture state changes without a message (settings, pins, roster, books).

    The snapshot's memory watermark remains unknown until M17. This revision
    is the branch head, not a replacement for the last message's checkpoint.
    """
    if state.schema_version < 3:
        return
    last_final = next(
        (message for message in reversed(state.messages) if message.status == "final"),
        None,
    )
    anchor_id = last_final.id if last_final else None
    snapshot = current_state_projection(state)
    current = _head_revision(state)
    if (current is not None and current.snapshot == snapshot
            and current.memory_watermark == memory_watermark):
        return
    legacy_head = current is not None and current.source == "legacy_head"
    legacy_anchor = legacy_head and current.after_message_id == anchor_id
    revision = StateRevision(
        after_message_id=anchor_id,
        snapshot=snapshot,
        content_hash=projection_hash(snapshot),
        memory_watermark=memory_watermark,
        source="legacy_head" if legacy_anchor else "runtime",
    )
    state.state_revisions.append(revision)
    state.head_state_revision_id = revision.id
    if legacy_anchor and last_final is not None:
        last_final.post_state_revision_id = revision.id


def reconcile_revisions(state: SessionState) -> None:
    """Drop checkpoints whose messages were physically removed in this branch.

    A revision shared by surviving messages stays available. The current head
    also stays available, but its anchor is cleared if that message was removed.
    Callers must first prevent deletion of an anchor used by a child branch.
    """
    if state.schema_version < 3:
        return
    first_ref: dict[str, str] = {}
    for message in state.messages:
        if message.post_state_revision_id:
            first_ref.setdefault(message.post_state_revision_id, message.id)
    remaining_ids = {message.id for message in state.messages}
    kept: list[StateRevision] = []
    for revision in state.state_revisions:
        if revision.id not in first_ref and revision.id != state.head_state_revision_id:
            continue
        if revision.after_message_id not in remaining_ids:
            revision.after_message_id = first_ref.get(revision.id)
        kept.append(revision)
    state.state_revisions = kept
    if state.head_state_revision_id not in {revision.id for revision in kept}:
        state.head_state_revision_id = None


@dataclass(frozen=True)
class BranchPoint:
    branch_id: str
    message_id: str
    revision: StateRevision
    messages: tuple[Message, ...]
    events: tuple[StoryEvent, ...]


class BranchPointUnavailable(ValueError):
    """The anchor is absent or its historic state was never recorded."""


def state_at(state: SessionState, message_id: str) -> BranchPoint:
    """Read an anchor without mutating the current branch or running migration.

    Copies guard the caller from accidentally editing live state. An old v2
    message with no checkpoint is rejected instead of borrowing the head.
    """
    index = next((i for i, msg in enumerate(state.messages) if msg.id == message_id), None)
    if index is None:
        raise BranchPointUnavailable("消息不在当前分支中")
    message = state.messages[index]
    revision = next(
        (rev for rev in state.state_revisions if rev.id == message.post_state_revision_id),
        None,
    )
    if revision is None or revision.source == "head_only" or message.status != "final":
        raise BranchPointUnavailable("此历史消息没有可用的状态修订")
    if revision.content_hash and revision.content_hash != projection_hash(revision.snapshot):
        raise BranchPointUnavailable("状态修订校验失败，不能使用该分叉点")
    included_ids = {msg.id for msg in state.messages[:index + 1]}
    return BranchPoint(
        branch_id=state.meta.id,
        message_id=message_id,
        revision=revision.model_copy(deep=True),
        messages=tuple(msg.model_copy(deep=True) for msg in state.messages[:index + 1]),
        events=tuple(event.model_copy(deep=True) for event in state.story_events
                     if event.anchor_message_id in included_ids),
    )


async def load_state_at(repo: Any, branch_id: str, message_id: str) -> BranchPoint:
    """Repository-backed, read-only branch point lookup for the fork service."""
    state = await repo.load_state_readonly(branch_id)
    if state is None:
        raise BranchPointUnavailable("世界线不存在或无法读取")
    return state_at(state, message_id)
