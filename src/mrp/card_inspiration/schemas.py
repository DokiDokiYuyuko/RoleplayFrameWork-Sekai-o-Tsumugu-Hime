from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=40)
    card_id: str = Field(min_length=1, max_length=220)


class CreateJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_query: str = Field(default="", max_length=2000)
    requirement: str = Field(min_length=1, max_length=2000)
    detail: str = Field(default="", max_length=5000)
    borrow: str = Field(default="", max_length=2000)
    avoid: str = Field(default="", max_length=2000)
    references: list[ReferenceInput] = Field(min_length=1, max_length=5)


class GenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=1, ge=1, le=3)


class DraftPatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    payload: dict[str, Any]
    aliases: list[str] = Field(default_factory=list, max_length=30)
    source: Literal["user", "agent"] = "user"


class CommitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    payload: dict[str, Any]
    aliases: list[str] = Field(default_factory=list, max_length=30)


class BriefPatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    requirement: str = Field(min_length=1, max_length=2000)
    detail: str = Field(default="", max_length=5000)
    borrow: str = Field(default="", max_length=2000)
    avoid: str = Field(default="", max_length=2000)


class AgentTurnInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=3000)
