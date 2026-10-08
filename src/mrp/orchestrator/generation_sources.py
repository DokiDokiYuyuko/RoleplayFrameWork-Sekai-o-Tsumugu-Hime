"""Versioned generation inputs and branch-local dependency checks.

Both generation and scheduling callers use these hooks. They never create a
branch or infer dependencies from matching prose or speaker names.
"""
from __future__ import annotations

from mrp.shared.models import GenerationProvenance, Message, MessageSourceRef, SessionState
from mrp.orchestrator.worldline_state import apply_projection, current_state_projection


def source_ref(message: Message) -> MessageSourceRef:
    variant = (message.variants[message.active_variant]
               if message.active_variant is not None and 0 <= message.active_variant < len(message.variants)
               else None)
    return MessageSourceRef(message_id=message.id, variant_id=variant.id if variant else None,
                            fingerprint=message.fingerprint)


def source_matches(ref: MessageSourceRef, message: Message | None) -> bool:
    # A first swipe materializes the original candidate. Its former null
    # variant id must still match that original text when the user switches back.
    return bool(message is not None and message.status == "final"
                and message.fingerprint == ref.fingerprint
                and (ref.variant_id is None or source_ref(message).variant_id == ref.variant_id))


def make_provenance(state: SessionState, mode: str = "single", participants=None,
                    *, shared_scene: str = "", shared_frame: str = "",
                    reply_to_message_ids=None, scheduling_sources=None, director_directive="") -> GenerationProvenance:
    return GenerationProvenance(
        mode=mode if mode in {"single", "serial", "parallel", "free"} else "single",
        participants=list(participants or []),
        trigger_message_ids=[m.id for m in state.messages if m.turn == state.current_turn()
                             and m.actor == "player" and m.kind in {"scene", "roleplay"}
                             and m.status == "final"],
        reply_to_message_ids=list(reply_to_message_ids or []),
        scheduling_sources=list(scheduling_sources or []),
        baseline_message_ids=[m.id for m in state.messages if m.status == "final"],
        baseline_state=current_state_projection(state),
        baseline_state_revision_id=state.head_state_revision_id,
        baseline_branch_revision=state.meta.branch_revision,
        shared_scene=shared_scene, shared_frame=shared_frame, complete=True,
        director_directive=director_directive,
    )


def finish_provenance(provenance: GenerationProvenance, state: SessionState,
                      visible: list[Message], *, included_ids=None, recall_audit=None,
                      include_player_inner: bool = True) -> GenerationProvenance:
    result = provenance.model_copy(deep=True)
    # Visible messages are read by worldbook matching and recall query building
    # even when the prompt capacity omits their raw text. Track these reads too.
    sources = {m.id: source_ref(m) for m in visible if m.status == "final"
               and not m.id.startswith("summary-")}
    all_messages = {m.id: m for m in state.messages}
    for row in recall_audit or []:
        if row.get("disposition") == "injected":
            for mid in row.get("source_message_ids", []):
                if mid in all_messages:
                    sources[mid] = source_ref(all_messages[mid])
    from mrp.shared.player_identity import current_inner
    inner = [m for m in state.messages if m.actor == "player" and m.kind == "inner"
             and m.status == "final" and current_inner(state, m)][-2:] if include_player_inner else []
    for m in inner:
        sources[m.id] = source_ref(m)
    result.sources = list(sources.values())
    visible_ids = {m.id for m in visible}
    result.trigger_message_ids = [mid for mid in result.trigger_message_ids if mid in visible_ids]
    result.reply_to_message_ids = [mid for mid in result.reply_to_message_ids if mid in visible_ids]
    return result


def generation_provenance(message: Message) -> GenerationProvenance | None:
    return message.generation_meta.provenance if message.generation_meta else None


def memory_matches_baseline(state: SessionState, record) -> bool:
    """A frozen node cannot read a later memory revision, even with old sources."""
    if not getattr(state, "_generation_frozen", False):
        return True
    override = getattr(state, "_generation_memory_overrides", {}).get(record.id)
    if override is not None:
        return override.revision == record.revision and override.content == record.content
    watermark = getattr(state, "_generation_memory_watermark", None)
    if watermark is not None and record.commit_seq > watermark:
        return False
    cutoff = getattr(state, "_generation_memory_cutoff", None)
    return cutoff is None or record.created_at <= cutoff


def dependency_descendants(state: SessionState, message_id: str) -> list[Message]:
    changed = {message_id}
    out = []
    for message in sorted(state.messages, key=lambda m: m.seq):
        provenance = generation_provenance(message)
        if message.id == message_id or provenance is None:
            continue
        refs = [*provenance.sources, *provenance.scheduling_sources]
        if changed.intersection([ref.message_id for ref in refs] + provenance.reply_to_message_ids):
            out.append(message)
            changed.add(message.id)
    return out


def recompute_dependency_state(state: SessionState) -> list[Message]:
    live = {m.id: m for m in state.messages}
    changed = []
    for message in sorted(state.messages, key=lambda m: m.seq):
        provenance = generation_provenance(message)
        stale = []
        accepted = {ref.message_id: ref for ref in message.accepted_dependency_sources}
        if provenance:
            for ref in [*provenance.sources, *provenance.scheduling_sources]:
                source = live.get(ref.message_id)
                override = accepted.get(ref.message_id)
                valid_override = override is not None and source_matches(override, source)
                if not valid_override and (not source_matches(ref, source)
                                           or (source is not None and source.dependency_stale)):
                    stale.append(ref.message_id)
        stale = list(dict.fromkeys(stale))
        if message.dependency_stale != bool(stale) or message.dependency_stale_sources != stale:
            message.dependency_stale = bool(stale)
            message.dependency_stale_sources = stale
            changed.append(message)
    return changed


def generation_baseline(state: SessionState, target: Message) -> tuple[SessionState, GenerationProvenance]:
    """Rebuild this node's old boundary, using current chosen predecessor text."""
    provenance = generation_provenance(target)
    snap = state.model_copy(deep=True)
    if provenance is not None and provenance.complete and provenance.baseline_state:
        provenance = provenance.model_copy(deep=True)
        allowed = set(provenance.baseline_message_ids)
        if provenance.baseline_state:
            apply_projection(snap, provenance.baseline_state)
        snap.messages = [m for m in snap.messages if m.id in allowed and m.id != target.id]
    else:
        # Compatibility: the latest interaction can reconstruct explicit
        # parallel/serial boundaries; historical unknown boundaries are rejected
        # by the service before reaching this function.
        player = next((m for m in reversed(state.messages) if m.seq < target.seq
                       and m.turn == target.turn and m.actor == "player"
                       and m.kind in {"roleplay", "scene"}), None)
        mode = player.executed_reply_mode if player else "single"
        participants = player.reply_order if player else [target.actor]
        boundary = target.seq
        if mode == "parallel":
            peers = [m.seq for m in state.messages if m.turn == target.turn
                     and m.actor in participants and m.kind == "roleplay"]
            boundary = min(peers, default=target.seq)
        snap.messages = [m for m in snap.messages if m.seq < boundary]
        anchor = next((m for m in reversed(snap.messages) if m.post_state_revision_id), None)
        revision = next((r for r in snap.state_revisions
                         if anchor is not None and r.id == anchor.post_state_revision_id), None)
        if revision is not None and revision.source == "runtime":
            apply_projection(snap, revision.snapshot)
        else:
            from mrp.orchestrator.message_regeneration import RegenerationConflict
            raise RegenerationConflict("这条旧回应缺少生成时的历史材料快照，请使用整轮重新回应")
        provenance = make_provenance(snap, mode or "single", participants,
                                     shared_scene=player.reply_shared_scene if player else "")
    # An unaccepted dependent is displayed but cannot silently become an input.
    snap.messages = [m for m in snap.messages if not m.dependency_stale]
    allowed_ids = {m.id for m in snap.messages}
    snap.pinned_facts = [f for f in snap.pinned_facts
                        if f.source_message_id is None or f.source_message_id in allowed_ids]
    snap.story_events = [e for e in snap.story_events if e.anchor_message_id in allowed_ids]
    provenance.baseline_message_ids = [m.id for m in snap.messages]
    correction_records = provenance.baseline_state.get("_corrected_memory_records", [])
    correction_origin = provenance.baseline_state.get("_correction_origin")
    policy_injections = provenance.baseline_state.get("_generation_policy_injections")
    provenance.baseline_state = current_state_projection(snap)
    if correction_records:
        provenance.baseline_state["_corrected_memory_records"] = correction_records
    if correction_origin:
        provenance.baseline_state["_correction_origin"] = correction_origin
    if policy_injections is not None:
        provenance.baseline_state["_generation_policy_injections"] = policy_injections
    provenance.baseline_branch_revision = state.meta.branch_revision
    # These are runtime-only properties, never part of the persisted projection.
    object.__setattr__(snap, "_generation_frozen", True)
    object.__setattr__(snap, "_generation_memory_watermark", provenance.baseline_memory_watermark)
    object.__setattr__(snap, "_generation_memory_cutoff", target.created_at)
    from mrp.shared.models import MemoryRecord
    overrides = {}
    for raw in correction_records:
        record = MemoryRecord.model_validate(raw)
        if record.character_id == target.actor and record.source_message_ids and all(
                mid in allowed_ids for mid in record.source_message_ids):
            record.session_id = snap.meta.id
            overrides[record.id] = record
    object.__setattr__(snap, "_generation_memory_overrides", overrides)
    object.__setattr__(snap, "_correction_origin", correction_origin)
    if policy_injections is not None:
        object.__setattr__(snap, "_generation_policy_injections", policy_injections)
    if provenance.mode == "free":
        object.__setattr__(snap, "_conversation_turn", target.turn)
        object.__setattr__(snap, "_conversation_directive", provenance.director_directive)
    return snap, provenance
