from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from mrp.card_discovery.schemas import NextPageInput, SearchInput
from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.card_discovery.transport import SourceRequestError

router = APIRouter()


@router.get("/api/v1/card-sources")
async def list_card_sources(container: AppContainer = Depends(get_container)):
    return container.card_discovery.list_sources()


@router.post("/api/v1/card-searches")
async def search_cards(req: SearchInput, container: AppContainer = Depends(get_container)):
    try:
        return (await container.card_discovery.search(req)).model_dump(mode="json")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/v1/card-searches/next")
async def next_card_page(req: NextPageInput, container: AppContainer = Depends(get_container)):
    try:
        result = await container.card_discovery.next_page(req.source_id, req.query, req.cursor, req.limit)
        return result.model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(404, "角色卡站点不存在") from exc


@router.get("/api/v1/card-sources/{source_id}/cards/{card_id:path}")
async def get_source_card(source_id: str, card_id: str, container: AppContainer = Depends(get_container)):
    try:
        return (await container.card_discovery.full_card(source_id, card_id)).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(404, "角色卡站点或卡片不存在") from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "站点完整卡读取超时") from exc
    except (SourceRequestError, ValueError) as exc:
        raise HTTPException(502, str(exc)) from exc
