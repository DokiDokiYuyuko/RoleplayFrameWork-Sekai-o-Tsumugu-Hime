"""A world stores canonical long-form material; lorebooks remain runtime resources."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mrp.shared.models import new_id, utcnow


class BackgroundData(BaseModel):
    model_config = ConfigDict(extra="allow")
    section: Literal["overview", "history", "geography", "society", "other"] = "other"


class BiologyData(BaseModel):
    model_config = ConfigDict(extra="allow")
    classification: Literal["race", "species", "creature", "other"] = "other"
    appearance: str = Field("", max_length=30000)
    habitat: str = Field("", max_length=30000)
    culture: str = Field("", max_length=30000)
    abilities: str = Field("", max_length=30000)
    limitations: str = Field("", max_length=30000)


ARCHIVE_KINDS: dict[str, type[BaseModel]] = {
    "background": BackgroundData,
    "biology": BiologyData,
}


class ArchiveRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(default_factory=lambda: new_id("archive"))
    kind: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    subtype: str = ""
    title: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=30)
    summary: str = Field("", max_length=5000)
    body: str = ""
    visibility: Literal["public", "private"] = "public"
    kind_data: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = Field(1, ge=1)
    revision: int = Field(1, ge=1)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    # Server-owned copy link. Absent on records written in this world.
    # All three stay together; the archive editor does not send them.
    copied_from_world_id: str | None = None
    copied_from_archive_id: str | None = None
    copied_from_revision: int | None = None

    @model_validator(mode="after")
    def validate_kind_data(self) -> "ArchiveRecord":
        model = ARCHIVE_KINDS.get(self.kind)
        if model is not None:
            self.kind_data = model.model_validate(self.kind_data).model_dump(mode="json")
        return self

    @model_validator(mode="after")
    def provenance_complete(self) -> "ArchiveRecord":
        world_id = (self.copied_from_world_id or "").strip() or None
        archive_id = (self.copied_from_archive_id or "").strip() or None
        revision = self.copied_from_revision
        self.copied_from_world_id = world_id
        self.copied_from_archive_id = archive_id
        if revision is not None and revision < 1:
            raise ValueError("导入来源修订号无效")
        present = (world_id is not None, archive_id is not None, revision is not None)
        if any(present) and not all(present):
            raise ValueError("导入来源记录不完整")
        return self


class World(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(default_factory=lambda: new_id("world"))
    schema_version: int = Field(default=1, ge=1)
    source_asset_id: str | None = None
    source_asset_revision: int | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    cover_id: str | None = Field(None, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    core_brief: str = ""
    author_core_brief: str = ""
    manuscript_archive_id: str | None = None
    runtime_policy: Literal["legacy_full", "raw", "compiled"] = "legacy_full"
    revision: int = Field(1, ge=1)
    archived: bool = False
    archive_records: list[ArchiveRecord] = Field(default_factory=list, max_length=2000)
    lorebook_ids: list[str] = Field(default_factory=list, max_length=100)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def unique_children(self) -> "World":
        archive_ids = [item.id for item in self.archive_records]
        if len(archive_ids) != len(set(archive_ids)):
            raise ValueError("世界档案条目 ID 重复")
        if len(self.lorebook_ids) != len(set(self.lorebook_ids)):
            raise ValueError("世界书关联重复")
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "cover_id": self.cover_id,
            "core_brief": self.core_brief,
            "manuscript_archive_id": self.manuscript_archive_id,
            "runtime_policy": self.runtime_policy,
            "revision": self.revision,
            "archived": self.archived,
            "background_count": sum(item.kind == "background" for item in self.archive_records),
            "biology_count": sum(item.kind == "biology" for item in self.archive_records),
            "lorebook_count": len(self.lorebook_ids),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
