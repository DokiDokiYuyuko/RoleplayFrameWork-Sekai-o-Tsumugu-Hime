"""Synthetic transport and frozen-channel checks for world organization."""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from mrp.llm import LlmConfig, chat_stream_with_usage
from mrp.orchestrator.model_capacity import ModelCapacity
from mrp.settings import provider_profile_id
from mrp.tests.test_world_organize import SOURCE, archive_candidate, make_world
from mrp.world_organize.schemas import CreateJobInput


@pytest.mark.parametrize("old_gateway,new_gateway", [
    ("https://openrouter.ai/api/v1", "https://api.getgoapi.com/v1"),
    ("https://api.getgoapi.com/v1", "https://openrouter.ai/api/v1"),
])
@pytest.mark.parametrize("retained_key", [True, False])
def test_frozen_gateway_uses_its_key_and_routing_after_global_channel_switch(
    mrp_client, monkeypatch, old_gateway, new_gateway, retained_key,
):
    world = make_world(mrp_client)
    container = mrp_client.app.state.container
    container._fake_mode = False
    service = container.world_organize
    settings = container.settings
    settings.gateway = old_gateway
    settings.model = "synthetic/main"
    settings.auxiliary_model = "synthetic/organizer"
    settings.auxiliary_provider = "frozen-supplier"
    settings.provider_allow_fallbacks = False
    settings.api_key = "synthetic-original-key"
    settings.provider_api_keys = {
        provider_profile_id(old_gateway): "synthetic-original-key",
        provider_profile_id(new_gateway): "synthetic-other-key",
    } if retained_key else {provider_profile_id(new_gateway): "synthetic-other-key"}
    monkeypatch.setattr(service, "_schedule", lambda _job_id: None)
    job = service.create(CreateJobInput(world_id=world["id"], category="archives", source_text=SOURCE))
    snapshot = service._snapshot(job["id"])
    settings.gateway = new_gateway
    settings.model = settings.auxiliary_model = "synthetic/other-model"
    settings.auxiliary_provider = "other-supplier"
    settings.provider_allow_fallbacks = True
    settings.api_key = "synthetic-other-key"
    settings.generation.max_output_tokens = 128
    capacity_calls, gateway_calls = [], []

    async def capacity(routed, model, **kwargs):
        capacity_calls.append((routed, model, kwargs))
        return ModelCapacity(65536, 60000, 5536, "synthetic-catalog")

    def chat(messages, config, on_delta, **kwargs):
        gateway_calls.append(config)
        response = json.dumps({"archives": [archive_candidate()]})
        on_delta(response)
        return response, {"finish_reason": "stop"}

    monkeypatch.setattr("mrp.world_organize.service.resolve_model_capacity", capacity)
    monkeypatch.setattr("mrp.world_organize.service.chat_stream_with_usage", chat)
    batches = mrp_client.portal.call(service._plan_archive_batches, job, snapshot)
    assert batches == [snapshot]
    routed, model, kwargs = capacity_calls[0]
    expected_key = "synthetic-original-key" if retained_key else ""
    assert routed.gateway == kwargs["base_url"] == old_gateway
    assert routed.model == model == "synthetic/organizer"
    assert routed.model_provider == kwargs["model_provider"] == "frozen-supplier"
    assert routed.provider_allow_fallbacks is False
    assert routed.api_key == kwargs["api_key"] == expected_key
    assert routed.generation.max_output_tokens is None
    assert kwargs["reply_max_tokens"] == 0
    if retained_key:
        response = mrp_client.portal.call(service._call, "Synthetic prompt", job["id"], 1)
        assert response == json.dumps({"archives": [archive_candidate()]})
        config = gateway_calls[0]
        assert config.api_key == expected_key and config.base_url == old_gateway
        assert config.model == "synthetic/organizer" and config.provider_allow_fallbacks is False
        assert config.provider == ("frozen-supplier" if provider_profile_id(old_gateway) == "openrouter" else "")
    else:
        with pytest.raises(ValueError, match="API Key"):
            mrp_client.portal.call(service._call, "Synthetic prompt", job["id"], 1)
        assert gateway_calls == []
    assert service._streams == {}
    public_job = json.dumps(service.get(job["id"]))
    assert "synthetic-original-key" not in public_job and "synthetic-other-key" not in public_job
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world


@pytest.mark.parametrize("with_done", [True, False])
@pytest.mark.parametrize("strict", [True, False])
def test_strict_stream_rejects_missing_done_even_after_stop_reason(monkeypatch, with_done, strict):
    response_text = json.dumps({"archives": [archive_candidate()]})

    class Response:
        status_code = 200

        def iter_lines(self):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": response_text}}]})
            yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                                        "usage": {"prompt_tokens": 4, "completion_tokens": 6}})
            if with_done:
                yield "data: [DONE]"

    @contextmanager
    def stream(*_args, **_kwargs):
        yield Response()

    monkeypatch.setattr("mrp.llm.httpx.stream", stream)
    chunks = []
    text, usage = chat_stream_with_usage(
        [{"role": "user", "content": "Synthetic prompt"}],
        LlmConfig(model="synthetic/model", base_url="https://example.invalid/v1", api_key_env="SYNTHETIC_TEST_KEY", api_key="synthetic-key"),
        chunks.append, require_complete=strict,
    )
    assert chunks == [response_text] and text == response_text
    assert usage["input_tokens"] == 4 and usage["output_tokens"] == 6
    assert usage["finish_reason"] == ("connection_closed" if strict and not with_done else "stop")
