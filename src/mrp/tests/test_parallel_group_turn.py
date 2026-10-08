"""同轮并行点名与重跑：群体和正式角色均不能被遗漏。"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import pytest

from mrp.orchestrator.session import SessionRunner
from mrp.llm import LlmConfig, chat_stream_with_usage
from mrp.shared.models import (
    Character, CharacterCard, EngineReply, GroupActor, Message, Scene,
    SessionMeta, SessionState, TokenUsage,
)


class EventSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.group_final = asyncio.Event()

    async def publish(self, session_id, event, payload, *, lossy=False) -> None:
        self.events.append((event, payload))
        if event == "message.final" and payload["message"]["actor"] == "group-a":
            self.group_final.set()


class WaitingEngine:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.histories: list[list[str]] = []
        self.visible_text: list[list[str]] = []
        self.turn_contexts = []

    async def generate(self, character, ctx, *, on_delta=None):
        self.turn_contexts.append(ctx)
        self.histories.append([message.actor for message in ctx.visible_messages])
        self.visible_text.append([message.content for message in ctx.visible_messages])
        self.started.set()
        await self.release.wait()
        if on_delta is not None:
            on_delta("正式角色回复")
        return EngineReply(
            content="正式角色回复", usage=TokenUsage(input_tokens=10, output_tokens=4),
            engine_session_id="test-character",
        )


class WaitingGroup:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.histories: list[list[str]] = []
        self.visible_text: list[list[str]] = []

    async def generate(
        self, state, group, exclude_message_id=None, feedback="", on_delta=None,
        parallel_plan="", reply_mode="single", participants=None,
    ):
        visible = [message for message in state.messages if message.status == "final"]
        self.histories.append([message.actor for message in visible])
        self.visible_text.append([message.content for message in visible])
        self.started.set()
        await self.release.wait()
        if on_delta is not None:
            on_delta("群体回复")
        trace = {
            "system": "test", "user": "test", "visible_messages": visible,
            "group_details": "test", "source_id": group.id,
            "tokens_by_section": {"group": 1, "system": 1, "scene": 1, "history": 1, "instructions": 1},
        }
        return "群体回复", {"input_tokens": 8, "output_tokens": 3, "cached_tokens": 0}, trace


class OrderedEngine:
    def __init__(self): self.contexts = []; self.turn_contexts = []; self.fail = False
    async def generate(self, character, ctx, *, on_delta=None):
        self.turn_contexts.append(ctx)
        self.contexts.append([(m.actor, m.content) for m in ctx.visible_messages])
        if self.fail: raise RuntimeError("safe test failure")
        return EngineReply(content="人物实际回复", usage=TokenUsage())


class OrderedGroup:
    def __init__(self): self.contexts = []; self.plan_calls = 0; self.plans = []; self.fail = False
    async def plan_parallel_scene(self, state, turn, names):
        self.plan_calls += 1
        return "second planner call", {"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0}
    async def generate(self, state, group, exclude_message_id=None, feedback="", on_delta=None,
                       parallel_plan="", reply_mode="single", participants=None):
        self.plans.append(parallel_plan)
        visible = [m for m in state.messages if m.status == "final"]
        self.contexts.append([(m.actor, m.content) for m in visible])
        if self.fail: raise RuntimeError("safe test failure")
        trace = {"system":"test", "user":"test", "visible_messages":visible,
                 "group_details":"test", "source_id":group.id,
                 "tokens_by_section":{"group":1,"system":1,"scene":1,"history":1,"instructions":1}}
        return "群体实际回复", {"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0}, trace


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [["char-a", "group-a"], ["group-a", "char-a"]])
async def test_serial_character_group_order_passes_first_visible_reply(order):
    character = Character(id="char-a", card=CharacterCard(name="向导"))
    group = GroupActor(id="group-a", label="街边摊主们", scene_id="scene-a", joined_seq=0)
    scene = Scene(id="scene-a", title="街道", member_ids=[character.id], group_ids=[group.id])
    state = SessionState(meta=SessionMeta(id="sess-serial", character_ids=[character.id], director_mode="rules",
        hygiene_enabled=False, short_input_padding=False, proactive_turn_limit=0), characters=[character],
        groups=[group], scenes=[scene], active_scene_id=scene.id)
    engine, responder = OrderedEngine(), OrderedGroup()
    runner = SessionRunner(state, engine, group_responder=responder)
    result = await runner.player_say_with_reply_mode("请按顺序接话", mentions=order, reply_mode="serial")
    assert [m.actor for m in result[1:]] == order
    if order[0] == "char-a":
        assert any(actor == "char-a" and content == "人物实际回复" for actor, content in responder.contexts[0])
    else:
        assert any(actor == "group-a" and content == "群体实际回复" for actor, content in engine.contexts[0])
        frame = engine.turn_contexts[0].reply_frame
        assert frame.mode == "serial"
        assert frame.visible_prior_speaker_labels == ["群体·街边摊主们"]
        assert "[群体·街边摊主们] 群体实际回复" in engine.turn_contexts[0].planned_prompt.text
    player = result[0]
    assert player.executed_reply_mode == "serial"
    assert player.reply_order == order


@pytest.mark.asyncio
async def test_serial_failure_keeps_success_and_leaves_no_pending_message():
    character = Character(id="char-a", card=CharacterCard(name="向导"))
    group = GroupActor(id="group-a", label="街边摊主们", scene_id="scene-a", joined_seq=0)
    scene = Scene(id="scene-a", title="街道", member_ids=[character.id], group_ids=[group.id])
    state = SessionState(meta=SessionMeta(id="sess-serial-failure", character_ids=[character.id], director_mode="rules",
        hygiene_enabled=False, short_input_padding=False, proactive_turn_limit=0), characters=[character],
        groups=[group], scenes=[scene], active_scene_id=scene.id)
    engine, responder = OrderedEngine(), OrderedGroup()
    responder.fail = True
    runner = SessionRunner(state, engine, group_responder=responder)
    result = await runner.player_say_with_reply_mode("接力测试", mentions=["char-a", "group-a"], reply_mode="serial")
    assert [message.actor for message in result] == ["player", "char-a"]
    assert [message.actor for message in runner.state.messages] == ["player", "char-a"]
    assert all(message.status != "pending" for message in runner.state.messages)
    assert runner.runtime.turn_errors and "后续接力已停止" in runner.runtime.turn_errors[0]


@pytest.mark.asyncio
async def test_serial_regeneration_failure_restores_entire_previous_turn():
    character = Character(id="char-a", card=CharacterCard(name="向导"))
    group = GroupActor(id="group-a", label="街边摊主们", scene_id="scene-a", joined_seq=0)
    scene = Scene(id="scene-a", title="街道", member_ids=[character.id], group_ids=[group.id])
    state = SessionState(meta=SessionMeta(id="sess-serial-rerun", character_ids=[character.id], director_mode="rules",
        hygiene_enabled=False, short_input_padding=False, proactive_turn_limit=0), characters=[character],
        groups=[group], scenes=[scene], active_scene_id=scene.id)
    engine, responder = OrderedEngine(), OrderedGroup()
    runner = SessionRunner(state, engine, group_responder=responder)
    client_id = "msg-1234567890ab"
    first = await runner.player_say_with_reply_mode("接力测试", mentions=["group-a", "char-a"], reply_mode="serial",
                                    client_message_id=client_id)
    old_tail = [(message.id, message.actor, message.content) for message in first[1:]]
    engine.fail = True
    with pytest.raises(RuntimeError):
        await runner.regenerate_turn(client_id)
    assert [(message.id, message.actor, message.content) for message in runner.state.messages[1:]] == old_tail
    assert all(message.status == "final" for message in runner.state.messages)


@pytest.mark.asyncio
async def test_serial_later_actor_cannot_see_private_first_reply():
    first = Character(id="char-a", card=CharacterCard(name="向导"))
    second = Character(id="char-b", card=CharacterCard(name="守卫"))
    scene = Scene(id="scene-a", title="街道", member_ids=[first.id, second.id])
    state = SessionState(meta=SessionMeta(id="sess-private-chain", character_ids=[first.id, second.id], director_mode="rules",
        hygiene_enabled=False, short_input_padding=False, proactive_turn_limit=0), characters=[first, second],
        scenes=[scene], active_scene_id=scene.id)
    engine = OrderedEngine()
    runner = SessionRunner(state, engine)
    original = runner.turns.run_character_turn
    async def restrict_first_reply(character, turn, **kwargs):
        message = await original(character, turn, **kwargs)
        if character.id == first.id:
            message.visible_to = [first.id]
        return message
    runner.turns.run_character_turn = restrict_first_reply
    await runner.player_say_with_reply_mode("接力测试", mentions=[first.id, second.id], reply_mode="serial")
    second_history = engine.contexts[1]
    assert not any(actor == first.id and content == "人物实际回复" for actor, content in second_history)
    assert engine.turn_contexts[1].reply_frame.visible_prior_reply_ids == []


@pytest.mark.asyncio
async def test_auto_planner_uses_public_context_and_reuses_parallel_scene(monkeypatch):
    character = Character(id="char-a", card=CharacterCard(name="向导"))
    group = GroupActor(id="group-a", label="街边摊主们", scene_id="scene-a", joined_seq=0)
    scene = Scene(id="scene-a", title="街道", member_ids=[character.id], group_ids=[group.id])
    state = SessionState(meta=SessionMeta(id="sess-auto", character_ids=[character.id], director_mode="rules",
        hygiene_enabled=False, short_input_padding=False, proactive_turn_limit=0), characters=[character],
        groups=[group], scenes=[scene], active_scene_id=scene.id)
    state.messages.append(Message(
        session_id=state.meta.id, seq=0, turn=1, actor="player", content="私人备注",
        visible_to=["player"], scene_id=scene.id))
    engine, responder = OrderedEngine(), OrderedGroup()
    runner = SessionRunner(state, engine, group_responder=responder)
    captured = []
    def planner(messages, config, **kwargs):
        captured.append(messages)
        return (json.dumps({"mode":"parallel", "order":["char-a", "group-a"],
                            "reason":"共同反应", "shared_scene":"共用的场面安排"}, ensure_ascii=False),
                {"input_tokens": 4, "output_tokens": 3, "cached_tokens": 0})
    monkeypatch.setattr("mrp.orchestrator.turn_engine.chat_text_with_usage", planner)
    result = await runner.player_say_with_reply_mode("安全的公开邀请", mentions=["char-a", "group-a"], reply_mode="auto")
    assert [item.actor for item in result[1:]] == ["char-a", "group-a"]
    assert "私人备注" not in captured[0][1]["content"]
    assert "不得预写任何参与者未来的动作、台词" in captured[0][0]["content"]
    assert responder.plan_calls == 0
    assert responder.plans == ["共用的场面安排"]
    assert result[0].executed_reply_mode == "parallel"
    assert result[0].reply_shared_scene == "共用的场面安排"
    assert runner.runtime.cost_by_purpose["director"]["input_tokens"] == 4


@pytest.mark.asyncio
async def test_invalid_auto_plan_falls_back_to_click_order(monkeypatch):
    character = Character(id="char-a", card=CharacterCard(name="向导"))
    group = GroupActor(id="group-a", label="街边摊主们", scene_id="scene-a", joined_seq=0)
    scene = Scene(id="scene-a", title="街道", member_ids=[character.id], group_ids=[group.id])
    state = SessionState(meta=SessionMeta(id="sess-auto-fallback", character_ids=[character.id], director_mode="rules",
        hygiene_enabled=False, short_input_padding=False, proactive_turn_limit=0), characters=[character],
        groups=[group], scenes=[scene], active_scene_id=scene.id)
    engine, responder = OrderedEngine(), OrderedGroup()
    runner = SessionRunner(state, engine, group_responder=responder)
    monkeypatch.setattr("mrp.orchestrator.turn_engine.chat_text_with_usage",
        lambda *args, **kwargs: ('{"mode":"serial","order":["char-a","char-a"]}',
                                 {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}))
    result = await runner.player_say_with_reply_mode("回退测试", mentions=["group-a", "char-a"], reply_mode="auto")
    assert [item.actor for item in result[1:]] == ["group-a", "char-a"]
    assert result[0].executed_reply_mode == "serial"
    assert result[0].reply_order == ["group-a", "char-a"]
    assert "自动判断失败" in result[0].reply_reason


@pytest.mark.asyncio
async def test_explicit_character_and_group_run_together_and_regenerate_together():
    character = Character(id="char-a", card=CharacterCard(name="甲"))
    group = GroupActor(id="group-a", label="路人群", scene_id="scene-a", joined_seq=0)
    scene = Scene(id="scene-a", title="街道", member_ids=[character.id], group_ids=[group.id])
    state = SessionState(
        meta=SessionMeta(
            id="sess-parallel", character_ids=[character.id], director_mode="rules",
            hygiene_enabled=False, short_input_padding=False, proactive_turn_limit=0,
        ),
        characters=[character], groups=[group], scenes=[scene], active_scene_id=scene.id,
    )
    engine, responder, sink = WaitingEngine(), WaitingGroup(), EventSink()
    runner = SessionRunner(state, engine, sink=sink, group_responder=responder)
    client_id = "msg-1234567890ab"

    first = asyncio.create_task(runner.player_say(
        "请两位回答", mentions=[character.id, group.id], client_message_id=client_id,
    ))
    await asyncio.wait_for(asyncio.gather(engine.started.wait(), responder.started.wait()), 2)
    assert [payload["message"]["actor"] for event, payload in sink.events if event == "message.pending"] == [character.id, group.id]
    assert [message.actor for message in runner.state.messages] == ["player"]
    responder.release.set()
    await asyncio.wait_for(sink.group_final.wait(), 2)
    assert not any(
        event == "message.final" and payload["message"]["actor"] == character.id
        for event, payload in sink.events
    )
    engine.release.set()
    created = await asyncio.wait_for(first, 2)

    assert [message.actor for message in created] == ["player", character.id, group.id]
    assert [message.seq for message in runner.state.messages] == sorted(message.seq for message in runner.state.messages)
    assert engine.histories[0] == ["player"]
    assert responder.histories[0] == ["player"]
    assert "正式角色回复" not in engine.visible_text[0] + responder.visible_text[0]
    assert "群体回复" not in engine.visible_text[0] + responder.visible_text[0]
    assert engine.turn_contexts[0].reply_frame.mode == "parallel"
    assert engine.turn_contexts[0].reply_frame.visible_prior_reply_ids == []
    assert engine.turn_contexts[0].reply_frame.trigger_message_ids == [created[0].id]
    assert group.last_spoke_turn == 1
    old_ids = {message.id for message in created[1:]}

    rerun = await asyncio.wait_for(runner.regenerate_turn(client_id), 2)
    assert rerun is not None
    assert [message.actor for message in rerun] == [character.id, group.id]
    assert all(message.id not in old_ids for message in runner.state.messages)
    assert [message.actor for message in runner.state.messages] == ["player", character.id, group.id]
    assert len([item for item in runner.state.director_log if item.turn == 1]) == 1
    repeated = await runner.player_say(
        "请两位回答", mentions=[character.id, group.id], client_message_id=client_id,
    )
    assert [message.id for message in repeated] == [message.id for message in runner.state.messages]
    assert len(runner.state.messages) == 3


def test_group_gateway_stream_forwards_chunks_and_usage(monkeypatch):
    sent_bodies = []

    class Response:
        status_code = 200

        def iter_lines(self):
            yield 'data: ' + json.dumps({"choices": [{"delta": {"content": "你好"}}]})
            yield 'data: ' + json.dumps({"choices": [{"delta": {"content": "，世界"}}]})
            yield 'data: ' + json.dumps({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4}})
            yield 'data: [DONE]'

    @contextmanager
    def stream(method, url, *, headers, json, timeout):
        sent_bodies.append(json)
        yield Response()

    monkeypatch.setattr("mrp.llm.httpx.stream", stream)
    chunks = []
    text, usage = chat_stream_with_usage(
        [{"role": "user", "content": "test"}],
        LlmConfig(model="demo", base_url="https://example.invalid/api/v1", api_key_env="TEST_KEY"),
        chunks.append, no_thinking=True,
    )
    assert chunks == ["你好", "，世界"]
    assert text == "你好，世界"
    assert usage == {
        "input_tokens": 12, "output_tokens": 4, "cached_tokens": 0,
        "finish_reason": "unknown",
    }
    assert sent_bodies[0]["stream"] is True
    assert "reasoning" not in sent_bodies[0]  # OpenRouter controls never leak to other gateways.
