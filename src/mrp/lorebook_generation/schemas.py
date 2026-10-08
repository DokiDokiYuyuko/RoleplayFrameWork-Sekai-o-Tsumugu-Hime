from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    world_id: str = Field(min_length=1, max_length=200)
    source_ids: list[str] | None = Field(default=None, max_length=80)
    include_core_brief: bool = False
    mode: Literal["initial", "incremental"] = "initial"
    target_lorebook_id: str | None = Field(default=None, max_length=200)
    new_lorebook_name: str = Field("", max_length=200)
    goal: str = Field("", max_length=4000)


class DraftPatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    payload: dict[str, Any]
    source_refs: list[dict[str, Any]] | None = None
    positive_examples: list[str] | None = None
    negative_examples: list[str] | None = None
    rationale: str | None = Field(None, max_length=4000)
    risk_notes: list[str] | None = Field(None, max_length=20)
    action: Literal["add", "replace", "disable", "keep"] | None = None
    resolve_conflict: bool = False
    target_uid: int | None = None


class CommitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    accept_source_changes: bool = False


class BatchCommitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_revision: int = Field(ge=1)
    expected_lorebook_revision: int | None = Field(None, ge=1)
    draft_ids: list[str] = Field(min_length=1)
    shared_draft_ids: list[str] = Field(default_factory=list)


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=12, max_length=500)
    revision: int | None = None
    content_sha256: str | None = None
    start: int | None = None
    end: int | None = None


class CandidateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any]
    source_refs: list[SourceReference] = Field(min_length=1, max_length=20)
    positive_examples: list[str] = Field(min_length=1, max_length=20)
    negative_examples: list[str] = Field(min_length=1, max_length=20)
    rationale: str = Field("", max_length=4000)
    risk_notes: list[str] = Field(default_factory=list, max_length=20)


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[CandidateEnvelope] = Field(default_factory=list, max_length=2000)
    coverage_notes: list[str] = Field(default_factory=list)
