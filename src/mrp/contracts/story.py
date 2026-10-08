"""Story read contracts. Persistence models never cross this boundary implicitly."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from mrp.shared.models import (
    Message, GenerationMeta, GenerationProvenance, MessageVariant, SessionMeta,
    Character, ConversationRun, TurnRun, GroupActor, Scene, PinnedFact,
    PlayerIdentity, DirectorDecision,
)

STORY_EVENT_TYPES = (
    "message.pending", "director.decision", "message.final", "message.retracted", "cost.update",
    "message.updated", "message.deleted", "message.error", "message.delta", "scene.switched",
    "director.pending", "reply.plan", "session.roster.changed", "session.player.changed",
    "turn.run.updated", "conversation.run.updated",
)


def _fields(model, names):
    return {name: (model.model_fields[name].annotation, deepcopy(model.model_fields[name]))
            for name in names}


# Explicit allowlists keep new persistence fields out of the wire by default.
ProvenanceView = create_model("ProvenanceView", **_fields(GenerationProvenance, (
    "run_id", "step_id", "mode", "participants", "trigger_message_ids",
    "reply_to_message_ids", "sources", "scheduling_sources", "complete",
)))
GenerationView = create_model("GenerationView", provenance=(ProvenanceView | None, None),
    **_fields(GenerationMeta, ("generation_id", "operation_id", "attempt_id", "message_id",
        "request_ids", "plan_id", "model", "usage", "finish_reason", "completion_state",
        "injected_entry_ids", "prompt_tokens_by_section")))
VariantView = create_model("VariantView", generation_meta=(GenerationView | None, None),
    **_fields(MessageVariant, ("id", "content", "hygiene", "created_at", "accepted_dependency_sources")))
MessageView = create_model("MessageView", generation_meta=(GenerationView | None, None),
    variants=(list[VariantView], Field(default_factory=list)), **_fields(Message, (
        "id", "session_id", "seq", "turn", "actor", "content", "kind", "visible_to", "status",
        "fingerprint", "created_at", "active_variant", "edited", "hygiene", "scene_id", "mentions",
        "input_group_id", "reply_mode", "executed_reply_mode", "reply_order", "reply_reason",
        "reply_basis_fingerprint", "reply_shared_scene", "idempotency_key", "post_state_revision_id",
        "player_identity_id", "person_id", "known_to", "control_event", "generation_id",
        "operation_id", "attempt_id", "dependency_stale", "dependency_stale_sources", "accepted_dependency_sources",
    )))
TurnRunView = create_model("TurnRunView", **_fields(TurnRun, (
    "id", "session_id", "operation_id", "request_fingerprint", "player_identity_id", "scene_id",
    "turn", "status", "epoch", "input_message_ids", "slots", "plan", "followups",
    "prepared_scene_message_ids", "padding_prepared", "basis_fingerprint", "errors", "last_error",
    "stop_reason", "created_at", "updated_at",
)))


ConversationRunView = create_model("ConversationRunView", **_fields(ConversationRun, (
    "id", "session_id", "operation_id", "request_fingerprint", "mode", "participant_ids",
    "scene_id", "player_identity_id", "directive", "max_replies", "completed_replies",
    "status", "stage", "current_speaker_id", "stop_reason", "last_error", "epoch",
    "pause_requested", "stop_requested", "last_committed_step_id", "last_committed_message_id",
    "cumulative_usage", "usage_incomplete", "turn", "seed_message_ids", "current_step_id",
    "current_message_id", "current_generation_id", "current_attempt_id", "current_trigger_message_ids",
    "current_reply_to_message_ids", "current_usage", "created_at", "updated_at",
)))


SETUP_FIELDS = (
    "id", "title", "branch_revision", "player_persona", "player_character_id", "player_identity_id",
    "character_ids", "lorebook_ids", "reply_max_tokens", "options_enabled", "options_style",
    "options_direct_send", "streaming_enabled", "hygiene_enabled", "director_mode", "memory_enabled",
    "memory_interval_turns", "memory_top_k", "memory_compress_horizon_turns", "narrative_pov",
    "narrative_density", "short_input_padding", "proactive_turn_limit", "prompt_preset_id",
    "response_style_id", "response_style_overrides",
)
SetupMeta = create_model("SetupMeta", **_fields(SessionMeta, SETUP_FIELDS))
BranchSummary = create_model("BranchSummary", turn=(int, 0), persona=(str, ""),
    character_names=(list[str], Field(default_factory=list)), **_fields(SessionMeta, (
        "id", "title", "created_at", "story_id", "branch_name", "parent_branch_id",
        "branch_revision", "archived", "source_world_id", "character_ids", "player_character_id",
        "reply_max_tokens", "prompt_preset_id", "response_style_id", "response_style_overrides",
        "options_enabled", "options_style", "options_direct_send", "streaming_enabled",
        "hygiene_enabled", "director_mode", "narrative_pov", "narrative_density", "short_input_padding",
    )))
SessionView = create_model("SessionView", __base__=BranchSummary,
    player_identities=(list[PlayerIdentity], Field(default_factory=list)),
    player_people=(dict[str, Character], Field(default_factory=dict)),
    pinned_facts=(list[PinnedFact], Field(default_factory=list)), **_fields(SessionMeta, (
        "player_identity_id", "source_world_revision", "world_core_brief", "memory_enabled",
        "proactive_turn_limit",
    )))


class LorebookSummary(BaseModel):
    id: str
    name: str
    source_format: str
    entry_count: int
    entries: list[Any] = Field(default_factory=list)


class SessionSetup(BaseModel):
    meta: SetupMeta
    characters: list[Character]
    lorebooks: list[LorebookSummary]


class MessagePage(BaseModel):
    messages: list[MessageView]
    next_before_seq: int | None = None
    branch_revision: int
    turn: int
    latest_seq: int = -1


class StoryView(MessagePage):
    contract_version: Literal[1] = 1
    session: SessionView
    characters: list[Character]
    groups: list[GroupActor]
    scenes: list[Scene]
    active_scene_id: str | None
    lorebooks: list[Any] = Field(default_factory=list)
    player_identities: list[PlayerIdentity]
    player_people: dict[str, Character]
    turn_runs: list[TurnRunView]
    conversation_runs: list[ConversationRunView]
    pending_director: DirectorDecision | None
    event_cursor: str | None


class CommandResult(BaseModel):
    operation_id: str
    branch_revision: int
    messages: list[MessageView] = Field(default_factory=list)


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


def project_message(value: Message | dict) -> dict:
    """Never mutate a live message or a persistence operation result."""
    if isinstance(value, BaseModel):
        generation = {name: True for name in GenerationView.model_fields}
        generation["provenance"] = {name: True for name in ProvenanceView.model_fields}
        variant = {name: True for name in VariantView.model_fields}
        variant["generation_meta"] = generation
        include = {name: True for name in MessageView.model_fields}
        include["generation_meta"] = generation
        include["variants"] = {"__all__": variant}
        value = value.model_dump(mode="json", include=include)
    return MessageView.model_validate(value).model_dump(mode="json")


def project_turn_run(value: TurnRun | dict) -> dict:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", include=set(TurnRunView.model_fields))
    return TurnRunView.model_validate(value).model_dump(mode="json")



def project_conversation_run(value: ConversationRun | dict) -> dict:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", include=set(ConversationRunView.model_fields))
    return ConversationRunView.model_validate(value).model_dump(mode="json")


def project_character(value: Character) -> dict:
    # Keep the existing Character shape for roster consumers, without prompt manuscripts.
    keep = {"name", "avatar_path", "tags", "source_format", "spec", "spec_version",
            "description", "appearance", "traits", "traits_label", "personality", "scenario",
            "first_mes", "alternate_greetings", "creator", "character_version"}
    include = {name: True for name in Character.model_fields if name != "authoring_source"}
    include["card"] = keep
    data = value.model_dump(mode="json", include=include)
    return Character.model_validate(data).model_dump(mode="json", exclude={"authoring_source"})


def project_event(event: str, payload: dict) -> dict:
    result = dict(payload)
    for key in ("message", "transition_message"):
        value = result.get(key)
        if isinstance(value, dict) and {"id", "session_id", "seq", "actor", "content"} <= value.keys():
            result[key] = project_message(value)
    if event == "turn.run.updated" and isinstance(result.get("run"), dict):
        result["run"] = project_turn_run(result["run"])
    if event == "conversation.run.updated" and isinstance(result.get("run"), dict):
        result["run"] = project_conversation_run(result["run"])
    return result
