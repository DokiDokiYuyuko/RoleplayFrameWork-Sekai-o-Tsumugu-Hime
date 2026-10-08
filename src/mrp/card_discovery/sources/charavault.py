from __future__ import annotations

from urllib.parse import quote, urlencode

from mrp.importers.character_card import import_card_png

from ..schemas import CardHit, SearchPage, SourceCapabilities
from ..transport import SourceRequestError, fetch_bytes, fetch_json

BASE = "https://charavault.net"
MAX_CARD_BYTES = 10 * 1024 * 1024


def _path_parts(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str) or len(value) > 220 or value.count("/") != 1:
        return None
    folder, filename = value.split("/", 1)
    if any(not part or part in {".", ".."} or "\\" in part or any(ord(ch) < 32 for ch in part) for part in (folder, filename)):
        return None
    return folder, filename


def _encoded_path(parts: tuple[str, str]) -> str:
    return "/".join(quote(part, safe="") for part in parts)


def _tags(raw: object) -> list[str]:
    return [item.strip() for item in raw if isinstance(item, str) and item.strip()] if isinstance(raw, list) else []


def _hit(entry: object) -> CardHit | None:
    if not isinstance(entry, dict):
        return None
    folder = entry.get("folder")
    filename = entry.get("file")
    path = _path_parts(f"{folder}/{filename}") if isinstance(folder, str) and isinstance(filename, str) else None
    name = entry.get("name")
    if path is None or not isinstance(name, str) or not name.strip():
        return None
    tags = _tags(entry.get("tags"))
    nsfw = entry.get("nsfw")
    rating = "sensitive" if nsfw is True else "sfw" if nsfw is False else "unknown"
    metrics = {}
    for key in ("download_count", "avg_rating", "rating_count", "token_count"):
        value = entry.get(key)
        metrics[key] = value if isinstance(value, (int, float)) else None
    return CardHit(
        source_id="charavault", card_id=f"{path[0]}/{path[1]}", title=name.strip(),
        creator=entry.get("creator") if isinstance(entry.get("creator"), str) else "",
        summary=entry.get("description_preview", "")[:1500] if isinstance(entry.get("description_preview"), str) else "",
        tags=tags, source_url=f"{BASE}/cards/{_encoded_path(path)}", published_at=None,
        content_rating=rating, raw_metrics=metrics,
    )


class CharaVaultSource:
    source_id = "charavault"
    label = "CharaVault"

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source_id=self.source_id, label=self.label,
            summary="多站归档、语言混合 · 作者/标签元数据 · 仅限18+",
            homepage=BASE, filters=["keyword_search", "offset_paging"],
            paging="offset", full_card=True,
        )

    async def search(self, query: str, cursor: str | None, limit: int) -> SearchPage:
        offset = max(0, min(int(cursor or 0), 1_000_000))
        params = {"q": query[:256], "limit": str(limit), "offset": str(offset)}
        payload = await fetch_json(f"{BASE}/api/cards?{urlencode(params)}", allowed_host="charavault.net", max_bytes=4 * 1024 * 1024)
        nodes = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(nodes, list):
            raise SourceRequestError("CharaVault 搜索响应格式变化")
        hits = [hit for node in nodes[:limit] if (hit := _hit(node)) is not None]
        total = payload.get("total") if isinstance(payload, dict) else None
        end = offset + len(nodes[:limit])
        has_more = end < total if isinstance(total, int) else len(nodes) >= limit
        next_cursor = str(end) if nodes and has_more else None
        return SearchPage(
            source_id=self.source_id, status="ok", results=hits, next_cursor=next_cursor,
            skipped_count=len(nodes[:limit]) - len(hits),
        )

    async def detail(self, card_id: str) -> tuple[CardHit, tuple[str, str]]:
        parts = _path_parts(card_id)
        if parts is None:
            raise SourceRequestError("CharaVault 卡片 ID 无效")
        encoded_path = _encoded_path(parts)
        payload = await fetch_json(
            f"{BASE}/api/cards/{encoded_path}", allowed_host="charavault.net", max_bytes=4 * 1024 * 1024,
        )
        entry = payload.get("entry") if isinstance(payload, dict) else None
        hit = _hit(entry)
        if hit is None or hit.card_id != card_id:
            raise SourceRequestError("CharaVault 卡片不可用")
        return hit, parts

    async def full_card(self, card_id: str) -> tuple[CardHit, object]:
        hit, parts = await self.detail(card_id)
        encoded_path = _encoded_path(parts)
        raw, content_type = await fetch_bytes(
            f"{BASE}/cards/{encoded_path}", allowed_host="charavault.net",
            max_bytes=MAX_CARD_BYTES, accept="image/png",
        )
        if not content_type.lower().startswith("image/png"):
            raise SourceRequestError("CharaVault 没有返回 PNG 卡片")
        try:
            card = import_card_png(raw)
        except Exception as exc:
            raise SourceRequestError("CharaVault PNG 中没有可识别的角色卡") from exc
        if not card.name.strip():
            raise SourceRequestError("CharaVault 卡片缺少角色名称")
        card.creator = hit.creator or card.creator
        card.tags = hit.tags or card.tags
        return hit, card.model_dump(mode="json")
