from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    world_id: str = Field(min_length=1, max_length=200)
    category: Literal["archives", "lorebook"]
    source_text: str = Field(min_length=1, max_length=5_000_000)
    instruction: str = Field("", max_length=12000)
    reference_source_ids: list[str] = Field(default_factory=list, max_length=80)
    source_visibility: Literal["public", "private"] = "public"
    target_archive_id: str | None = None
    target_lorebook_id: str | None = None
    new_lorebook_name: str = Field("", max_length=200)


class DraftPatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    payload: dict[str, Any]
    action: Literal["add", "replace"] | None = None
    target_uid: int | None = None
    source_refs: list[dict[str, Any]] | None = None
    positive_examples: list[str] | None = None
    negative_examples: list[str] | None = None


class BatchCommitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_revision: int = Field(ge=1)
    draft_ids: list[str] = Field(min_length=1, max_length=2000)
    expected_lorebook_revision: int | None = Field(None, ge=1)
    approved_replace_draft_ids: list[str] = Field(default_factory=list)
    accept_source_changes: bool = False


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1)
    revision: int | None = None
    content_sha256: str | None = None
    start: int | None = None
    end: int | None = None


class ArchiveCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["background", "biology"]
    payload: dict[str, Any]
    source_refs: list[SourceReference] = Field(min_length=1, max_length=80)


class ArchiveResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archives: list[ArchiveCandidate] = Field(min_length=1, max_length=2000)
    coverage_notes: list[str] = Field(default_factory=list)


class ArchiveReferenceRepair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    source_refs: list[SourceReference] = Field(min_length=1, max_length=80)


class ArchiveReferenceRepairs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repairs: list[ArchiveReferenceRepair] = Field(min_length=1, max_length=2000)
