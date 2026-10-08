"""Explicit memory and story lifecycle contracts; legacy restore remains additive."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from mrp.shared.models import MemoryRecord
from .story import _fields


MemoryRecordView = create_model("MemoryRecordView", **_fields(MemoryRecord, (
    "id", "character_id", "session_id", "turn_start", "turn_end", "kind", "content",
    "source_message_ids", "effective_message_id", "commit_seq", "inherited_from_id",
    "invalidated", "created_at", "participants", "keywords", "importance", "scene_id",
    "category", "participant_ids", "source_fingerprints", "evidence", "important",
    "revision", "manually_revised", "source_changed", "revisions", "matter_status", "supersedes",
)))


class MemoryRecordsResult(BaseModel):
    records: list[MemoryRecordView] = Field(default_factory=list)
    operation_id: str | None = None
    status: Literal["committed"] | None = None
    branch_revision: int | None = None


class MemoryDeleteResult(BaseModel):
    ok: bool


class MemoryScheduledResult(BaseModel):
    scheduled: Literal[True] = True
    operation_id: str
    status: Literal["pending"] = "pending"


class MemoryJobView(BaseModel):
    operation_id: str
    status: Literal["pending", "committed", "failed"]
    fingerprint: str
    result: MemoryRecordsResult | None = None
    branch_revision: int | None = None
    error: str | None = None


class LegacyRestoreResult(BaseModel):
    """Preserve the legacy full-state response; new readers should use StoryView."""
    model_config = ConfigDict(extra="allow")
    meta: dict[str, Any]
    memory_restore_status: Literal["complete", "unavailable"] | None = None


class StoryListSummary(BaseModel):
    story_id: str
    title: str
    cover_id: str | None = None
    branch_count: int
    event_count: int
    save_count: int = 0
    latest_branch_id: str
    updated_at: str | float
    membership_revision: str | None = None


class WorldlineBranchView(BaseModel):
    id: str
    name: str
    parent_branch_id: str | None
    fork_message_id: str | None
    fork_save_id: str | None
    branch_revision: int
    archived: bool
    message_count: int
    first_message_id: str | None
    last_message_id: str | None
    events: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str | float
    updated_at: str | float


class WorldlineView(BaseModel):
    story_id: str
    total_branches: int
    matched_branches: int | None = None
    next_cursor: str | None = None
    branches: list[WorldlineBranchView]
    membership_revision: str | None = None


class StoryTrashView(BaseModel):
    model_config = ConfigDict(extra="allow")
    story_id: str
    title: str
    created_at: str
    branch_count: int
    message_count: int
    save_count: int
    memory_count: int
    sha256: str
    external_avatar_characters: list[str] = Field(default_factory=list)
    generation_id: str | None = None


class StoryDeleteResult(BaseModel):
    cleanup_pending: bool | None = None
    ok: bool
    story_id: str
    branch_ids: list[str]
    saves_deleted: int
    recoverable: bool
    backup_sha256: str | None = None
    generation_id: str | None = None
    branch_revision: int


class RestoredIdentity(BaseModel):
    old_id: str
    new_id: str


class StoryRestoreResult(BaseModel):
    cleanup_pending: bool | None = None
    model_config = ConfigDict(extra="allow")
    story_id: str
    source_story_id: str
    branches: list[RestoredIdentity]
    saves: list[RestoredIdentity]
    branch_revision: int


class StoryPurgeResult(BaseModel):
    cleanup_pending: bool | None = None
    ok: bool
    story_id: str
    generation_id: str | None = None
    branch_revision: int


class StoryPresetResult(BaseModel):
    cleanup_pending: bool | None = None
    story_id: str
    preset_id: str | None
    branches_updated: int
    branch_revision: int
    membership_revision: str


COMMAND_MODELS = (
    MemoryRecordsResult, MemoryDeleteResult, MemoryScheduledResult,
    MemoryJobView, LegacyRestoreResult, StoryListSummary, WorldlineView, StoryTrashView,
    StoryDeleteResult, StoryRestoreResult, StoryPurgeResult, StoryPresetResult,
)
