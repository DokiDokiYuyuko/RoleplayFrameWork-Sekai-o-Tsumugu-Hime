from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class SourceCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    label: str
    summary: str = ""
    homepage: str
    filters: list[str] = Field(default_factory=list)
    paging: Literal["offset", "page"]
    full_card: bool


class CardSource(Protocol):
    source_id: str

    def capabilities(self) -> SourceCapabilities: ...

    async def search(self, query: str, cursor: str | None, limit: int) -> "SearchPage": ...

    async def full_card(self, card_id: str) -> tuple["CardHit", object]: ...


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(default_factory=lambda: ["botbooru", "chub"], max_length=8)
    limit: int = Field(default=12, ge=1, le=24)


class NextPageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    source_id: str = Field(min_length=1, max_length=40)
    cursor: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=12, ge=1, le=24)


class CardHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    card_id: str
    title: str
    creator: str = ""
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    source_url: str
    published_at: str | None = None
    content_rating: Literal["sfw", "sensitive", "unknown"]
    raw_metrics: dict[str, int | float | str | None] = Field(default_factory=dict)


class SearchPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    status: Literal["ok", "error"]
    results: list[CardHit] = Field(default_factory=list)
    next_cursor: str | None = None
    error: str | None = None
    skipped_count: int = 0


class CardDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hit: CardHit
    card: dict[str, Any]
    fetched_at: str
    content_sha256: str


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    sources: list[SearchPage]
