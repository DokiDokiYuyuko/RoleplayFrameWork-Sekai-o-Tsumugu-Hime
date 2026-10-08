from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from mrp.shared.models import CharacterCard

from .schemas import CardDetail, CardHit, SearchInput, SearchPage, SearchResponse
from .registry import CardSourceRegistry
from .sources import (
    AICharacterCardsSource,
    BotbooruSource,
    CharaVaultSource,
    CharacterTavernSource,
    ChubSource,
)
from .transport import SourceRequestError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CardDiscoveryService:
    def __init__(self, sources: tuple[Any, ...] | None = None) -> None:
        self.registry = CardSourceRegistry(sources or (
            BotbooruSource(), ChubSource(), AICharacterCardsSource(),
            CharacterTavernSource(), CharaVaultSource(),
        ))
        self.sources = self.registry.sources
        self._semaphores = {source_id: asyncio.Semaphore(2) for source_id in self.sources}
        self._detail_cache: dict[tuple[str, str], tuple[float, CardDetail]] = {}

    def list_sources(self) -> list[dict[str, Any]]:
        return [source.capabilities().model_dump(mode="json") for source in self.sources.values()]

    async def search(self, request: SearchInput) -> SearchResponse:
        source_ids = request.source_ids or list(self.sources)
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("站点列表存在重复项")
        unknown = set(source_ids) - self.sources.keys()
        if unknown:
            raise ValueError("选择了未启用的角色卡站点")
        pages = await asyncio.gather(
            *(self._search_one(source_id, request.query, None, request.limit) for source_id in source_ids)
        )
        return SearchResponse(query=request.query, sources=pages)

    async def next_page(self, source_id: str, query: str, cursor: str, limit: int) -> SearchPage:
        if source_id not in self.sources:
            raise KeyError(source_id)
        return await self._search_one(source_id, query, cursor, limit)

    async def _search_one(self, source_id: str, query: str, cursor: str | None, limit: int) -> SearchPage:
        source = self.sources[source_id]
        try:
            async with self._semaphores[source_id]:
                page = await asyncio.wait_for(source.search(query, cursor, limit), timeout=18.0)
            unique: dict[tuple[str, str], CardHit] = {}
            for hit in page.results:
                unique[(hit.source_id, hit.card_id)] = hit
            page.results = sorted(unique.values(), key=lambda hit: self._score(query, hit), reverse=True)
            return page
        except asyncio.TimeoutError:
            return SearchPage(source_id=source_id, status="error", error="站点搜索超时（18 秒）")
        except Exception as exc:  # one source must not fail the rest of the search
            message = str(exc)[:200] if isinstance(exc, (SourceRequestError, ValueError, ValidationError)) else "站点暂时不可用"
            if isinstance(exc, SourceRequestError) and exc.retry_after:
                message += f"（Retry-After: {exc.retry_after[:32]}）"
            return SearchPage(source_id=source_id, status="error", error=message)

    @staticmethod
    def _score(query: str, hit: CardHit) -> int:
        terms = [term.casefold() for term in re.findall(r"[\w\u3400-\u9fff]+", query) if len(term) > 1]
        if not terms:
            return 0
        title = hit.title.casefold()
        tags = " ".join(hit.tags).casefold()
        summary = hit.summary.casefold()
        return sum(4 if term in title else 3 if term in tags else 1 if term in summary else 0 for term in terms)

    async def full_card(self, source_id: str, card_id: str) -> CardDetail:
        source = self.sources.get(source_id)
        if source is None:
            raise KeyError(source_id)
        key = (source_id, card_id)
        cached = self._detail_cache.get(key)
        now = asyncio.get_running_loop().time()
        if cached and cached[0] > now:
            return cached[1]
        async with self._semaphores[source_id]:
            hit, raw_card = await asyncio.wait_for(source.full_card(card_id), timeout=24.0)
        card = CharacterCard.model_validate(raw_card)
        if card.source_format not in {"ccv1", "ccv2", "ccv3"}:
            raise ValueError("卡片格式不受支持")
        normalized = card.model_dump(mode="json")
        result = CardDetail(hit=hit, card=normalized, fetched_at=_now(), content_sha256=_fingerprint(normalized))
        cache_ttl = float(getattr(source, "detail_cache_ttl_seconds", 300.0))
        self._detail_cache[key] = (now + cache_ttl, result)
        return result
