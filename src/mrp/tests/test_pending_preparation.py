"""Waiting bubbles precede expensive preparation in every reply mode."""
import asyncio

import pytest

from mrp.tests.test_integration import make_runner


@pytest.mark.parametrize("mode,mentions", [
    ("serial", ["char-a"]),
    ("serial", ["char-b", "char-a"]),
    ("parallel", ["char-a", "char-b"]),
])
async def test_pending_is_emitted_before_capacity_and_live_reply(mode, mentions):
    runner, sink = make_runner(replies=["新的回答", "另一位的回答"])
    entered, release = asyncio.Event(), asyncio.Event()
    capacity = runner.context_builder.model_capacity
    generate = runner.engines.generate

    async def blocked_capacity(character, **kwargs):
        entered.set()
        await release.wait()
        return await capacity(character, **kwargs)

    async def live_generate(character, context, **kwargs):
        reply = await generate(character, context)
        if kwargs.get("on_delta"):
            kwargs["on_delta"](reply.content)
            await asyncio.sleep(0.08)
        return reply

    runner.context_builder.model_capacity = blocked_capacity
    runner.engines.generate = live_generate
    task = asyncio.create_task(runner.turns.player_say("大家好，介绍一下路线。", mentions=mentions, reply_mode=mode))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        pending = [payload for name, payload in sink.events if name == "message.pending"]
        assert pending
        assert pending[0]["character_id"] == mentions[0]
        assert not [p for n, p in sink.events if n == "message.final" and p["message"]["actor"] != "player"]
        release.set()
        await asyncio.wait_for(task, 5)
        for actor in mentions:
            names = [name for name, payload in sink.events if
                     payload.get("character_id") == actor or payload.get("message", {}).get("actor") == actor]
            assert names.index("message.pending") < names.index("message.delta") < names.index("message.final")
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await runner.engines.shutdown_all()


async def test_preparation_failure_removes_waiting_bubble():
    runner, sink = make_runner()

    async def fail(character, **kwargs):
        raise RuntimeError("synthetic preparation failure")

    runner.context_builder.model_capacity = fail
    try:
        await runner.turns.player_say("你好", mentions=["char-a", "char-b"], reply_mode="serial")
        errors = [payload for name, payload in sink.events if name == "message.error"]
        assert errors and errors[-1]["discard_pending"] is True
        assert errors[-1]["message_id"] == next(p["message"]["id"] for n, p in sink.events if n == "message.pending")
    finally:
        await runner.engines.shutdown_all()
