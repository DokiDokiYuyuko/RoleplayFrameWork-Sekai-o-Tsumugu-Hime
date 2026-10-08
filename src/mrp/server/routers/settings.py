"""R48 全局设置路由（设置页数据面）。"""
from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any, Literal, cast
from urllib.parse import quote, urlsplit

import httpx
from mrp.response_styles import ResponseStyle
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from mrp.llm import LlmConfig, chat_text
from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.settings import (
    AppearancePreferences,
    BreakArmorMode,
    BreakArmorPromptPreset,
    EngineKind,
    GenerationSettings,
    Thinking,
    activate_gateway_profile,
    provider_profile_id,
    save_settings,
)

router = APIRouter()


class PatchAppearancePreferences(BaseModel):
    bubble_style_id: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    theme_id: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    visual_style_id: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    typography_id: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    decoration_id: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    cursor_id: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    trail_id: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    click_effect_id: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    effect_intensity: float | None = Field(None, ge=0, le=1)
    density: Literal["comfortable", "compact"] | None = None
    avatar_frame_id: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    dialogue_avatar_size: Literal[32, 40, 56] | None = None
    primary_button_skin: bool | None = None
    secondary_button_skin: bool | None = None
    card_ornaments: bool | None = None
    card_border: bool | None = None
    background_art: bool | None = None
    portrait_placeholder: Literal["art", "initial"] | None = None
    reading_width: Literal["narrow", "standard", "wide"] | None = None
    reading_font_size: int | None = Field(None, ge=14, le=24)


class PatchSettingsReq(BaseModel):
    appearance: PatchAppearancePreferences | None = None
    response_styles: list[ResponseStyle] | None = Field(None, max_length=200)
    expected_response_styles_revision: int | None = None
    engine: str | None = Field(None, max_length=32)  # dsh / openrouter
    thinking: str | None = Field(None, max_length=32)  # on（低档）/ off（彻底关）
    gateway: str | None = Field(None, min_length=1, max_length=500)
    model: str | None = Field(None, min_length=1, max_length=256)
    auxiliary_model: str | None = Field(None, max_length=256)
    model_provider: str | None = Field(None, max_length=128)
    auxiliary_provider: str | None = Field(None, max_length=128)
    provider_allow_fallbacks: bool | None = None
    generation: GenerationSettings | None = None
    context_limit_override: int | None = Field(None, ge=1024, le=10_000_000)
    active_prompt_preset_id: str | None = None
    backup_retention_days: int | None = Field(None, ge=1, le=3650)
    backup_keep_count: int | None = Field(None, ge=1, le=500)
    backup_max_bytes: int | None = Field(None, ge=100_000_000)
    hygiene_enabled: bool | None = None
    memory_consolidation_enabled: bool | None = None
    api_key: str | None = Field(None, max_length=4096)
    clear_api_key: bool = False
    # Compatibility field for older frontends; new clients send named presets.
    break_armor_prompt: str | None = Field(None, max_length=4000)
    break_armor_prompts: list[BreakArmorPromptPreset] | None = Field(None, max_length=100)
    active_break_armor_prompt_id: str | None = Field(None, max_length=80)
    break_armor_mode: BreakArmorMode | None = None
    break_armor_interval: int | None = Field(None, ge=1, le=1000)

    @model_validator(mode="after")
    def unique_break_armor_prompt_presets(self):
        presets = self.break_armor_prompts
        if presets is not None:
            ids = [preset.id for preset in presets]
            names = [preset.name.casefold() for preset in presets]
            if len(ids) != len(set(ids)):
                raise ValueError("破甲词 ID 不能重复")
            if len(names) != len(set(names)):
                raise ValueError("破甲词名称不能重复")
        return self


def _settings_payload(container: AppContainer) -> dict[str, Any]:
    return {
        "engine": container.settings.engine,
        "thinking": container.settings.thinking,
        "gateway": container.settings.gateway,
        "model": container.settings.model,
        "auxiliary_model": container.settings.auxiliary_model,
        "model_provider": container.settings.model_provider,
        "auxiliary_provider": container.settings.auxiliary_provider,
        "provider_allow_fallbacks": container.settings.provider_allow_fallbacks,
        "generation": container.settings.generation.model_dump(mode="json"),
        "appearance": container.settings.appearance.model_dump(mode="json"),
        "response_styles": [s.model_dump(mode="json") for s in container.settings.response_styles],
        "response_styles_revision": container.settings.response_styles_revision,
        "context_limit_override": container.settings.context_limit_override,
        "active_prompt_preset_id": container.settings.active_prompt_preset_id,
        "backup_retention_days": container.settings.backup_retention_days,
        "backup_keep_count": container.settings.backup_keep_count,
        "backup_max_bytes": container.settings.backup_max_bytes,
        "hygiene_enabled": container.settings.hygiene_enabled,
        "memory_consolidation_enabled": container.settings.memory_consolidation_enabled,
        "gateway_profiles": {
            profile_id: profile.model_dump(mode="json")
            for profile_id, profile in container.settings.gateway_profiles.items()
        },
        # Keep the previous field for compatibility with any already-open old client.
        "break_armor_prompt": container.settings.break_armor_prompt,
        "break_armor_prompts": [
            preset.model_dump(mode="json") for preset in container.settings.break_armor_prompts
        ],
        "active_break_armor_prompt_id": container.settings.active_break_armor_prompt_id,
        "break_armor_mode": container.settings.break_armor_mode,
        "break_armor_interval": container.settings.break_armor_interval,
        "tts": container.settings.tts.model_dump(mode="json"),
        "info": {
            "default_model": container.config.model,
            "gateway": container.config.base_url,
            "api_key_configured": container.api_key_configured,
            "fake_mode": container.fake_mode,
            "configured_providers": sorted(
                provider
                for provider, key in container.settings.provider_api_keys.items()
                if key
            ),
        },
    }


@router.get("/api/v1/settings")
async def get_settings(container: AppContainer = Depends(get_container)):
    """全量读回（前端必须以此为准，防"改了不显示"复发）。"""
    return _settings_payload(container)


@router.get("/api/v1/settings/models")
async def list_gateway_models(
    profile: str = Query(..., min_length=1, max_length=256),
    container: AppContainer = Depends(get_container),
):
    """Read the selected gateway's current model catalog without exposing its key."""
    profile_id = profile.strip()
    active_id = provider_profile_id(container.settings.gateway)
    if profile_id == active_id:
        gateway = container.settings.gateway
        api_key = container.settings.api_key
    elif profile_id in container.settings.gateway_profiles:
        gateway = container.settings.gateway_profiles[profile_id].gateway
        api_key = container.settings.provider_api_keys.get(profile_id, "")
    elif profile_id in ("openrouter", "getgoapi"):
        gateway = (
            "https://openrouter.ai/api/v1"
            if profile_id == "openrouter"
            else "https://api.getgoapi.com/v1"
        )
        api_key = container.settings.provider_api_keys.get(profile_id, "")
    else:
        raise HTTPException(404, "请先保存自定义接口，再读取模型列表")

    parsed = urlsplit(gateway)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, "网关地址无效")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{gateway.rstrip('/')}/models", headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "暂时无法读取模型列表") from exc
    if response.status_code in (401, 403):
        raise HTTPException(502, "模型列表需要有效的渠道密钥，请先保存密钥")
    if response.status_code != 200:
        raise HTTPException(502, f"模型列表请求失败（{response.status_code}）")
    try:
        payload = response.json()
        rows = payload.get("data") or payload.get("models") or []
        if not isinstance(rows, list):
            raise ValueError("model list is not an array")
    except (ValueError, AttributeError) as exc:
        raise HTTPException(502, "渠道返回的模型列表格式无效") from exc
    models = {}
    from mrp.reasoning import remember_capabilities, reasoning_capabilities
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            continue
        remember_capabilities(gateway, model_id, row)
        models[model_id] = {
            "id": model_id, "name": str(row.get("name") or model_id).strip(),
            "context_length": row.get("context_length") or row.get("context_window") or row.get("input_token_limit"),
            "max_completion_tokens": (row.get("top_provider") or {}).get("max_completion_tokens"),
            "reasoning": reasoning_capabilities(gateway, model_id) or None,
            "prompt_price_per_million": (
                float((row.get("pricing") or {}).get("prompt")) * 1_000_000
                if profile_id == "openrouter" and str((row.get("pricing") or {}).get("prompt", "")).replace(".", "", 1).isdigit()
                else None
            ),
            "completion_price_per_million": (
                float((row.get("pricing") or {}).get("completion")) * 1_000_000
                if profile_id == "openrouter" and str((row.get("pricing") or {}).get("completion", "")).replace(".", "", 1).isdigit()
                else None
            ),
        }
    return {"profile": profile_id, "models": sorted(models.values(), key=lambda row: (row["name"].casefold(), row["id"]))}


@router.get("/api/v1/settings/providers")
async def list_model_providers(
    model: str = Query(..., min_length=3, max_length=256),
):
    """查询 OpenRouter 当前为模型提供的可用上游；不需要或转发用户 API Key。"""
    model_id = model.strip().removeprefix("~")
    author, separator, slug = model_id.partition("/")
    if not separator or not author or not slug or any(ch.isspace() for ch in model_id):
        raise HTTPException(400, "模型 ID 应为提供商/模型名，例如 deepseek/deepseek-v4-flash")

    endpoint_url = (
        "https://openrouter.ai/api/v1/models/"
        f"{quote(author, safe='')}/{quote(slug, safe='')}/endpoints"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(endpoint_url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise HTTPException(502, "暂时无法读取 OpenRouter 的模型提供商列表") from exc
    if response.status_code == 404:
        raise HTTPException(404, "OpenRouter 中未找到这个模型")
    if response.status_code != 200:
        raise HTTPException(502, f"OpenRouter 提供商列表请求失败（{response.status_code}）")
    try:
        data = response.json().get("data") or {}
        endpoints = data.get("endpoints") or []
    except (ValueError, AttributeError) as exc:
        raise HTTPException(502, "OpenRouter 返回的提供商列表格式无效") from exc

    providers: dict[str, dict[str, Any]] = {}
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        status = endpoint.get("status")
        if status is not None and status not in (0, "0"):
            continue
        tag = str(endpoint.get("tag") or "").strip()
        name = str(endpoint.get("provider_name") or "").strip()
        if not tag or not name:
            continue
        # The base provider slug covers its endpoint variants and regions.
        provider_slug = tag.split("/", 1)[0]
        row = providers.setdefault(
            provider_slug,
            {"slug": provider_slug, "name": name, "endpoint_tags": [], "context_length": None},
        )
        if tag not in row["endpoint_tags"]:
            row["endpoint_tags"].append(tag)
        capacity = endpoint.get("max_prompt_tokens") or endpoint.get("context_length")
        if isinstance(capacity, int) and capacity > 0:
            old = row["context_length"]
            row["context_length"] = min(old, capacity) if old else capacity

    return {
        "model": str(data.get("id") or model_id),
        "providers": sorted(providers.values(), key=lambda item: (item["name"].casefold(), item["slug"])),
    }


@router.post("/api/v1/settings/test-connection")
async def test_connection(container: AppContainer = Depends(get_container)):
    """用已保存的网关、主模型和密钥做一次轻量真实请求；响应不包含密钥或模型正文。"""
    if not container.api_key_configured:
        raise HTTPException(400, "请先保存 API Key，再测试连接")
    if container.busy_any():
        raise HTTPException(409, "当前有角色正在生成，请稍后再测试")

    cfg = LlmConfig(
        model=container.settings.model,
        base_url=container.settings.gateway,
        api_key_env=container.config.api_key_env,
        max_tokens=16,
        provider=container.settings.model_provider,
        provider_allow_fallbacks=container.settings.provider_allow_fallbacks,
    )
    started = perf_counter()
    try:
        reply = await asyncio.to_thread(
            chat_text,
            [{"role": "user", "content": "Reply with OK."}],
            cfg,
            max_tokens=16,
            timeout=30.0,
            no_thinking=True,
        )
        if not reply.strip():
            raise RuntimeError("网关返回了空文本")
    except RuntimeError as exc:
        raise HTTPException(502, f"连接测试失败：{str(exc)[:240]}") from exc
    return {
        "ok": True,
        "model": container.settings.model,
        "latency_ms": round((perf_counter() - started) * 1000),
    }


@router.patch("/api/v1/settings")
async def patch_settings(req: PatchSettingsReq, container: AppContainer = Depends(get_container)):
    """全局推理设置；API Key 只写入本地设置文件，永不从此接口读回。

    修改立即生效：仅模型/网关/引擎路由变化会重建引擎；回复审查可热切换。
    """
    if container.busy_any() and req.model_fields_set != {"appearance"}:
        raise HTTPException(409, "回合进行中，稍后再试")
    next_appearance = container.settings.appearance
    if req.appearance is not None:
        next_appearance = AppearancePreferences.model_validate({
            **container.settings.appearance.model_dump(),
            **req.appearance.model_dump(exclude_unset=True, exclude_none=True),
        })
    if req.model_fields_set == {"appearance"}:
        # Visual preferences can be saved during generation without touching engines.
        previous_appearance = container.settings.appearance
        if next_appearance != previous_appearance:
            container.settings.appearance = next_appearance
            try:
                save_settings(container.data_root, container.settings)
            except Exception:
                container.settings.appearance = previous_appearance
                raise
        return _settings_payload(container)
    if req.response_styles is not None:
        if req.expected_response_styles_revision != container.settings.response_styles_revision:
            raise HTTPException(409, "风格库已更新，请重新加载后保存")
        ids = [s.id for s in req.response_styles]
        if len(ids) != len(set(ids)):
            raise HTTPException(400, "风格 ID 不能重复")
    if req.engine is not None and req.engine not in ("dsh", "openrouter"):
        raise HTTPException(400, "engine 须为 dsh/openrouter")
    if req.thinking is not None and req.thinking not in ("on", "off"):
        raise HTTPException(400, "thinking 须为 on/off")
    if req.clear_api_key and req.api_key is not None:
        raise HTTPException(400, "api_key 与 clear_api_key 不能同时提供")
    gateway = None
    if req.gateway is not None:
        gateway = req.gateway.strip().rstrip("/")
        parsed = urlsplit(gateway)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HTTPException(400, "网关地址须为有效的 http/https URL")
    model = req.model.strip() if req.model is not None else None
    if model == "":
        raise HTTPException(400, "模型名称不能为空")
    auxiliary_model = req.auxiliary_model.strip() if req.auxiliary_model is not None else None
    model_provider = req.model_provider.strip() if req.model_provider is not None else None
    auxiliary_provider = (
        req.auxiliary_provider.strip() if req.auxiliary_provider is not None else None
    )

    changed = False
    engine_config_before = (
        container.settings.engine, container.settings.thinking, container.settings.gateway,
        container.settings.model, container.settings.model_provider,
        container.settings.provider_allow_fallbacks, container.settings.api_key,
        container.settings.generation.model_dump(mode="json"),
    )
    previous_gateway = container.settings.gateway
    target_gateway = gateway or previous_gateway
    target_profile = provider_profile_id(target_gateway)

    # Validate the active selection against the complete preset set before mutating settings.
    if req.break_armor_prompts is not None:
        next_prompts = req.break_armor_prompts
    else:
        next_prompts = container.settings.break_armor_prompts
    if "active_break_armor_prompt_id" in req.model_fields_set:
        next_active_prompt_id = req.active_break_armor_prompt_id
    elif req.break_armor_prompts is not None and container.settings.active_break_armor_prompt_id not in {
        preset.id for preset in next_prompts
    }:
        next_active_prompt_id = None
    else:
        next_active_prompt_id = container.settings.active_break_armor_prompt_id
    if next_active_prompt_id is not None and next_active_prompt_id not in {
        preset.id for preset in next_prompts
    }:
        raise HTTPException(400, "当前选中的破甲词不存在")
    if (
        req.break_armor_prompts is not None
        and req.break_armor_prompts != container.settings.break_armor_prompts
    ):
        container.settings.break_armor_prompts = req.break_armor_prompts
        changed = True
    if next_active_prompt_id != container.settings.active_break_armor_prompt_id:
        container.settings.active_break_armor_prompt_id = next_active_prompt_id
        changed = True

    if gateway is not None and gateway != previous_gateway:
        activate_gateway_profile(container.settings, gateway)
        changed = True
    if req.engine is not None and req.engine != container.settings.engine:
        container.settings.engine = cast(EngineKind, req.engine)
        changed = True
    if req.thinking is not None and req.thinking != container.settings.thinking:
        container.settings.thinking = cast(Thinking, req.thinking)
        changed = True
    if model is not None and model != container.settings.model:
        container.settings.model = model
        changed = True
    if auxiliary_model is not None and auxiliary_model != container.settings.auxiliary_model:
        container.settings.auxiliary_model = auxiliary_model
        changed = True
    if req.break_armor_prompt is not None and req.break_armor_prompts is None:
        prompt = req.break_armor_prompt
        active = container.settings.active_break_armor_preset
        if active is not None and prompt != active.content:
            container.settings.break_armor_prompts = [
                preset.model_copy(update={"content": prompt}) if preset.id == active.id else preset
                for preset in container.settings.break_armor_prompts
            ]
            changed = True
        elif active is None and prompt.strip():
            profile_id = "default"
            existing_ids = {preset.id for preset in container.settings.break_armor_prompts}
            if profile_id in existing_ids:
                profile_id = "legacy-default"
            container.settings.break_armor_prompts = [
                *container.settings.break_armor_prompts,
                BreakArmorPromptPreset(id=profile_id, name="默认破甲词", content=prompt),
            ]
            container.settings.active_break_armor_prompt_id = profile_id
            changed = True
    if req.break_armor_mode is not None and req.break_armor_mode != container.settings.break_armor_mode:
        container.settings.break_armor_mode = req.break_armor_mode
        changed = True
    if (
        req.break_armor_interval is not None
        and req.break_armor_interval != container.settings.break_armor_interval
    ):
        container.settings.break_armor_interval = req.break_armor_interval
        changed = True
    if model_provider is not None and model_provider != container.settings.model_provider:
        container.settings.model_provider = model_provider
        changed = True
    if auxiliary_provider is not None and auxiliary_provider != container.settings.auxiliary_provider:
        container.settings.auxiliary_provider = auxiliary_provider
        changed = True
    if target_profile != "openrouter":
        if container.settings.model_provider:
            container.settings.model_provider = ""
            changed = True
        if container.settings.auxiliary_provider:
            container.settings.auxiliary_provider = ""
            changed = True
    if (
        req.provider_allow_fallbacks is not None
        and req.provider_allow_fallbacks != container.settings.provider_allow_fallbacks
    ):
        container.settings.provider_allow_fallbacks = req.provider_allow_fallbacks
        changed = True
    if req.generation is not None and req.generation != container.settings.generation:
        container.settings.generation = req.generation
        changed = True
    if "context_limit_override" in req.model_fields_set:
        if req.context_limit_override != container.settings.context_limit_override:
            container.settings.context_limit_override = req.context_limit_override
            changed = True
    elif (gateway is not None and gateway != previous_gateway) or (model is not None and model != engine_config_before[3]):
        # A capacity entered for one model must not silently carry to another.
        if container.settings.context_limit_override is not None:
            container.settings.context_limit_override = None
            changed = True
    if "active_prompt_preset_id" in req.model_fields_set:
        if req.active_prompt_preset_id and container.prompt_presets.get(req.active_prompt_preset_id) is None:
            raise HTTPException(404, "提示词方案不存在")
        container.settings.active_prompt_preset_id = req.active_prompt_preset_id
        changed = True
    for field in ("backup_retention_days", "backup_keep_count", "backup_max_bytes"):
        value = getattr(req, field)
        if value is not None and value != getattr(container.settings, field):
            setattr(container.settings, field, value)
            changed = True
    if req.hygiene_enabled is not None and req.hygiene_enabled != container.settings.hygiene_enabled:
        container.settings.hygiene_enabled = req.hygiene_enabled
        changed = True
    if req.memory_consolidation_enabled is not None and req.memory_consolidation_enabled != container.settings.memory_consolidation_enabled:
        container.settings.memory_consolidation_enabled = req.memory_consolidation_enabled
        changed = True
    if req.clear_api_key and container.settings.api_key:
        container.settings.api_key = ""
        if target_profile:
            container.settings.provider_api_keys.pop(target_profile, None)
        changed = True
    elif req.api_key is not None:
        api_key = req.api_key.strip()
        if api_key and api_key != container.settings.api_key:
            container.settings.api_key = api_key
            changed = True
        if api_key and target_profile and container.settings.provider_api_keys.get(target_profile) != api_key:
            container.settings.provider_api_keys[target_profile] = api_key
            changed = True
    if req.response_styles is not None:
        old_styles = {s.id: s for s in container.settings.response_styles}
        container.settings.response_styles = [s.model_copy(update={
            "revision": old_styles[s.id].revision + (s.model_dump(exclude={"revision"}) != old_styles[s.id].model_dump(exclude={"revision"}))
            if s.id in old_styles else 1,
        }) for s in req.response_styles]
        container.settings.response_styles_revision += 1
        changed = True
    previous_appearance = container.settings.appearance
    if next_appearance != previous_appearance:
        container.settings.appearance = next_appearance
        changed = True
    if changed:
        container._refresh_llm_config()
        for runner in container.runners.values():
            for character in runner.state.characters:
                container._apply_global_llm_settings(character)
        try:
            save_settings(container.data_root, container.settings)
        except Exception:
            container.settings.appearance = previous_appearance
            raise
        engine_config_after = (
            container.settings.engine, container.settings.thinking, container.settings.gateway,
            container.settings.model, container.settings.model_provider,
            container.settings.provider_allow_fallbacks, container.settings.api_key,
            container.settings.generation.model_dump(mode="json"),
        )
        if engine_config_after != engine_config_before:
            await container.engine_manager.shutdown_all()
            container.spawn_warm_recent()  # 换模型路由后预热；仅切回复审查无需冷启动
    return _settings_payload(container)
