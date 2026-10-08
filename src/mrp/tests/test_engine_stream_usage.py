"""W4：引擎层修复回归。

- DshEngine C13：回合事件默认不物化（MRP_ENGINE_DEBUG_EVENTS=1 才保留）
- DshEngine B12：会话已存在判定（JsonRpcError structured 路径 + 文案常量）
- DshEngine：profile 文件写走 asyncio.to_thread（不卡事件循环）
- OpenRouter C14：流式请求带 stream_options.include_usage 并解析 usage；
  网关不支持时降级重试一次
- OpenRouter：on_delta 回调异常不中断生成
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from types import SimpleNamespace

import httpx
import pytest

import mrp.engines.dsh.engine as dsh_engine_mod
from mrp.engines.dsh.engine import DshEngine
from mrp.engines.openrouter import OpenRouterEngine
from mrp.shared.models import Character, CharacterCard, ModelConfig, TurnContext


def _char() -> Character:
    return Character(
        id="char-a",
        card=CharacterCard(name="测试甲", description="合成角色甲简介"),
        llm=ModelConfig(sampling={"max_tokens": 1234}),
    )


def _ctx() -> TurnContext:
    return TurnContext(session_id="sess-x", character_id="char-a", turn=3)


class _HarnessStub:
    """DeepSeekHarness 替身：首次按需抛错，之后返回固定事件。"""

    def __init__(self, events: list[dict] | None = None, first_error: BaseException | None = None):
        self.events = list(events or [])
        self.first_error = first_error
        self.ids: list[str] = []

    def run(self, text: str, session_id: str):  # noqa: ANN201
        self.ids.append(session_id)
        if self.first_error is not None and len(self.ids) == 1:
            raise self.first_error
        return SimpleNamespace(final_response="你好", finish_reason="stop", events=self.events)


def _engine_with(harness: _HarnessStub) -> DshEngine:
    engine = DshEngine()
    engine._harness = harness
    engine._character = _char()
    return engine


# ---------- C13 ----------


async def test_dsh_engine_events_not_materialized_by_default(monkeypatch):
    monkeypatch.delenv("MRP_ENGINE_DEBUG_EVENTS", raising=False)
    events = [
        {
            "type": "assistant/message",
            "data": {"message": {"usage": {"input_tokens": 7, "output_tokens": 3}}},
        }
    ]
    reply = await _engine_with(_HarnessStub(events)).generate(_ctx())
    assert reply.raw_events == []  # 默认不物化（省一次全量拷贝）
    assert reply.usage.input_tokens == 7 and reply.usage.output_tokens == 3  # usage 仍提取
    assert reply.content == "你好"


async def test_dsh_engine_debug_env_keeps_events(monkeypatch):
    monkeypatch.setenv("MRP_ENGINE_DEBUG_EVENTS", "1")
    events = [{"type": "assistant/message", "data": {"usage": {"input_tokens": 1}}}]
    reply = await _engine_with(_HarnessStub(events)).generate(_ctx())
    assert reply.raw_events and reply.raw_events[0]["type"] == "assistant/message"


# ---------- B12 ----------


async def test_dsh_engine_jsonrpc_session_exists_retries():
    from deepseek_harness.errors import JsonRpcError

    def factory() -> _HarnessStub:
        stub = _HarnessStub()
        first = JsonRpcError(-32000, 'session "sess-x-char-a-0003" already exists')
        stub.first_error = first
        return stub

    harness = factory()
    reply = await _engine_with(harness).generate(_ctx())
    assert len(harness.ids) == 2  # 换后缀重试
    assert harness.ids[1].startswith(harness.ids[0] + "-")
    assert reply.content == "你好"


async def test_dsh_engine_jsonrpc_other_error_not_retried():
    from deepseek_harness.errors import JsonRpcError

    harness = _HarnessStub()
    harness.first_error = JsonRpcError(-32603, "internal error")
    engine = _engine_with(harness)
    with pytest.raises(JsonRpcError):
        await engine.generate(_ctx())
    assert len(harness.ids) == 1  # 非会话冲突不上后缀重试
    assert engine._errors == 1


def test_dsh_profile_write_offloaded_to_thread(monkeypatch, tmp_path):
    seen: dict[str, str] = {}

    def spy_write(home, profile_name="main"):  # noqa: ANN001
        seen["thread"] = threading.current_thread().name
        return home

    monkeypatch.setattr(dsh_engine_mod, "write_character_profile", spy_write)
    monkeypatch.setattr(
        dsh_engine_mod,
        "DeepSeekHarness",
        lambda cfg: SimpleNamespace(start=lambda: None, close=lambda: None, client=None),
    )

    async def run() -> DshEngine:
        engine = DshEngine(engines_root=tmp_path)
        await engine.start(_char())
        return engine

    engine = asyncio.run(run())
    assert seen["thread"] != threading.current_thread().name  # 跑在线程池里
    assert engine._harness is not None  # start 完成（替身已就位）


# ---------- OpenRouter C14 ----------


def _client_factory(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def _sse_response(*chunks: str) -> httpx.Response:
    body = "".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


async def test_openrouter_streaming_requests_and_parses_usage():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _sse_response(
            '{"choices":[{"delta":{"content":"你"}}]}',
            '{"choices":[{"delta":{"content":"好"}}]}',
            '{"choices":[{"delta":{},"finish_reason":"stop"}]}',
            '{"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":4,'
            '"prompt_tokens_details":{"cached_tokens":2}}}',
        )

    pieces: list[str] = []
    engine = OpenRouterEngine(client_factory=_client_factory(handler))
    await engine.start(_char())
    reply = await engine.generate(_ctx(), on_delta=pieces.append)

    assert seen["body"]["stream"] is True
    assert seen["body"]["stream_options"] == {"include_usage": True}
    assert pieces == ["你", "好"]
    assert reply.content == "你好"
    assert reply.usage.input_tokens == 10
    assert reply.usage.output_tokens == 4
    assert reply.usage.cached_tokens == 2
    await engine.stop()


async def test_openrouter_stream_options_unsupported_falls_back(caplog):
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if "stream_options" in body:
            return httpx.Response(400, text='{"error":{"message":"unknown field: stream_options"}}')
        return _sse_response('{"choices":[{"delta":{"content":"好"}}]}')

    engine = OpenRouterEngine(client_factory=_client_factory(handler))
    await engine.start(_char())
    with caplog.at_level(logging.WARNING, logger="mrp.engine.openrouter"):
        reply = await engine.generate(_ctx(), on_delta=lambda _s: None)
    assert len(calls) == 2 and "stream_options" not in calls[1]  # 降级重试一次
    assert reply.content == "好"
    assert any("stream_options" in r.message for r in caplog.records)

    calls.clear()
    await engine.generate(_ctx(), on_delta=lambda _s: None)  # 后续不再带该参数
    assert "stream_options" not in calls[0]
    await engine.stop()


async def test_openrouter_on_delta_exception_does_not_break_generation(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response(
            '{"choices":[{"delta":{"content":"你"}}]}',
            '{"choices":[{"delta":{"content":"好"}}]}',
        )

    def boom(_piece: str) -> None:
        raise ValueError("回调炸了")

    engine = OpenRouterEngine(client_factory=_client_factory(handler))
    await engine.start(_char())
    with caplog.at_level(logging.WARNING, logger="mrp.engine.openrouter"):
        reply = await engine.generate(_ctx(), on_delta=boom)
    assert reply.content == "你好"  # 生成不中断
    assert sum(1 for r in caplog.records if "on_delta" in r.message) == 1  # 只告警一次
    await engine.stop()
