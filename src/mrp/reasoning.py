"""Normalize reasoning controls against published model capabilities.

Catalog reads refresh this process-local registry. The documented Aion 3.5
capabilities also work before the settings catalog has been opened or offline.
No requests, credentials or story content are stored here.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

_capabilities: dict[tuple[str, str], dict[str, Any]] = {}
_AION_35 = {"mandatory": True, "supported_efforts": ["low", "high", "max"], "default_effort": "high"}


def _host(gateway: str) -> str:
    return (urlsplit(gateway).hostname or "").lower()


def is_openrouter(gateway: str) -> bool:
    host = _host(gateway)
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


def remember_capabilities(gateway: str, model: str, row: dict[str, Any]) -> None:
    reasoning = row.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("mandatory"), bool):
        _capabilities[(_host(gateway), model)] = dict(reasoning)


def reasoning_capabilities(gateway: str, model: str) -> dict[str, Any]:
    cached = _capabilities.get((_host(gateway), model))
    if cached is not None:
        return dict(cached)
    if is_openrouter(gateway) and model.split(":", 1)[0] in {
        "aion-labs/aion-3.5", "aion-labs/aion-3.5-mini",
    }:
        return dict(_AION_35)
    return {}


def normalize_reasoning(body: dict[str, Any], gateway: str) -> bool:
    """Repair unsupported controls in place; return whether reasoning is required.

    Keep output budgets and provider routing untouched. Mandatory reasoning can
    consume the whole output budget, but that never authorizes a paid retry with
    a larger budget or a forbidden attempt to disable it.
    """
    capabilities = reasoning_capabilities(gateway, str(body.get("model") or ""))
    if not capabilities.get("mandatory"):
        return False
    if not is_openrouter(gateway):
        return False
    reasoning = body.get("reasoning")
    reasoning = dict(reasoning) if isinstance(reasoning, dict) else {}
    efforts = capabilities.get("supported_efforts") or []
    effort = reasoning.get("effort", body.get("reasoning_effort"))
    if effort not in efforts:
        effort = "low" if "low" in efforts else capabilities.get("default_effort")
    reasoning.pop("max_tokens", None)  # Don't retain a conflicting reasoning cap.
    reasoning["enabled"] = True
    if effort:
        reasoning["effort"] = effort
    else:
        reasoning.pop("effort", None)
    body.pop("reasoning_effort", None)
    body["reasoning"] = reasoning
    return True
