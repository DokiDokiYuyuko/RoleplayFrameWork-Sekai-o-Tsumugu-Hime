"""Model-aware prompt capacity lookup. Unknown limits stay unknown, never 8192 by fiat."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx

from mrp.settings import provider_profile_id
from mrp.reasoning import remember_capabilities


@dataclass(frozen=True)
class ModelCapacity:
    context_limit: int | None
    input_limit: int | None
    output_reserve: int
    source: str
    fetched_at: str | None = None


_cache: dict[tuple[str, str, str, bool], tuple[float, int | None, str, str | None]] = {}
_locks: dict[tuple[str, str, str, bool], asyncio.Lock] = {}
_CACHE_SECONDS = 3600


def _positive(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def _lookup(gateway: str, model: str, key: str, provider: str,
                  allow_fallbacks: bool) -> tuple[int | None, str]:
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    profile = provider_profile_id(gateway)
    async with httpx.AsyncClient(timeout=12.0) as client:
        if profile == "openrouter" and "/" in model:
            author, slug = model.removeprefix("~").split("/", 1)
            root = "https://openrouter.ai/api/v1"
            url = f"{root}/models/{quote(author, safe='')}/{quote(slug, safe='')}/endpoints"
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json().get("data") or {}
            remember_capabilities(gateway, model, data)
            endpoints = [row for row in data.get("endpoints", []) if isinstance(row, dict)
                         and row.get("status") in (None, 0, "0")]
            if provider and not allow_fallbacks:
                endpoints = [row for row in endpoints
                             if str(row.get("tag") or "").split("/", 1)[0] == provider]
            limits = [_positive(row.get("max_prompt_tokens") or row.get("context_length"))
                      for row in endpoints]
            limits = [limit for limit in limits if limit is not None]
            if limits:
                return min(limits), "openrouter_endpoint"
            if provider and not allow_fallbacks:
                return None, "unknown"
            model_limit = _positive(data.get("context_length"))
            return model_limit, "openrouter_model" if model_limit else "unknown"

        response = await client.get(f"{gateway.rstrip('/')}/models", headers=headers)
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or payload.get("models") or []
        for row in rows:
            if isinstance(row, dict) and str(row.get("id")) == model:
                remember_capabilities(gateway, model, row)
                limit = _positive(row.get("context_length") or row.get("context_window")
                                  or row.get("input_token_limit"))
                return limit, "gateway_catalog" if limit else "unknown"
    return None, "unknown"


async def resolve_model_capacity(settings, model: str, *, reply_max_tokens: int | None = None,
                                 base_url: str | None = None, model_provider: str | None = None,
                                 api_key: str | None = None) -> ModelCapacity:
    gateway = (base_url or settings.gateway).rstrip("/")
    provider = (settings.model_provider if model_provider is None else model_provider) if provider_profile_id(gateway) == "openrouter" else ""
    cache_key = (gateway, model, provider, bool(settings.provider_allow_fallbacks))
    override = getattr(settings, "context_limit_override", None)
    if override and model == settings.model and gateway == settings.gateway.rstrip("/"):
        limit, source = int(override), "user_override"
        fetched_at = None
    else:
        cached = _cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            limit, source = cached[1], cached[2]
            fetched_at = cached[3]
        else:
            lock = _locks.setdefault(cache_key, asyncio.Lock())
            async with lock:
                cached = _cache.get(cache_key)
                if cached and cached[0] > time.monotonic():
                    limit, source = cached[1], cached[2]
                    fetched_at = cached[3]
                else:
                    try:
                        limit, source = await _lookup(
                            gateway, model, settings.api_key if api_key is None else api_key, provider,
                            settings.provider_allow_fallbacks,
                        )
                        fetched_at = datetime.now(timezone.utc).isoformat()
                    except (httpx.HTTPError, ValueError, KeyError, TypeError):
                        if cached and cached[1] is not None:
                            limit, source, fetched_at = cached[1], f"stale_{cached[2]}", cached[3]
                        else:
                            limit, source, fetched_at = None, "unknown", None
                    _cache[cache_key] = (time.monotonic() + (_CACHE_SECONDS if limit else 300), limit, source, fetched_at)
    configured_output = reply_max_tokens if reply_max_tokens and reply_max_tokens > 0 else settings.generation.max_output_tokens
    output_reserve = int(configured_output or (min(8192, max(256, limit // 4)) if limit else 8192))
    if limit is None:
        return ModelCapacity(None, None, output_reserve, source, fetched_at)
    # Reserve response space and protocol/harness overhead, without imposing
    # an unrelated fixed prompt ceiling on long-context models.
    overhead = max(1024, min(8192, limit // 100))
    return ModelCapacity(limit, max(0, limit - output_reserve - overhead), output_reserve, source, fetched_at)
