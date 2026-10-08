"""全局设置（设置页数据面）：`data/settings.json`。

- 优先级：MRP_FAKE_ENGINE（测试/冒烟，最高）> settings.json > 内置默认。
- 首次启动（无 settings.json）时用 env 兜底推导思考开关（MRP_REASONING_EFFORT=off → off）。
- 文件缺失/损坏一律静默回落默认——设置读取绝不能挡住服务启动。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator
from mrp.response_styles import ResponseStyle

EngineKind = Literal["dsh", "openrouter"]
Thinking = Literal["on", "off"]
BreakArmorMode = Literal["opening", "interval"]


def _local_fish_paths() -> tuple[str, str, str]:
    """Fresh installations configure their own optional speech runtime paths."""
    return ("", "", "")


class TTSSettings(BaseModel):
    """Machine-local speech runtime settings; model weights stay outside the project."""

    enabled: bool = False
    executable_path: str = Field(default_factory=lambda: _local_fish_paths()[0])
    model_path: str = Field(default_factory=lambda: _local_fish_paths()[1])
    tokenizer_path: str = Field(default_factory=lambda: _local_fish_paths()[2])
    host: str = "127.0.0.1"
    port: int = Field(default=3030, ge=1024, le=65535)
    default_voice_profile_id: str | None = None


class BreakArmorPromptPreset(BaseModel):
    """A named, reusable global prompt used by the break-armor injector."""

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("破甲词名称不能为空")
        return value


class GatewayProfile(BaseModel):
    """Non-secret model and routing choices saved separately for each gateway."""

    gateway: str
    model: str
    auxiliary_model: str = ""
    model_provider: str = ""
    auxiliary_provider: str = ""
    provider_allow_fallbacks: bool = True


class GenerationSettings(BaseModel):
    """Optional chat generation parameters; unset fields leave provider defaults intact."""

    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=32768)

    def request_parameters(self) -> dict[str, float]:
        return {
            key: value for key in (
                "temperature", "top_p", "frequency_penalty", "presence_penalty"
            ) if (value := getattr(self, key)) is not None
        }


class AppearancePreferences(BaseModel):
    """Shared appearance choices. IDs stay open strings so a newer client can store a style this process does not draw.

    visual_style_id selects a button frame drawn by the web client. The ids shipped with this
    tree are listed in src/web/src/appearance/buttons/catalog.json and checked by
    mrp.appearance_catalog. Geometry stays in the client; an unknown but well-formed id is kept.
    """

    bubble_style_id: str = Field("star-track", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    theme_id: str = Field(default="astral", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    visual_style_id: str = Field(default="celestial-atelier", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    typography_id: str = Field(default="mincho", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    decoration_id: str = Field(default="celestial", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    cursor_id: str = Field(default="system", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    trail_id: str = Field(default="iridescent", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    click_effect_id: str = Field(default="none", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    effect_intensity: float = Field(default=0.65, ge=0, le=1)
    density: Literal["comfortable", "compact"] = "comfortable"
    avatar_frame_id: str = Field("iris-wreath", pattern=r"^[a-z][a-z0-9-]{0,63}$")
    dialogue_avatar_size: Literal[32, 40, 56] = 40
    primary_button_skin: bool = True
    secondary_button_skin: bool = False
    card_ornaments: bool = True
    card_border: bool = False
    background_art: bool = True
    portrait_placeholder: Literal["art", "initial"] = "art"
    reading_width: Literal["narrow", "standard", "wide"] = "standard"
    reading_font_size: int = Field(18, ge=14, le=24)


class AppSettings(BaseModel):
    """本地全局推理设置。API Key 隐藏在 repr 中，接口不会回传。"""

    engine: EngineKind = "dsh"
    response_styles: list[ResponseStyle] = Field(default_factory=list, max_length=200)
    response_styles_revision: int = 0
    thinking: Thinking = "on"
    gateway: str = "https://openrouter.ai/api/v1"
    model: str = "deepseek/deepseek-v4-flash"
    auxiliary_model: str = "deepseek/deepseek-v4-flash"
    model_provider: str = ""
    auxiliary_provider: str = ""
    provider_allow_fallbacks: bool = True
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    appearance: AppearancePreferences = Field(default_factory=AppearancePreferences)
    # Optional manual capacity for the currently selected model and gateway.
    context_limit_override: int | None = Field(default=None, ge=1024, le=10_000_000)
    active_prompt_preset_id: str | None = None
    backup_retention_days: int = Field(default=30, ge=1, le=3650)
    backup_keep_count: int = Field(default=20, ge=1, le=500)
    backup_max_bytes: int = Field(default=5_000_000_000, ge=100_000_000)
    hygiene_enabled: bool = False
    # Background and manual memory extraction both call the auxiliary model.
    # Off by default: recall of memories already stored still works.
    memory_consolidation_enabled: bool = False
    api_key: str = Field(default="", repr=False)
    # Each compatible gateway keeps its own machine-local key; never returned by API.
    provider_api_keys: dict[str, str] = Field(default_factory=dict, repr=False)
    gateway_profiles: dict[str, GatewayProfile] = Field(default_factory=dict)
    break_armor_prompts: list[BreakArmorPromptPreset] = Field(default_factory=list, max_length=100)
    active_break_armor_prompt_id: str | None = Field(default=None, max_length=80)
    break_armor_mode: BreakArmorMode = "opening"
    break_armor_interval: int = Field(default=10, ge=1, le=1000)
    tts: TTSSettings = Field(default_factory=TTSSettings)

    @property
    def active_break_armor_preset(self) -> BreakArmorPromptPreset | None:
        if self.active_break_armor_prompt_id is None:
            return None
        return next(
            (preset for preset in self.break_armor_prompts if preset.id == self.active_break_armor_prompt_id),
            None,
        )

    @property
    def break_armor_prompt(self) -> str:
        """Compatibility view for older callers; new storage uses named presets."""
        preset = self.active_break_armor_preset
        return preset.content if preset is not None else ""


def provider_profile_id(gateway: str) -> str:
    """Stable local key slot for a known gateway or a custom gateway host."""
    host = (urlsplit(gateway.strip()).hostname or "").lower()
    if host == "getgoapi.com" or host.endswith(".getgoapi.com"):
        return "getgoapi"
    if host == "openrouter.ai" or host.endswith(".openrouter.ai"):
        return "openrouter"
    return f"custom:{host}" if host else ""


def current_gateway_profile(settings: AppSettings) -> GatewayProfile:
    return GatewayProfile(
        gateway=settings.gateway,
        model=settings.model,
        auxiliary_model=settings.auxiliary_model,
        model_provider=settings.model_provider,
        auxiliary_provider=settings.auxiliary_provider,
        provider_allow_fallbacks=settings.provider_allow_fallbacks,
    )


def default_gateway_profile(gateway: str, *, fallback_model: str = "") -> GatewayProfile:
    profile_id = provider_profile_id(gateway)
    if profile_id == "getgoapi":
        model = "gemini-3.5-flash"
    elif profile_id == "openrouter":
        model = "deepseek/deepseek-v4-flash"
    else:
        model = fallback_model or "deepseek/deepseek-v4-flash"
    return GatewayProfile(gateway=gateway, model=model)


def activate_gateway_profile(settings: AppSettings, gateway: str) -> None:
    """Save the outgoing choices, then restore the selected gateway's choices and key."""
    previous_id = provider_profile_id(settings.gateway)
    target_id = provider_profile_id(gateway)
    if previous_id:
        settings.gateway_profiles[previous_id] = current_gateway_profile(settings)
        if settings.api_key:
            settings.provider_api_keys[previous_id] = settings.api_key
    if target_id == previous_id:
        settings.gateway = gateway
        return

    target = settings.gateway_profiles.get(target_id) or default_gateway_profile(
        gateway, fallback_model=settings.model
    )
    settings.gateway = gateway
    settings.model = target.model
    settings.auxiliary_model = target.auxiliary_model
    settings.model_provider = target.model_provider if target_id == "openrouter" else ""
    settings.auxiliary_provider = target.auxiliary_provider if target_id == "openrouter" else ""
    settings.provider_allow_fallbacks = target.provider_allow_fallbacks
    settings.api_key = settings.provider_api_keys.get(target_id, "")


def _env_default() -> AppSettings:
    """首次启动的默认值：尊重 env 的思考兜底（off 才 off，其余按 on=低档）。"""
    thinking: Thinking = "off" if os.environ.get("MRP_REASONING_EFFORT") == "off" else "on"
    model = os.environ.get("MRP_MODEL", "").strip() or "deepseek/deepseek-v4-flash"
    return AppSettings(
        thinking=thinking,
        gateway=os.environ.get("OPENROUTER_BASE_URL", "").strip() or "https://openrouter.ai/api/v1",
        model=model,
        auxiliary_model=os.environ.get("MRP_AUX_MODEL", "").strip() or model,
        api_key="",
    )


def settings_path(data_root: Path) -> Path:
    return Path(data_root) / "settings.json"


def load_settings(data_root: Path) -> AppSettings:
    """读设置；文件缺失/损坏 → 默认（不抛异常）。"""
    try:
        raw = json.loads(settings_path(data_root).read_text(encoding="utf-8"))
        values = _env_default().model_dump()
        if isinstance(raw, dict):
            # A missing or damaged appearance field must not discard saved model/key settings.
            appearance_values = AppearancePreferences().model_dump()
            raw_appearance = raw.get("appearance")
            if isinstance(raw_appearance, dict):
                for field_name in appearance_values:
                    if field_name not in raw_appearance:
                        continue
                    try:
                        candidate = AppearancePreferences.model_validate({
                            field_name: raw_appearance[field_name],
                        })
                    except ValueError:
                        continue
                    appearance_values[field_name] = getattr(candidate, field_name)
            raw["appearance"] = appearance_values
            # Migrate the former single text field into a named preset without losing it.
            legacy_prompt = raw.pop("break_armor_prompt", None)
            if "break_armor_prompts" not in raw:
                raw["break_armor_prompts"] = (
                    [{"id": "default", "name": "默认破甲词", "content": legacy_prompt}]
                    if isinstance(legacy_prompt, str) and legacy_prompt.strip()
                    else []
                )
            presets = raw.get("break_armor_prompts")
            if not isinstance(presets, list):
                presets = []
                raw["break_armor_prompts"] = presets
            preset_ids = {
                str(preset.get("id"))
                for preset in presets
                if isinstance(preset, dict) and preset.get("id")
            }
            if "active_break_armor_prompt_id" not in raw:
                raw["active_break_armor_prompt_id"] = next(
                    (
                        str(preset["id"])
                        for preset in presets
                        if isinstance(preset, dict) and preset.get("id")
                    ),
                    None,
                )
            elif raw["active_break_armor_prompt_id"] is not None and raw["active_break_armor_prompt_id"] not in preset_ids:
                raw["active_break_armor_prompt_id"] = next(
                    (
                        str(preset["id"])
                        for preset in presets
                        if isinstance(preset, dict) and preset.get("id")
                    ),
                    None,
                )
            # Older settings used interval=0 to mean “opening only”. Preserve that behavior.
            if raw.get("break_armor_interval") == 0:
                raw["break_armor_interval"] = 1
            if "auxiliary_model" not in raw:
                raw["auxiliary_model"] = raw.get("model") or values["auxiliary_model"]
            keys = raw.get("provider_api_keys")
            keys = dict(keys) if isinstance(keys, dict) else {}
            active_provider = provider_profile_id(str(raw.get("gateway") or values["gateway"]))
            active_key = raw.get("api_key")
            if active_provider and isinstance(active_key, str) and active_key:
                keys.setdefault(active_provider, active_key)
            raw["provider_api_keys"] = keys
        values.update(raw)
        settings = AppSettings.model_validate(values)
        active_id = provider_profile_id(settings.gateway)
        if active_id:
            # Legacy files have only global model fields. Treat those fields as
            # the active gateway's profile without discarding the old values.
            settings.gateway_profiles[active_id] = current_gateway_profile(settings)
        return settings
    except FileNotFoundError:
        return _env_default()
    except Exception:  # noqa: BLE001 —— 损坏文件静默回落默认
        return _env_default()


def save_settings(data_root: Path, settings: AppSettings) -> None:
    """落盘（临时文件 + 原子替换，cephfs 安全）。"""
    p = settings_path(data_root)
    active_id = provider_profile_id(settings.gateway)
    if active_id:
        settings.gateway_profiles[active_id] = current_gateway_profile(settings)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)
