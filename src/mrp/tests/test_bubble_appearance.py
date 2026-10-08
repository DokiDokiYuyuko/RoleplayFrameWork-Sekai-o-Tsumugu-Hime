"""Appearance changes use isolated settings and never require story migration."""
from __future__ import annotations

import json

import pytest

from mrp.settings import AppSettings, load_settings, save_settings


def test_legacy_preferences_keep_model_choices(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "example/model", "appearance": {"theme_id": "night"}}))
    settings = load_settings(tmp_path)
    assert settings.model == "example/model"
    assert settings.appearance.theme_id == "night"
    assert settings.appearance.bubble_style_id == "star-track"


@pytest.mark.parametrize("style", ["star-track", "book-note", "crystal", "moonlight", "plain", "future-style"])
def test_bubble_roundtrip_is_extensible(tmp_path, style):
    settings = AppSettings(appearance={"bubble_style_id": style})
    save_settings(tmp_path, settings)
    assert load_settings(tmp_path).appearance.bubble_style_id == style


def test_damaged_bubble_does_not_discard_other_preferences(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({
        "model": "example/model", "appearance": {"bubble_style_id": "../bad", "theme_id": "night"},
    }))
    settings = load_settings(tmp_path)
    assert settings.model == "example/model"
    assert settings.appearance.theme_id == "night"
    assert settings.appearance.bubble_style_id == "star-track"


def test_bubble_patch_is_partial_and_never_rebuilds_engines(mrp_client, monkeypatch):
    container = mrp_client.app.state.container
    def unexpected(*args, **kwargs):
        pytest.fail("Appearance changes must not rebuild engines")
    monkeypatch.setattr(container, "_refresh_llm_config", unexpected)
    before = mrp_client.get("/api/v1/settings").json()
    result = mrp_client.patch("/api/v1/settings", json={"appearance": {"bubble_style_id": "moonlight"}})
    assert result.status_code == 200
    assert result.json()["appearance"]["bubble_style_id"] == "moonlight"
    for field in ("model", "gateway", "engine", "generation"):
        assert result.json()[field] == before[field]
    assert mrp_client.get("/api/v1/settings").json()["appearance"]["bubble_style_id"] == "moonlight"
    assert mrp_client.patch("/api/v1/settings", json={"appearance": {"bubble_style_id": "../bad"}}).status_code >= 400
