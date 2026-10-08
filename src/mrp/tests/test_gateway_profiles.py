"""Gateway switching keeps each channel's model, routing, and key together."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mrp.server.deps import get_container
from mrp.server.routers import settings as settings_routes
from mrp.settings import AppSettings, load_settings


class _EngineManager:
    async def shutdown_all(self) -> None:
        pass


class _Container:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.settings = AppSettings(
            engine="openrouter",
            gateway="https://openrouter.ai/api/v1",
            model="google/original-model",
            auxiliary_model="",
            model_provider="atlas-cloud",
            api_key="openrouter-test-key",
            provider_api_keys={"openrouter": "openrouter-test-key", "getgoapi": "getgo-test-key"},
        )
        self.config = SimpleNamespace(model=self.settings.model, base_url=self.settings.gateway, api_key_env="TEST_KEY")
        self.characters = {}
        self.runners = {}
        self.engine_manager = _EngineManager()
        self.fake_mode = False

    @property
    def api_key_configured(self) -> bool:
        return bool(self.settings.api_key)

    def busy_any(self) -> bool:
        return False

    def _refresh_llm_config(self) -> None:
        self.config.model = self.settings.model
        self.config.base_url = self.settings.gateway

    def spawn_warm_recent(self) -> None:
        pass


def _client(container: _Container) -> TestClient:
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[get_container] = lambda: container
    return TestClient(app)


def test_legacy_settings_gain_an_active_profile(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(json.dumps({
        "gateway": "https://api.getgoapi.com/v1",
        "model": "gemini-3.5-flash",
        "auxiliary_model": "gemini-3.5-flash",
        "api_key": "legacy-test-key",
    }), encoding="utf-8")
    settings = load_settings(tmp_path)
    assert settings.gateway_profiles["getgoapi"].model == "gemini-3.5-flash"
    assert settings.provider_api_keys["getgoapi"] == "legacy-test-key"


def test_switching_gateway_restores_models_provider_and_key(tmp_path: Path) -> None:
    container = _Container(tmp_path)
    with _client(container) as client:
        getgo = client.patch("/api/v1/settings", json={
            "gateway": "https://api.getgoapi.com/v1",
            "model": "gemini-3.5-flash",
            "auxiliary_model": "",
            "model_provider": "",
        })
        assert getgo.status_code == 200, getgo.text
        assert getgo.json()["model"] == "gemini-3.5-flash"
        assert getgo.json()["model_provider"] == ""
        assert getgo.json()["info"]["api_key_configured"] is True
        assert "getgo-test-key" not in getgo.text
        assert "openrouter-test-key" not in getgo.text

        restored = client.patch("/api/v1/settings", json={"gateway": "https://openrouter.ai/api/v1"})
        assert restored.status_code == 200, restored.text
        assert restored.json()["model"] == "google/original-model"
        assert restored.json()["model_provider"] == "atlas-cloud"
        assert restored.json()["auxiliary_model"] == ""
        assert container.settings.api_key == "openrouter-test-key"

    disk = load_settings(tmp_path)
    assert disk.gateway_profiles["getgoapi"].model == "gemini-3.5-flash"
    assert disk.gateway_profiles["openrouter"].model_provider == "atlas-cloud"


def test_model_catalog_uses_selected_gateways_key_without_returning_it(tmp_path: Path, monkeypatch) -> None:
    container = _Container(tmp_path)
    requests = []

    class Response:
        status_code = 200

        def json(self):
            return {"data": [{"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash"}]}

    class AsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, url, headers):
            requests.append((url, headers.get("Authorization")))
            return Response()

    monkeypatch.setattr(settings_routes.httpx, "AsyncClient", AsyncClient)
    with _client(container) as client:
        response = client.get("/api/v1/settings/models?profile=getgoapi")
        assert response.status_code == 200, response.text
        model = response.json()["models"][0]
        assert model == {
            "id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash",
            "context_length": None, "max_completion_tokens": None,
            "reasoning": None,
            "prompt_price_per_million": None, "completion_price_per_million": None,
        }
        assert "getgo-test-key" not in response.text
    assert requests == [("https://api.getgoapi.com/v1/models", "Bearer getgo-test-key")]
