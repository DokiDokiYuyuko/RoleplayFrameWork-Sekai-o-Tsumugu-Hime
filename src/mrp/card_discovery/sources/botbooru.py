from __future__ import annotations

from urllib.parse import urlencode

from mrp.importers.character_card import import_card_json, import_card_png

from ..schemas import CardHit, SearchPage, SourceCapabilities
from ..transport import SourceRequestError, fetch_bytes, fetch_json

BASE = "https://botbooru.com"
MAX_CARD_BYTES = 10 * 1024 * 1024


def _tags(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item["name"] for item in raw if isinstance(item, dict) and isinstance(item.get("name"), str)]


def _writer(raw: object) -> str:
    if not isinstance(raw, list):
        return ""
    for item in raw:
        if isinstance(item, dict) and item.get("category") == "Writer" and isinstance(item.get("name"), str):
            return item["name"]
    return ""


def _post_hit(post: object) -> CardHit | None:
    if not isinstance(post, dict):
        return None
    card_id = post.get("id")
    if not isinstance(card_id, int) or card_id < 1 or card_id > 999_999_999_999:
        return None
    title = post.get("character_name")
    if not isinstance(title, str) or not title.strip():
        return None
    tags = _tags(post.get("tags"))
    normalized_tags = {tag.casefold() for tag in tags}
    sensitive_tags = {"nsfw", "nsfl", "explicit", "gore"}
    content_rating = (
        "sensitive" if normalized_tags.intersection(sensitive_tags)
        else "sfw" if "sfw" in normalized_tags
        else "unknown"
    )
    metrics = {}
    for key in ("views", "downloads", "favorite_count", "token_count"):
        value = post.get(key)
        metrics[key] = value if isinstance(value, (int, float)) else None
    summary = post.get("tagline") or post.get("description_excerpt") or ""
    return CardHit(
        source_id="botbooru", card_id=str(card_id), title=title.strip(),
        creator=_writer(post.get("tags")) or str(post.get("uploader_name") or ""),
        summary=summary[:1500] if isinstance(summary, str) else "",
        tags=tags, source_url=f"{BASE}/character/{card_id}",
        published_at=post.get("created_at") if isinstance(post.get("created_at"), str) else None,
        content_rating=content_rating, raw_metrics=metrics,
    )


def _query_parts(query: str) -> tuple[str, str]:
    prefixes = ("writer:", "character:", "artist:", "copyright:", "scenario:", "language:", "meta:")
    exact, text = [], []
    for token in query[:500].split():
        if token.startswith("-") or token.casefold().startswith(prefixes):
            exact.append(token)
        else:
            text.append(token)
    return " ".join(exact), " ".join(text)


class BotbooruSource:
    source_id = "botbooru"
    label = "Botbooru"

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source_id=self.source_id, label=self.label,
            summary="英文为主 · 标签/正文搜索 · PNG 卡片",
            homepage=BASE,
            filters=["query_text", "exact_tags", "offset_paging"],
            paging="offset", full_card=True,
        )

    async def search(self, query: str, cursor: str | None, limit: int) -> SearchPage:
        exact, text = _query_parts(query)
        params = {"limit": str(limit), "offset": str(max(0, min(int(cursor or 0), 100_000))), "sort": "latest"}
        if exact:
            params["q"] = exact
        if text:
            params["qtext"] = text
        url = f"{BASE}/posts/?{urlencode(params)}"
        payload = await fetch_json(url, allowed_host="botbooru.com", max_bytes=2 * 1024 * 1024)
        if not isinstance(payload, dict) or not isinstance(payload.get("posts"), list):
            raise SourceRequestError("Botbooru 搜索响应格式变化")
        posts = payload["posts"][:limit]
        hits = [hit for post in posts if (hit := _post_hit(post)) is not None]
        skipped = len(posts) - len(hits)
        total = payload.get("total")
        offset = max(0, min(int(cursor or 0), 100_000))
        next_cursor = str(offset + len(posts)) if posts and (not isinstance(total, int) or offset + len(posts) < total) else None
        return SearchPage(source_id=self.source_id, status="ok", results=hits, next_cursor=next_cursor, skipped_count=skipped)

    async def detail(self, card_id: str) -> tuple[CardHit, object]:
        if not card_id.isdigit() or len(card_id) > 12:
            raise SourceRequestError("Botbooru 卡片 ID 无效")
        payload = await fetch_json(f"{BASE}/post/{card_id}", allowed_host="botbooru.com", max_bytes=4 * 1024 * 1024)
        hit = _post_hit(payload)
        if hit is None or hit.card_id != card_id:
            raise SourceRequestError("Botbooru 卡片不可用")
        return hit, payload

    async def full_card(self, card_id: str) -> tuple[CardHit, object]:
        hit, metadata = await self.detail(card_id)
        raw, content_type = await fetch_bytes(
            f"{BASE}/download/png/{card_id}", allowed_host="botbooru.com",
            max_bytes=MAX_CARD_BYTES, accept="image/png",
        )
        if not content_type.lower().startswith("image/png"):
            raise SourceRequestError("Botbooru 没有返回 PNG 卡片")
        try:
            card = import_card_png(raw)
        except Exception as exc:
            raise SourceRequestError("Botbooru PNG 中没有可识别的角色卡") from exc
        if not card.name.strip():
            raise SourceRequestError("Botbooru 卡片缺少角色名称")
        if isinstance(metadata, dict):
            card.creator = hit.creator or card.creator
            card.tags = hit.tags or card.tags
            if not card.creator_notes and isinstance(metadata.get("creator_notes"), str):
                card.creator_notes = metadata["creator_notes"]
        return hit, card.model_dump(mode="json")
