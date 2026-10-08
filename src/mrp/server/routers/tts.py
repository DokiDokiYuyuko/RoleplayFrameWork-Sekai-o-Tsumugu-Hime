"""Local TTS controls and voice profile management."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.settings import save_settings

router = APIRouter()


class SynthesizeReq(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    voice_profile_id: str | None = None
    emotion: str = Field(default="", max_length=48)


def _status(container: AppContainer) -> dict[str, Any]:
    return {"enabled": container.settings.tts.enabled, **container.tts_manager.status()}


@router.get("/api/v1/tts/status")
async def tts_status(container: AppContainer = Depends(get_container)):
    return _status(container)


@router.post("/api/v1/tts/enable")
async def enable_tts(container: AppContainer = Depends(get_container)):
    container.settings.tts.enabled = True
    save_settings(container.data_root, container.settings)
    await container.tts_manager.start_background()
    return _status(container)


@router.post("/api/v1/tts/disable")
async def disable_tts(container: AppContainer = Depends(get_container)):
    container.settings.tts.enabled = False
    save_settings(container.data_root, container.settings)
    await container.tts_manager.stop()
    return _status(container)


@router.get("/api/v1/tts/voices")
async def list_voices(container: AppContainer = Depends(get_container)):
    return {
        "voices": [row.model_dump(mode="json") for row in container.tts_voices.list()],
        "default_voice_profile_id": container.settings.tts.default_voice_profile_id,
    }


@router.post("/api/v1/tts/voices")
async def upload_voice(
    name: str = Form(..., max_length=80),
    transcript: str = Form(..., max_length=12000),
    file: UploadFile = File(...),
    container: AppContainer = Depends(get_container),
):
    raw = await file.read(30 * 1024 * 1024 + 1)
    try:
        profile = container.tts_voices.add(name, transcript, raw, file.filename or "reference.wav")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not container.settings.tts.default_voice_profile_id:
        container.settings.tts.default_voice_profile_id = profile.id
        save_settings(container.data_root, container.settings)
    return profile.model_dump(mode="json")


@router.post("/api/v1/tts/voices/{voice_id}/default")
async def set_default_voice(voice_id: str, container: AppContainer = Depends(get_container)):
    if container.tts_voices.get(voice_id) is None:
        raise HTTPException(404, "找不到这条音色档案")
    container.settings.tts.default_voice_profile_id = voice_id
    save_settings(container.data_root, container.settings)
    return {"default_voice_profile_id": voice_id}


@router.delete("/api/v1/tts/voices/{voice_id}")
async def delete_voice(voice_id: str, container: AppContainer = Depends(get_container)):
    if not container.tts_voices.delete(voice_id):
        raise HTTPException(404, "找不到这条音色档案")
    if container.settings.tts.default_voice_profile_id == voice_id:
        container.settings.tts.default_voice_profile_id = None
        save_settings(container.data_root, container.settings)
    return {"ok": True}


@router.post("/api/v1/tts/synthesize")
async def synthesize(req: SynthesizeReq, container: AppContainer = Depends(get_container)):
    if not container.settings.tts.enabled:
        raise HTTPException(409, "请先在设置中启用语音模型")
    voice_id = req.voice_profile_id or container.settings.tts.default_voice_profile_id
    if not voice_id:
        raise HTTPException(400, "请先上传并选择参考音色")
    profile = container.tts_voices.get(voice_id)
    if profile is None:
        raise HTTPException(404, "找不到所选音色，请在设置中重新选择")
    try:
        audio = await container.tts_manager.synthesize(req.text, profile, container.tts_voices, req.emotion.strip())
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"语音合成失败：{str(exc)[:300]}") from exc
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "private, max-age=86400"})
