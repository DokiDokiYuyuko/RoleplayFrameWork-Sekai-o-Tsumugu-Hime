"""Explicit field ownership and historical policy for every SessionMeta field.

Adding a model field requires a policy decision; contract tests reject omissions.
Legacy snapshot membership is frozen independently of new generation materials.
"""
from dataclasses import dataclass
from types import MappingProxyType

GENERATION_MATERIAL_META_FIELDS = (
    "response_style_id", "response_style_overrides", "player_persona", "player_character_id",
    "player_identity_id", "reply_max_tokens", "character_ids", "lorebook_ids", "options_enabled",
    "options_style", "options_direct_send", "streaming_enabled", "hygiene_enabled", "director_mode",
    "memory_enabled", "memory_interval_turns", "memory_top_k", "memory_compress_horizon_turns",
    "narrative_pov", "narrative_density", "short_input_padding", "proactive_turn_limit",
    "scenario_instructions", "source_scenario_id", "source_package_id", "source_package_revision",
    "source_world_id", "source_world_revision", "world_core_brief", "world_archive_records",
    "world_runtime_policy", "prompt_preset_snapshot",
)
LEGACY_SNAPSHOT_META_FIELDS = tuple(name for name in GENERATION_MATERIAL_META_FIELDS
    if name not in {"response_style_id", "response_style_overrides", "player_identity_id", "prompt_preset_snapshot"})
BRANCH_FIELDS = (
    "id", "title", "story_id", "branch_name", "parent_branch_id", "fork_message_id",
    "fork_state_revision_id", "fork_save_id", "fork_request_hash", "branch_revision", "archived", "created_at",
)


@dataclass(frozen=True)
class FieldPolicy:
    scope: str
    historical: str
    api: str = "explicit_projection"
    default: str = "model_default"


SESSION_FIELD_POLICIES = MappingProxyType({
    **{name: FieldPolicy("branch", "generation_snapshot") for name in GENERATION_MATERIAL_META_FIELDS},
    **{name: FieldPolicy("branch", "branch_identity") for name in BRANCH_FIELDS},
    "prompt_preset_id": FieldPolicy("branch", "live_selection_legacy_compatibility"),
})


# The compatibility aggregate is not a license to expose new fields in every API.
# Keep this list explicit: a newly persisted field needs its own review decision.
SESSION_STATE_FIELD_POLICIES = MappingProxyType({
    "schema_version": FieldPolicy("storage", "format_capability", "legacy_export"),
    "meta": FieldPolicy("branch", "see_SESSION_FIELD_POLICIES", "explicit_projection", "required"),
    "messages": FieldPolicy("branch", "message_variants_and_anchors", "windowed_message_view"),
    "usage_records": FieldPolicy("audit", "retain_paid_usage", "usage_projection"),
    "usage_incomplete": FieldPolicy("audit", "retain_paid_usage", "usage_projection"),
    "conversation_runs": FieldPolicy("branch", "durable_attempts", "run_summary"),
    "turn_runs": FieldPolicy("branch", "durable_attempts", "run_summary"),
    "pending_director": FieldPolicy("branch", "checkpoint_proposal", "director_projection"),
    "director_log": FieldPolicy("branch", "decision_audit", "legacy_export"),
    "pinned_facts": FieldPolicy("branch", "generation_snapshot", "story_view"),
    "characters": FieldPolicy("branch", "captured_material", "character_projection"),
    "lorebooks": FieldPolicy("branch", "captured_material", "setup_and_material_projection"),
    "scenes": FieldPolicy("branch", "state_revision", "scene_projection"),
    "active_scene_id": FieldPolicy("branch", "state_revision", "story_view"),
    "groups": FieldPolicy("branch", "state_revision", "group_projection"),
    "character_joined_at_seq": FieldPolicy("branch", "visibility_boundary", "explicit_projection"),
    "character_entry_briefs": FieldPolicy("branch", "generation_snapshot", "explicit_projection"),
    "state_revisions": FieldPolicy("branch", "immutable_revision_material", "legacy_export"),
    "head_state_revision_id": FieldPolicy("branch", "revision_head", "legacy_export"),
    "story_events": FieldPolicy("branch", "anchored_annotations", "event_projection"),
    "bookmarks": FieldPolicy("branch", "anchored_annotations", "bookmark_projection"),
    "player_identities": FieldPolicy("branch", "identity_history", "identity_projection"),
    "player_people": FieldPolicy("branch", "captured_material", "identity_projection"),
    "generation_operations": FieldPolicy("branch", "durable_operation_identity", "legacy_export"),
})
