from __future__ import annotations

from urllib.parse import quote, urlencode

from mrp.importers.character_card import import_card_png

from ..schemas import CardHit, SearchPage, SourceCapabilities
from ..transport import SourceRequestError, fetch_bytes, fetch_json

SITE = "https://character-tavern.com"
STORAGE = "https://ct-cards.storage.character-tavern.com"
MAX_CARD_BYTES = 10 * 1024 * 1024


def _card_path(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 220 or value.count("/") != 1:
        return None
    parts = value.split("/")
    if any(not part or part in {".", ".."} or "\\" in part or any(ord(ch) < 32 for ch in part) for part in parts):
        return None
    return value


def _tags(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        value = item.get("name") if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip():
            result.append(value.strip())
    return result


def _rating(warnings: object, tags: list[str]) -> str:
    if isinstance(warnings, list) and warnings:
        return "sensitive"
    if any(tag.casefold() in {"nsfw", "nsfl", "explicit", "gore"} for tag in tags):
        return "sensitive"
    return "unknown"


def _hit(node: object) -> CardHit | None:
    if not isinstance(node, dict):
        return None
    path = _card_path(node.get("path"))
    title = node.get("name")
    if path is None or not isinstance(title, str) or not title.strip():
        return None
    tags = _tags(node.get("tags"))
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    return CardHit(
        source_id="character_tavern", card_id=path, title=title.strip(),
        creator=node.get("author") if isinstance(node.get("author"), str) else "",
        summary=node.get("tagline", "")[:1500] if isinstance(node.get("tagline"), str) else "",
        tags=tags, source_url=f"{SITE}/character/{encoded_path}", published_at=None,
        content_rating=_rating(node.get("contentWarnings"), tags),
        raw_metrics={"permanentTokens": node.get("permanentTokens") if isinstance(node.get("permanentTokens"), int) else None},
    )


class CharacterTavernSource:
    source_id = "character_tavern"
    label = "Character Tavern"

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source_id=self.source_id, label=self.label,
            summary="英文内容较多 · 关键词相关排序 · PNG 卡片",
            homepage=SITE, filters=["keyword_search", "page_paging"],
            paging="page", full_card=True,
        )

    async def search(self, query: str, cursor: str | None, limit: int) -> SearchPage:
        page = max(1, min(int(cursor or 1), 10_000))
        params = {"query": query[:256], "sort": "best", "page": str(page), "limit": str(limit)}
        payload = await fetch_json(
            f"{SITE}/api/search/cards?{urlencode(params)}", allowed_host="character-tavern.com", max_bytes=4 * 1024 * 1024,
        )
        nodes = payload.get("hits") if isinstance(payload, dict) else None
        if not isinstance(nodes, list):
            raise SourceRequestError("Character Tavern 搜索响应格式变化")
        hits = [hit for node in nodes[:limit] if (hit := _hit(node)) is not None]
        total = payload.get("totalHits") if isinstance(payload, dict) else None
        has_more = page * limit < total if isinstance(total, int) else len(nodes) >= limit
        next_cursor = str(page + 1) if nodes and has_more else None
        return SearchPage(
            source_id=self.source_id, status="ok", results=hits, next_cursor=next_cursor,
            skipped_count=len(nodes[:limit]) - len(hits),
        )

    async def full_card(self, card_id: str) -> tuple[CardHit, object]:
        path = _card_path(card_id)
        if path is None:
            raise SourceRequestError("Character Tavern 卡片 ID 无效")
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        raw, content_type = await fetch_bytes(
            f"{STORAGE}/{encoded_path}.png?action=download", allowed_host="ct-cards.storage.character-tavern.com",
            max_bytes=MAX_CARD_BYTES, accept="image/png",
        )
        if not content_type.lower().startswith("image/png"):
            raise SourceRequestError("Character Tavern 没有返回 PNG 卡片")
        try:
            card = import_card_png(raw)
        except Exception as exc:
            raise SourceRequestError("Character Tavern PNG 中没有可识别的角色卡") from exc
        if not card.name.strip():
            raise SourceRequestError("Character Tavern 卡片缺少角色名称")
        hit = CardHit(
            source_id=self.source_id, card_id=path, title=card.name.strip(), creator=card.creator,
            summary=card.description[:1500], tags=card.tags, source_url=f"{SITE}/character/{encoded_path}",
            content_rating=_rating(None, card.tags), raw_metrics={},
        )
        return hit, card.model_dump(mode="json")
