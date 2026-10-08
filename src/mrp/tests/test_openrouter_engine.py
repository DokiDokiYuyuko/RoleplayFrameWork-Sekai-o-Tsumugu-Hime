"""R48 OpenRouterEngine 单测：请求体、usage 映射、真流式回调、错误（MockTransport 零真实请求）。"""
from __future__ import annotations

import json
import os

import httpx
import pytest

from mrp.engines.openrouter import OpenRouterEngine
from mrp.shared.models import Character, CharacterCard, ModelConfig, TurnContext

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")


@pytest.fixture(autouse=True)
def _test_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


def _char(max_tokens: int = 1234) -> Character:
    return Character(
        id="char-a",
        card=CharacterCard(name="测试甲", description="合成角色甲简介", first_mes="你好"),
        llm=ModelConfig(sampling={"max_tokens": max_tokens}),
    )


def _ctx() -> TurnContext:
    return TurnContext(session_id="sess-x", character_id="char-a", turn=3)


def _client_factory(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


async def test_blocking_request_shape_and_usage():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "你好呀"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 2},
                },
            },
        )

    engine = OpenRouterEngine(thinking=True, client_factory=_client_factory(handler))
    await engine.start(_char())
    assert await engine.is_alive() is True
    reply = await engine.generate(_ctx())

    body = seen["body"]
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer test-key"
    assert body["model"] == "deepseek/deepseek-v4-flash"
    assert body["max_tokens"] == 1234
    assert body["reasoning"] == {"effort": "low"}
    assert "stream" not in body
    assert body["messages"][0]["role"] == "system"
    assert '你是"测试甲"' in body["messages"][0]["content"]
    assert "合成角色甲简介" in body["messages"][0]["content"]
    assert body["messages"][1]["role"] == "user"

    assert reply.content == "你好呀"
    assert reply.finish_reason == "stop"
    assert reply.usage.input_tokens == 10 and reply.usage.output_tokens == 5
    assert reply.usage.cached_tokens == 2
    assert reply.engine_session_id == "sess-x-char-a-0003-or"
    await engine.stop()


async def test_streaming_emits_deltas_and_usage():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        sse = (
            'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            'data: {"usage":{"prompt_tokens":10,"completion_tokens":2}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse.encode()
        )

    pieces: list[str] = []
    engine = OpenRouterEngine(thinking=False, client_factory=_client_factory(handler))
    await engine.start(_char())
    reply = await engine.generate(_ctx(), on_delta=pieces.append)

    assert seen["body"]["stream"] is True
    assert seen["body"]["reasoning"] == {"enabled": False}
    assert pieces == ["你", "好"]
    assert reply.content == "你好"
    assert reply.finish_reason == "stop"
    assert reply.usage.output_tokens == 2
    await engine.stop()


async def test_http_error_raises_runtime_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    engine = OpenRouterEngine(client_factory=_client_factory(handler))
    await engine.start(_char())
    with pytest.raises(RuntimeError, match="OpenRouter 500"):
        await engine.generate(_ctx())
    assert engine.health().errors == 1
    await engine.stop()


async def test_generate_before_start_raises():
    engine = OpenRouterEngine()
    with pytest.raises(RuntimeError, match="before start"):
        await engine.generate(_ctx())


async def test_empty_reasoning_reply_retries_without_thinking_and_counts_usage():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if body["reasoning"] == {"effort": "low"}:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 100},
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "这次有回复"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 6},
        })

    engine = OpenRouterEngine(thinking=True, client_factory=_client_factory(handler))
    await engine.start(_char())
    reply = await engine.generate(_ctx())
    assert reply.content == "这次有回复"
    assert [item["reasoning"] for item in requests] == [{"effort": "low"}, {"enabled": False}]
    assert reply.usage.input_tokens == 20 and reply.usage.output_tokens == 106
    await engine.stop()


async def test_empty_reply_still_empty_after_retry_is_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        })

    engine = OpenRouterEngine(thinking=True, client_factory=_client_factory(handler))
    await engine.start(_char())
    with pytest.raises(RuntimeError, match="模型返回空回复"):
        await engine.generate(_ctx())
    assert engine.health().errors == 1
    await engine.stop()


async def test_empty_stream_retries_and_emits_only_visible_retry_text():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 2:
            return httpx.Response(200, json={
                'choices': [{'message': {'content': '你好'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 3, 'completion_tokens': 4},
            })
        piece = ''
        sse = (
            f'data: {{"choices":[{{"delta":{{"content":"{piece}"}}}}]}}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            'data: {"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\n'
            'data: [DONE]\n\n'
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())

    pieces: list[str] = []
    engine = OpenRouterEngine(thinking=True, client_factory=_client_factory(handler))
    await engine.start(_char())
    reply = await engine.generate(_ctx(), on_delta=pieces.append)
    assert reply.content == '你好' and pieces == ['你好']
    assert requests[1]['reasoning'] == {'enabled': False}
    assert requests[1].get('stream') is None
    assert reply.usage.input_tokens == 6 and reply.usage.output_tokens == 8
    await engine.stop()
