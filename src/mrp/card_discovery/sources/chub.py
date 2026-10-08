from __future__ import annotations

from urllib.parse import urlencode

from mrp.importers.character_card import import_card_json

from ..schemas import CardHit, SearchPage, SourceCapabilities
from ..transport import SourceRequestError, fetch_json

API = "https://gateway.chub.ai"
SITE = "https://chub.ai"
SENSITIVE_TAGS = {"nsfw", "nsfl", "explicit", "gore"}


def _topics(node: object) -> list[str]:
    value = node.get("topics") if isinstance(node, dict) else None
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _content_rating(node: dict) -> str:
    topics = {topic.casefold() for topic in _topics(node)}
    if node.get("nsfw_image") is True or topics.intersection(SENSITIVE_TAGS):
        return "sensitive"
    if node.get("nsfw_image") is False:
        return "sfw"
    return "unknown"


def _hit(node: object) -> CardHit | None:
    if not isinstance(node, dict):
        return None
    full_path = node.get("fullPath")
    if not isinstance(full_path, str) or full_path.count("/") != 1 or not all(full_path.split("/")):
        return None
    name = node.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    counts = {}
    for key in ("nChats", "starCount", "n_favorites", "nTokens"):
        value = node.get(key)
        counts[key] = value if isinstance(value, (int, float)) else None
    return CardHit(
        source_id="chub", card_id=full_path, title=name.strip(), creator=full_path.split("/", 1)[0],
        summary=str(node.get("tagline") or node.get("description") or "")[:1500], tags=_topics(node),
        source_url=f"{SITE}/characters/{full_path}",
        published_at=node.get("createdAt") if isinstance(node.get("createdAt"), str) else None,
        content_rating=_content_rating(node), raw_metrics=counts,
    )


class ChubSource:
    source_id = "chub"
    label = "Chub"

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source_id=self.source_id, label=self.label,
            summary="英文为主、混有多语言卡 · 搜索/完整字段",
            homepage=SITE,
            filters=["query", "page_paging"],
            paging="page", full_card=True,
        )

    async def search(self, query: str, cursor: str | None, limit: int) -> SearchPage:
        page = max(1, min(int(cursor or 1), 10_000))
        params = {
            "search": query[:128], "namespace": "characters", "first": str(limit), "page": str(page),
            "sort": "default", "asc": "false", "nsfw": "true", "nsfl": "true",
            "nsfw_only": "false", "count": "true",
        }
        payload = await fetch_json(f"{API}/search?{urlencode(params)}", allowed_host="gateway.chub.ai", max_bytes=4 * 1024 * 1024)
        data = payload.get("data") if isinstance(payload, dict) else None
        nodes = data.get("nodes") if isinstance(data, dict) else None
        if not isinstance(nodes, list):
            raise SourceRequestError("Chub 搜索响应格式变化")
        hits = [hit for node in nodes[:limit] if (hit := _hit(node)) is not None]
        skipped = min(len(nodes[:limit]) - len(hits), limit)
        count = data.get("count") if isinstance(data, dict) else None
        next_cursor = str(page + 1) if nodes and (not isinstance(count, int) or page * limit < count) else None
        return SearchPage(source_id=self.source_id, status="ok", results=hits, next_cursor=next_cursor, skipped_count=skipped)

    async def detail(self, card_id: str) -> tuple[CardHit, object]:
        segments = card_id.split("/")
        if len(segments) != 2 or any(not part or len(part) > 160 or not all(c.isalnum() or c in "._~-" for c in part) for part in segments):
            raise SourceRequestError("Chub 卡片 ID 无效")
        payload = await fetch_json(
            f"{API}/api/characters/{card_id}?full=true", allowed_host="gateway.chub.ai", max_bytes=6 * 1024 * 1024,
        )
        node = payload.get("node") if isinstance(payload, dict) else None
        hit = _hit(node)
        if hit is None or hit.card_id != card_id:
            raise SourceRequestError("Chub 卡片不可用")
        return hit, node

    async def full_card(self, card_id: str) -> tuple[CardHit, object]:
        hit, node = await self.detail(card_id)
        definition = node.get("definition") if isinstance(node, dict) else None
        if not isinstance(definition, dict):
            raise SourceRequestError("Chub 卡片没有完整定义")
        greetings = definition.get("alternate_greetings")
        examples = definition.get("example_dialogs")
        if isinstance(examples, list):
            examples = "\n".join(str(item) for item in examples if isinstance(item, str))
        if not isinstance(examples, str):
            examples = ""
        source = {
            "spec": "chara_card_v2", "spec_version": "2.0",
            "data": {
                "name": str(definition.get("name") or node.get("name") or hit.title),
                "description": str(definition.get("description") or node.get("description") or ""),
                "personality": str(definition.get("personality") or ""),
                "scenario": str(definition.get("scenario") or ""),
                "first_mes": str(definition.get("first_message") or ""),
                "mes_example": examples,
                "alternate_greetings": greetings if isinstance(greetings, list) else [],
                "system_prompt": definition.get("system_prompt") if isinstance(definition.get("system_prompt"), str) else None,
                "post_history_instructions": definition.get("post_history_instructions") if isinstance(definition.get("post_history_instructions"), str) else None,
                "creator_notes": str(definition.get("creator_notes") or ""),
                "creator": hit.creator,
                "tags": hit.tags,
                "extensions": {"chub_full_path": card_id},
            },
        }
        try:
            card = import_card_json(source)
        except Exception as exc:
            raise SourceRequestError("Chub 卡片字段无法转换为 Character Card") from exc
        return hit, card.model_dump(mode="json")
