from __future__ import annotations

import asyncio
import time
from collections import deque
from urllib.parse import urlencode

from mrp.importers.character_card import import_card_png

from ..schemas import CardHit, SearchPage, SourceCapabilities
from ..transport import SourceRequestError, fetch_bytes, fetch_json

API = "https://api.aicharactercards.com"
SITE = "https://aicharactercards.com"
MAX_CARD_BYTES = 10 * 1024 * 1024


def _tags(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        value = item.get("name") if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip():
            result.append(value.strip())
    return result


def _hit(node: object) -> CardHit | None:
    if not isinstance(node, dict):
        return None
    card_id = node.get("id")
    if isinstance(card_id, str) and card_id.isdigit():
        card_id = int(card_id)
    if type(card_id) is not int or not 1 <= card_id <= 999_999_999_999:
        return None
    title = node.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    nsfw = node.get("isNsfw")
    rating = "sensitive" if nsfw is True else "sfw" if nsfw is False else "unknown"
    metrics = {}
    for key in ("downloadCount", "ratingAvg", "ratingCount", "tokenCount"):
        value = node.get(key)
        metrics[key] = value if isinstance(value, (int, float)) else None
    summary = node.get("excerpt") or node.get("description") or ""
    return CardHit(
        source_id="aicharactercards", card_id=str(card_id), title=title.strip(),
        creator=node.get("author") if isinstance(node.get("author"), str) else "",
        summary=summary[:1500] if isinstance(summary, str) else "", tags=_tags(node.get("tags")),
        source_url=f"{SITE}/cards/{card_id}",
        published_at=node.get("createdAt") if isinstance(node.get("createdAt"), str) else None,
        content_rating=rating, raw_metrics=metrics,
    )


class AICharacterCardsSource:
    source_id = "aicharactercards"
    label = "AICharacterCards"
    detail_cache_ttl_seconds = 1800.0

    def __init__(self) -> None:
        self._download_lock = asyncio.Lock()
        self._download_starts: deque[float] = deque()

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source_id=self.source_id, label=self.label,
            summary="可提供卡片语言 · 标签/评分较全 · 含18+内容需站点验证",
            homepage=SITE, filters=["keyword_search", "offset_paging"],
            paging="offset", full_card=True,
        )

    async def search(self, query: str, cursor: str | None, limit: int) -> SearchPage:
        offset = max(0, min(int(cursor or 0), 1_000_000))
        params = {"search": query[:256], "skip": str(offset), "limit": str(limit)}
        payload = await fetch_json(f"{API}/api/cards?{urlencode(params)}", allowed_host="api.aicharactercards.com", max_bytes=4 * 1024 * 1024)
        nodes = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(nodes, list):
            raise SourceRequestError("AICharacterCards 搜索响应格式变化")
        hits = [hit for node in nodes[:limit] if (hit := _hit(node)) is not None]
        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        total = pagination.get("total") if isinstance(pagination, dict) else None
        end = offset + len(nodes[:limit])
        has_more = end < total if isinstance(total, int) else len(nodes) >= limit
        next_cursor = str(end) if nodes and has_more else None
        return SearchPage(
            source_id=self.source_id, status="ok", results=hits, next_cursor=next_cursor,
            skipped_count=len(nodes[:limit]) - len(hits),
        )

    async def detail(self, card_id: str) -> tuple[CardHit, dict]:
        if not card_id.isdigit() or len(card_id) > 12:
            raise SourceRequestError("AICharacterCards 卡片 ID 无效")
        payload = await fetch_json(
            f"{API}/api/cards/{card_id}", allowed_host="api.aicharactercards.com", max_bytes=6 * 1024 * 1024,
        )
        hit = _hit(payload)
        if hit is None or hit.card_id != card_id:
            raise SourceRequestError("AICharacterCards 卡片不可用")
        versions = payload.get("versions")
        if not isinstance(versions, list) or not versions:
            raise SourceRequestError("AICharacterCards 没有可下载的卡片版本")
        version = next((row for row in versions if isinstance(row, dict) and row.get("isCurrent") is True), None)
        if version is None:
            version = next((row for row in versions if isinstance(row, dict)), None)
        if version is None or not isinstance(version.get("fileUrl"), str):
            raise SourceRequestError("AICharacterCards 当前版本缺少下载地址")
        return hit, version

    async def full_card(self, card_id: str) -> tuple[CardHit, object]:
        hit, version = await self.detail(card_id)
        async with self._download_lock:
            now = time.monotonic()
            while self._download_starts and now - self._download_starts[0] >= 60.0:
                self._download_starts.popleft()
            if len(self._download_starts) >= 9:
                raise SourceRequestError("AICharacterCards 本地保护限制为每分钟最多 9 次完整卡读取，请稍后再试")
            self._download_starts.append(now)
        file_url = version["fileUrl"]
        if file_url.startswith("/") and not file_url.startswith("//"):
            file_url = f"{API}{file_url}"
        raw, content_type = await fetch_bytes(
            file_url, allowed_host="api.aicharactercards.com", max_bytes=MAX_CARD_BYTES, accept="image/png",
        )
        if not content_type.lower().startswith("image/png"):
            raise SourceRequestError("AICharacterCards 没有返回 PNG 卡片")
        try:
            card = import_card_png(raw)
        except Exception as exc:
            raise SourceRequestError("AICharacterCards PNG 中没有可识别的角色卡") from exc
        if not card.name.strip():
            raise SourceRequestError("AICharacterCards 卡片缺少角色名称")
        card.creator = hit.creator or card.creator
        card.tags = hit.tags or card.tags
        return hit, card.model_dump(mode="json")
