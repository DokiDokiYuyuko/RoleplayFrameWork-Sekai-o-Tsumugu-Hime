"""Declarative prompt scheme library and inspectable foreign-file drafts."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.storage.prompt_presets import PromptPreset, preview_import
from mrp.orchestrator.story_archive import _scrub

router = APIRouter()


@router.get("/api/v1/prompt-presets")
async def list_presets(container: AppContainer = Depends(get_container)):
    return await asyncio.to_thread(container.prompt_presets.list)


@router.post("/api/v1/prompt-presets")
async def save_preset(preset: PromptPreset, container: AppContainer = Depends(get_container)):
    clean = PromptPreset.model_validate(_scrub(preset.model_dump(mode="json"), [0]))
    return await asyncio.to_thread(container.prompt_presets.save, clean)


@router.get("/api/v1/prompt-presets/{preset_id}")
async def get_preset(preset_id: str, container: AppContainer = Depends(get_container)):
    preset = await asyncio.to_thread(container.prompt_presets.get, preset_id)
    if preset is None:
        raise HTTPException(404, "提示词方案不存在")
    return preset


@router.delete("/api/v1/prompt-presets/{preset_id}")
async def delete_preset(preset_id: str, container: AppContainer = Depends(get_container)):
    if container.settings.active_prompt_preset_id == preset_id:
        raise HTTPException(409, "请先取消全局启用，再删除方案")
    await asyncio.to_thread(container.prompt_presets.delete, preset_id)
    return {"ok": True}


@router.post("/api/v1/prompt-presets/import-preview")
async def import_preview(file: UploadFile = File(...)):
    raw = await file.read(2_000_001)
    try:
        return preview_import(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)[:200]) from exc


@router.get("/api/v1/prompt-presets/{preset_id}/export")
async def export_preset(preset_id: str, container: AppContainer = Depends(get_container)):
    preset = await asyncio.to_thread(container.prompt_presets.get, preset_id)
    if preset is None:
        raise HTTPException(404, "提示词方案不存在")
    payload = _scrub(preset.model_dump(mode="json"), [0])
    return Response(json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="prompt-{preset.id}.json"'})
