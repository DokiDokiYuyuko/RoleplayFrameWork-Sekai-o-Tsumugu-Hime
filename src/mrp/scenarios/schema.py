"""Stable interchange contract for reusable roleplay scenarios.

This deliberately does not serialize Character, Lorebook, or SessionState wholesale.
Only story-facing fields are included; runtime IDs and model credentials stay local.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mrp.shared.models import new_id, utcnow


def legacy_package_id(source_id: str) -> str:
    """Derive a repeatable package identity for old records that lack one."""
    return "package-" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:12]


class ScenarioCharacterCard(BaseModel):
    """Versioned, story-facing card shape; independent of runtime Character fields."""

    model_config = ConfigDict(extra="allow")

    spec: str = "chara_card_v2"
    spec_version: str = "2.0"
    name: str
    description: str = ""
    appearance: str = ""
    traits_label: str = "能力与实力"
    traits: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    alternate_greetings: list[str] = Field(default_factory=list)
    system_prompt: str | None = None
    post_history_instructions: str | None = None
    creator_notes: str = ""
    creator: str = ""
    character_version: str = ""
    tags: list[str] = Field(default_factory=list)
    extensions: dict[str, Any] = Field(default_factory=dict)
    source_format: str = "ccv2"


class ScenarioLorebookEntry(BaseModel):
    """Portable worldbook entry fields, kept separate from the runtime entity model."""

    model_config = ConfigDict(extra="allow")

    uid: int
    keys: list[str] = Field(default_factory=list)
    secondary_keys: list[str] = Field(default_factory=list)
    content: str = ""
    comment: str = ""
    enabled: bool = True
    constant: bool = False
    selective: bool = False
    selective_logic: Literal[0, 1, 2, 3] = 0
    order: int = 100
    anchor: Literal["system", "at_depth", "near"] = "system"
    depth: int = 4
    probability: int = Field(100, ge=0, le=100)
    extensions: dict[str, Any] = Field(default_factory=dict)


class ScenarioCharacter(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    card: ScenarioCharacterCard
    source_asset_id: str | None = None
    source_asset_revision: int | None = None
    aliases: list[str] = Field(default_factory=list, max_length=100)
    talkativeness: float = Field(0.5, ge=0, le=1)
    interject_enabled: bool = False
    followup_enabled: bool = False
    lorebook_keys: list[str] = Field(default_factory=list, max_length=20)


class ScenarioLorebook(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    source_asset_id: str | None = None
    source_asset_revision: int | None = None
    name: str = Field("未命名世界书", max_length=200)
    description: str = Field("", max_length=5000)
    entries: list[ScenarioLorebookEntry] = Field(default_factory=list, max_length=2000)
    scan_depth: int = Field(2, ge=0, le=100)
    token_budget: int = Field(1024, ge=0, le=100000)
    recursive_scanning: bool = True
    source_format: str = "st"


class ScenarioOpening(BaseModel):
    model_config = ConfigDict(extra="allow")

    location: str = Field("开场", max_length=200)
    description: str = Field("", max_length=5000)
    narration: str = Field("", max_length=10000)
    member_keys: list[str] = Field(default_factory=list, max_length=20)


class ScenarioPlayOptions(BaseModel):
    """Optional story-level overrides. None means use the user's normal default."""

    model_config = ConfigDict(extra="allow")

    narrative_pov: Literal["free", "second", "third"] | None = None
    narrative_density: Literal["dialogue", "balanced", "atmosphere"] | None = None
    director_mode: Literal["auto", "confirm", "rules"] | None = None
    options_enabled: bool | None = None
    options_style: Literal["action", "dialogue", "mixed"] | None = None
    proactive_turn_limit: int | None = Field(None, ge=0, le=3)


class ScenarioComponent(BaseModel):
    """Namespaced extension owned by a feature module, not by the scenario core."""

    model_config = ConfigDict(extra="allow")

    version: int = Field(ge=1)
    required: bool = False
    data: dict[str, Any] = Field(default_factory=dict)


class ScenarioPackage(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: new_id("scenario"))
    package_id: str = Field(default_factory=lambda: new_id("package"))
    format: Literal["mrp.scenario"] = "mrp.scenario"
    format_version: Literal[1] = 1
    schema_version: int = Field(default=1, ge=1)
    revision: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=5000)
    author: str = Field("", max_length=200)
    license: str = Field("", max_length=200)
    source_url: str = Field("", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    player_persona: str = Field("", max_length=20000)
    instructions: str = Field("", max_length=20000)
    cast: list[ScenarioCharacter] = Field(min_length=1, max_length=20)
    lorebooks: list[ScenarioLorebook] = Field(default_factory=list, max_length=20)
    opening: ScenarioOpening = Field(default_factory=ScenarioOpening)
    play: ScenarioPlayOptions = Field(default_factory=ScenarioPlayOptions)
    components: dict[str, ScenarioComponent] = Field(default_factory=dict, max_length=100)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    imported_at: datetime | None = None

    @model_validator(mode="after")
    def validate_references(self) -> "ScenarioPackage":
        actor_keys = [member.key for member in self.cast]
        book_keys = [book.key for book in self.lorebooks]
        if len(actor_keys) != len(set(actor_keys)):
            raise ValueError("场景包中的角色代号重复")
        if len(book_keys) != len(set(book_keys)):
            raise ValueError("场景包中的世界书代号重复")
        if not self.opening.member_keys:
            self.opening.member_keys = actor_keys.copy()
        if set(self.opening.member_keys) - set(actor_keys):
            raise ValueError("开场场景引用了不存在的角色")
        if any(set(member.lorebook_keys) - set(book_keys) for member in self.cast):
            raise ValueError("角色绑定了场景包中不存在的世界书")
        for component_name in self.components:
            if not component_name or "." not in component_name or len(component_name) > 120:
                raise ValueError("场景扩展模块必须使用带命名空间的模块名")
        return self


class ScenarioSummary(BaseModel):
    id: str
    package_id: str
    revision: int = 1
    title: str
    description: str = ""
    author: str = ""
    imported_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    character_names: list[str] = Field(default_factory=list)
    lorebook_count: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class ScenarioCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=5000)
    author: str = Field("", max_length=200)
    license: str = Field("", max_length=200)
    source_url: str = Field("", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    character_ids: list[str] = Field(min_length=1, max_length=20)
    lorebook_ids: list[str] = Field(default_factory=list, max_length=20)
    world_id: str | None = None
    player_persona: str = Field("", max_length=20000)
    instructions: str = Field("", max_length=20000)
    opening: ScenarioOpening = Field(default_factory=ScenarioOpening)
    play: ScenarioPlayOptions = Field(default_factory=ScenarioPlayOptions)


class ScenarioPatchRequest(BaseModel):
    expected_revision: int | None = Field(None, ge=1)
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=5000)
    author: str | None = Field(None, max_length=200)
    license: str | None = Field(None, max_length=200)
    source_url: str | None = Field(None, max_length=2000)
    tags: list[str] | None = Field(None, max_length=30)
    player_persona: str | None = Field(None, max_length=20000)
    instructions: str | None = Field(None, max_length=20000)
    opening: ScenarioOpening | None = None
    play: ScenarioPlayOptions | None = None
