"""Synthetic mandatory-reasoning requests across every gateway transport."""
import json
from contextlib import contextmanager

import httpx
import pytest

from mrp import reasoning
from mrp.engines.dsh.process import EngineManager
from mrp.engines.dsh.provider_proxy import ProviderRoutingProxy
from mrp.engines.openrouter import OpenRouterEngine
from mrp.llm import LlmConfig, chat_text_with_usage, chat_stream_with_usage
from mrp.shared.models import Character, CharacterCard, ModelConfig, TurnContext

GATEWAY = "https://openrouter.ai/api/v1"
MODEL = "aion-labs/aion-3.5-mini"


@pytest.fixture(autouse=True)
def clean_capabilities(monkeypatch):
    monkeypatch.setattr(reasoning, "_capabilities", {})


def character():
    return Character(id="synthetic-actor", card=CharacterCard(name="Synthetic"),
                     llm=ModelConfig(model=MODEL, base_url=GATEWAY, sampling={"max_tokens": 256}))


def result(content="OK", finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 12}}


@pytest.mark.parametrize("thinking", [True, False])
@pytest.mark.parametrize("streaming", [True, False])
async def test_direct_engine_never_disables_mandatory_reasoning(thinking, streaming):
    requests = []
    def handle(request):
        body = json.loads(request.content); requests.append(body)
        assert body["reasoning"] == {"enabled": True, "effort": "low"}
        assert body["max_tokens"] == 256
        if body.get("stream"):
            data = {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}], "usage": result()["usage"]}
            return httpx.Response(200, text="data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json=result())
    engine = OpenRouterEngine(thinking=thinking, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    await engine.start(character())
    deltas = []
    reply = await engine.generate(TurnContext(session_id="synthetic", character_id="synthetic-actor", turn=1),
                                  on_delta=deltas.append if streaming else None)
    assert reply.content == "OK" and len(requests) == 1
    assert reply.usage.output_tokens == 12
    if streaming: assert deltas == ["OK"]
    await engine.stop()


async def test_mandatory_reasoning_budget_failure_is_not_paid_twice():
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=result("", "length"))
    manager = EngineManager(engine_factory=lambda: OpenRouterEngine(thinking=True,
                            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle))))
    with pytest.raises(RuntimeError, match="推理耗尽") as caught:
        await manager.generate(character(), TurnContext(session_id="synthetic", character_id="synthetic-actor", turn=1))
    assert len(calls) == 1
    assert caught.value.usage.output_tokens == 12
    assert len(caught.value.usage_calls) == 1
    await manager.shutdown_all()


@pytest.mark.parametrize("streaming", [True, False])
def test_auxiliary_helpers_use_supported_reasoning(monkeypatch, streaming):
    seen = []
    def post(url, *, json, **kwargs):
        seen.append(json)
        return httpx.Response(200, json=result())
    @contextmanager
    def stream(method, url, *, json, **kwargs):
        seen.append(json)
        data = {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}], "usage": result()["usage"]}
        yield httpx.Response(200, text="data: " + __import__('json').dumps(data) + "\n\ndata: [DONE]\n\n")
    monkeypatch.setattr("mrp.llm.httpx.post", post)
    monkeypatch.setattr("mrp.llm.httpx.stream", stream)
    config = LlmConfig(model=MODEL, base_url=GATEWAY, api_key_env="SYNTHETIC_KEY", max_tokens=256)
    if streaming:
        text, usage = chat_stream_with_usage([{"role": "user", "content": "Synthetic"}], config, lambda delta: None, no_thinking=True)
    else:
        text, usage = chat_text_with_usage([{"role": "user", "content": "Synthetic"}], config, no_thinking=True)
    assert text == "OK" and usage["output_tokens"] == 12
    assert seen[0]["reasoning"] == {"enabled": True, "effort": "low"}
    assert seen[0]["max_tokens"] == 256


def test_dsh_proxy_normalizes_before_archiving_and_forwarding(monkeypatch):
    original_client = httpx.Client
    sent, archived = [], []
    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=result())
    monkeypatch.setattr("mrp.engines.dsh.provider_proxy.httpx.Client",
                        lambda **kwargs: original_client(transport=httpx.MockTransport(handle)))
    proxy = ProviderRoutingProxy(GATEWAY, "aion-labs", False, on_request=archived.append)
    try:
        with original_client(trust_env=False) as client:
            response = client.post(proxy.base_url + "/chat/completions", json={
                "model": MODEL, "messages": [{"role": "user", "content": "Synthetic"}],
                "reasoning_effort": "none", "reasoning": {"enabled": False}, "max_tokens": 256})
        assert response.status_code == 200
        assert archived == sent
        assert sent[0]["reasoning"] == {"enabled": True, "effort": "low"}
        assert "reasoning_effort" not in sent[0]
        assert sent[0]["provider"] == {"order": ["aion-labs"], "allow_fallbacks": False}
    finally:
        proxy.close()


def test_catalog_capabilities_apply_to_other_models_without_touching_optional_models():
    model = "synthetic/required-reasoning"
    reasoning.remember_capabilities(GATEWAY, model, {"reasoning": {
        "mandatory": True, "supported_efforts": ["high"], "default_effort": "high"}})
    body = {"model": model, "reasoning": {"enabled": False, "effort": "none", "exclude": True}}
    assert reasoning.normalize_reasoning(body, GATEWAY)
    assert body["reasoning"] == {"enabled": True, "effort": "high", "exclude": True}
    body = {"model": "synthetic/optional", "reasoning": {"enabled": False}}
    assert not reasoning.normalize_reasoning(body, GATEWAY)
    assert body["reasoning"] == {"enabled": False}
    assert not reasoning.normalize_reasoning({"model": MODEL}, "https://example.invalid/v1")
