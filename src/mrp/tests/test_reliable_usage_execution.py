"""Offline final requests and durable receipts through actual generation flows."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from mrp.engines.dsh.process import EngineManager
from mrp.engines.fake import FakeEngine
from mrp.engines.openrouter import OpenRouterEngine
from mrp.orchestrator.group_actors import GroupResponder
from mrp.orchestrator.model_capacity import ModelCapacity
from mrp.orchestrator.session import SessionRunner
from mrp.orchestrator.usage import label_main_call, record_usage
from mrp.server.container import AppContainer, AppContainerConfig
from mrp.settings import AppSettings, provider_profile_id
from mrp.shared.models import (
    Character, CharacterCard, EngineReply, GroupActor, Message, Scene,
    SessionMeta, SessionState, TokenUsage, TurnContext, UsageRecord,
)
from mrp.storage.usage_ledger import UsageLedger


@pytest.fixture
def offline_capacity(monkeypatch):
    calls = []

    async def resolve(settings, model, **kwargs):
        calls.append((model, kwargs))
        return ModelCapacity(None, None, 0, "offline-test")

    monkeypatch.setattr("mrp.orchestrator.model_capacity.resolve_model_capacity", resolve)
    return calls


class ScriptedEngine(FakeEngine):
    def __init__(self):
        super().__init__()
        self.responses = [
            ("我答应你，等到", "length", 10, 3),
            ("天亮再出发。", "stop", 7, 4),
            ("另一个选择是", "max_tokens", 5, 2),
        ]

    async def generate(self, ctx, *, on_delta=None):
        self.calls.append(ctx)
        content, reason, input_tokens, output_tokens = self.responses.pop(0)
        return EngineReply(content=content, finish_reason=reason,
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens))


@pytest.mark.asyncio
async def test_truncated_continue_candidates_and_restart(tmp_path, offline_capacity):
    data_root = tmp_path / "data"
    container = AppContainer(data_root=data_root, config=AppContainerConfig(fake_mode=True))
    engine = ScriptedEngine()
    container.engine_manager = EngineManager(engine_factory=lambda: engine)
    character = Character(card=CharacterCard(name="向导"))
    await container.save_character(character)
    runner = await container.create_session("合成续写", [character.id], "", [])
    runner.state.meta.streaming_enabled = False
    runner.state.meta.memory_enabled = False
    messages = await runner.player_say("我们什么时候出发？", force_character=character.id)
    reply = next(message for message in messages if message.actor == character.id)
    assert reply.generation_meta.finish_reason == "length"
    assert reply.generation_meta.completion_state == "truncated"
    initial_generation = reply.generation_meta.generation_id

    continued = await runner.continue_message(reply.id)
    assert continued.content == "我答应你，等到天亮再出发。"
    assert continued.generation_meta.completion_state == "complete"
    assert continued.generation_meta.generation_id != initial_generation
    assert continued.generation_meta.message_id == reply.id
    assert continued.generation_id == continued.generation_meta.generation_id
    assert continued.operation_id == continued.generation_meta.operation_id
    assert continued.attempt_id == continued.generation_meta.attempt_id
    assert continued.generation_meta.provenance.complete
    assert {ref.message_id for ref in continued.generation_meta.provenance.sources} == {messages[0].id}
    assert continued.generation_meta.injected_entry_ids == engine.calls[1].planned_prompt.included_entry_ids
    assert engine.calls[1].visible_messages[-1].id == reply.id
    assert runner.cost_report()["total"] == {
        "input_tokens": 17, "output_tokens": 7, "cached_tokens": 0,
    }
    assert runner.cost_report()["by_purpose"]["continue"] == {
        "input_tokens": 7, "output_tokens": 4, "cached_tokens": 0,
    }

    alternate = await runner.swipe(reply.id)
    assert alternate.generation_meta.completion_state == "truncated"
    assert alternate.generation_meta.finish_reason == "max_tokens"
    assert all(message.id != reply.id for message in engine.calls[2].visible_messages)
    assert len(alternate.variants) == 3
    await runner.switch_variant(reply.id, 0)
    assert reply.content == "我答应你，等到"
    assert reply.generation_meta.finish_reason == "length"
    sid = runner.state.meta.id
    await container.persist_session(runner)
    await container.aclose()

    restored_container = AppContainer(data_root=data_root, config=AppContainerConfig(fake_mode=True))
    restored = await restored_container.load_session(sid)
    restored_reply = next(message for message in restored.state.messages if message.id == reply.id)
    assert restored_reply.generation_meta.completion_state == "truncated"
    assert restored.cost_report()["call_count"] == 3
    assert restored.cost_report()["total"] == {
        "input_tokens": 22, "output_tokens": 9, "cached_tokens": 0,
    }
    assert restored.cost_report()["by_purpose"]["continue"]["input_tokens"] == 7
    await restored.switch_variant(reply.id, 1)
    assert restored_reply.generation_meta.completion_state == "complete"
    assert restored_reply.content.endswith("天亮再出发。")
    assert restored_reply.generation_meta.provenance.complete
    assert reply.id not in {ref.message_id for ref in restored_reply.generation_meta.provenance.sources}
    await restored.switch_variant(reply.id, 2)
    assert restored_reply.generation_meta.completion_state == "truncated"
    assert restored.cost_report()["call_count"] == 3
    await restored_container.aclose()


@pytest.mark.parametrize("gateway,saved_key,expected_provider", [
    ("https://role.example/v1", "synthetic-role-key", None),
    ("https://role.example/v1", "", None),
    ("https://active.example/v1", "", None),
])
@pytest.mark.asyncio
async def test_explicit_model_gateway_and_key_reach_final_http(
    tmp_path, offline_capacity, monkeypatch, gateway, saved_key, expected_provider,
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "unused-original-test-key")
    data_root = tmp_path / "data"
    settings = AppSettings(engine="openrouter", gateway="https://active.example/v1",
        model="active/model", model_provider="active-provider", api_key="synthetic-active-key",
        provider_api_keys={provider_profile_id(gateway): saved_key} if saved_key else {})
    captured = []

    def handler(request):
        captured.append((str(request.url), request.headers.get("Authorization"), json.loads(request.content)))
        return httpx.Response(200, json={"choices": [{"message": {"content": "合成回应。"},
            "finish_reason": "stop"}], "usage": {"prompt_tokens": 8, "completion_tokens": 4}})

    container = AppContainer(data_root=data_root, settings=settings, config=AppContainerConfig(fake_mode=True))
    container.engine_manager = EngineManager(engine_factory=lambda: OpenRouterEngine(
        provider="active-provider", thinking=False,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler))))
    character = Character(card=CharacterCard(name="指定角色"))
    character.llm.model = "explicit/model"
    character.llm.base_url = gateway
    character.llm.inherit_model = False
    character.llm.inherit_base_url = False
    await container.save_character(character)
    runner = await container.create_session("配置验证", [character.id], "", [])
    runner.state.meta.streaming_enabled = False
    runner.state.meta.memory_enabled = False
    await runner.player_say("请确认目前的约定。", force_character=character.id)
    url, authorization, body = captured[-1]
    assert url == gateway + "/chat/completions"
    expected_key = "synthetic-active-key" if gateway == settings.gateway else saved_key
    assert authorization == "Bearer " + expected_key
    assert body["model"] == "explicit/model"
    assert body.get("provider") is expected_provider
    model, capacity_options = offline_capacity[-1]
    assert model == "explicit/model"
    assert capacity_options["base_url"] == gateway
    assert capacity_options["api_key"] == expected_key
    assert capacity_options["model_provider"] == ""
    sid = runner.state.meta.id
    await container.persist_session(runner)
    await container.aclose()

    settings.model = "changed/global"
    settings.gateway = "https://changed.example/v1"
    restored_container = AppContainer(data_root=data_root, settings=settings, config=AppContainerConfig(fake_mode=True))
    restored = await restored_container.load_session(sid)
    role = restored.state.character(character.id)
    assert role.llm.model == "explicit/model"
    assert role.llm.base_url == gateway
    assert role.llm.inherit_model is False and role.llm.inherit_base_url is False
    await restored_container.aclose()


def test_torn_utf8_receipt_does_not_erase_other_receipts_or_next_append(tmp_path):
    ledger = UsageLedger(tmp_path)
    first = UsageRecord(id="receipt-one", model="合成模型", usage=TokenUsage(input_tokens=11))
    ledger.append("synthetic-story", first)
    with ledger._path("synthetic-story").open("ab") as handle:
        handle.write(b'{"id":"torn","model":"\xe4')
    rows, damaged = ledger.read("synthetic-story")
    assert damaged and [row.id for row in rows] == [first.id]
    second = UsageRecord(id="receipt-two", usage=TokenUsage(output_tokens=3))
    ledger.append("synthetic-story", second)
    first.labels.append("continue")
    ledger.append("synthetic-story", first)
    rows, damaged = ledger.read("synthetic-story")
    assert damaged
    assert [row.id for row in rows] == [first.id, second.id]
    assert rows[0].labels == ["continue"]


@pytest.mark.asyncio
async def test_generation_labels_include_retry_receipts_and_survive_stale_snapshot(tmp_path):
    root = tmp_path / "data"
    container = AppContainer(data_root=root, config=AppContainerConfig(fake_mode=True))
    character = Character(card=CharacterCard(name="分类验证角色"))
    await container.save_character(character)
    runner = await container.create_session("分类验证", [character.id], "", [])
    ctx = TurnContext(session_id=runner.state.meta.id, character_id="guide", turn=1,
        generation_id="gen-target", operation_id="op-target", attempt_id="attempt-first")
    record_usage(runner, "generation", TokenUsage(input_tokens=10, output_tokens=3),
        model="main", character_id="guide", ctx=ctx)
    ctx.attempt_id = "attempt-retry"
    record_usage(runner, "generation", TokenUsage(input_tokens=7, output_tokens=4),
        model="main", character_id="guide", ctx=ctx)
    record_usage(runner, "generation", TokenUsage(input_tokens=100), model="other",
        ctx=ctx.model_copy(update={"generation_id": "gen-other"}))
    await container.persist_session(runner)
    # Labels are newer than the story snapshot, and target both paid attempts.
    label_main_call(runner, "proactive", generation_id="gen-target")
    label_main_call(runner, "proactive", generation_id="gen-target")
    assert runner.cost_report()["by_purpose"]["proactive"]["input_tokens"] == 17
    sid = runner.state.meta.id
    await container.aclose()
    restored_container = AppContainer(data_root=root, config=AppContainerConfig(fake_mode=True))
    restored = await restored_container.load_session(sid)
    assert restored.cost_report()["call_count"] == 3
    assert restored.cost_report()["total"]["input_tokens"] == 117
    assert restored.cost_report()["by_purpose"]["proactive"]["input_tokens"] == 17
    assert restored.state.usage_records[-1].labels == []
    await restored_container.aclose()


@pytest.mark.asyncio
async def test_group_length_reaches_message_metadata_without_double_charging(monkeypatch, offline_capacity):
    group = GroupActor(id="crowd", label="村民", scene_id="scene", joined_seq=0, public_brief="村民正在讨论。")
    scene = Scene(id="scene", title="广场", group_ids=[group.id])
    settings = AppSettings()
    state = SessionState(meta=SessionMeta(id="synthetic-group", title="合成群体",
        streaming_enabled=False, memory_enabled=False), groups=[group], scenes=[scene],
        active_scene_id=scene.id, messages=[Message(session_id="synthetic-group", seq=0, turn=1,
            actor="player", content="村民们，你们打算怎么办？", scene_id=scene.id)])
    class Archive:
        def write(self, *args, **kwargs):
            return "synthetic-request"

    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "村民正准备"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5}}

    monkeypatch.setattr("mrp.llm.httpx.post", lambda *args, **kwargs: Response())
    container = SimpleNamespace(settings=settings, fake_mode=False, memory_store=None,
        config=SimpleNamespace(api_key_env="SYNTHETIC_GROUP_NO_KEY"), request_archive=Archive())
    runner = SessionRunner(state, FakeEngine(), group_responder=GroupResponder(container), app_settings=settings)
    reply = await runner.turns.run_group_turn(group, 1)
    assert reply.generation_meta.finish_reason == "length"
    assert reply.generation_meta.completion_state == "truncated"
    assert reply.generation_meta.request_ids == ["synthetic-request"]
    assert runner.cost_report()["call_count"] == 1
    assert runner.cost_report()["total"]["input_tokens"] == 20
    assert runner.cost_report()["by_purpose"]["group_response"]["input_tokens"] == 20
    assert state.usage_records[0].generation_id == reply.generation_meta.generation_id
    await runner.aclose()


@pytest.mark.parametrize("all_empty,expected_calls", [(False, 3), (True, 4)])
@pytest.mark.asyncio
async def test_paid_empty_response_retries_are_individual_durable_receipts(
    tmp_path, offline_capacity, all_empty, expected_calls,
):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        text = "" if all_empty or len(captured) < 3 else "终于有可见回复。"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": text}, "finish_reason": "length" if not text else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 7},
        })

    data_root = tmp_path / "data"
    container = AppContainer(data_root=data_root, config=AppContainerConfig(fake_mode=True))
    container.engine_manager = EngineManager(engine_factory=lambda: OpenRouterEngine(
        thinking=True, client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler))))
    character = Character(card=CharacterCard(name="重试验证"))
    await container.save_character(character)
    runner = await container.create_session("合成计费重试", [character.id], "", [])
    runner.state.meta.streaming_enabled = False
    runner.state.meta.memory_enabled = False
    await container.persist_session(runner)
    role = runner.state.character(character.id)
    if all_empty:
        with pytest.raises(RuntimeError, match="模型返回空回复"):
            await runner.run_character_turn(role, 1)
    else:
        reply = await runner.run_character_turn(role, 1)
        assert reply.content == "终于有可见回复。"
        assert reply.generation_meta.usage.input_tokens == 30
    assert len(captured) == expected_calls
    assert runner.cost_report()["call_count"] == expected_calls
    assert runner.cost_report()["total"]["input_tokens"] == expected_calls * 10
    assert runner.cost_report()["total"]["output_tokens"] == expected_calls * 7
    generation_ids = {row.generation_id for row in runner.state.usage_records}
    assert len(generation_ids) == 1 and None not in generation_ids
    sid = runner.state.meta.id
    # No story save after generation: append-only receipts alone must survive.
    await container.aclose()
    restored_container = AppContainer(data_root=data_root, config=AppContainerConfig(fake_mode=True))
    restored = await restored_container.load_session(sid)
    assert restored.cost_report()["call_count"] == expected_calls
    assert restored.cost_report()["total"]["input_tokens"] == expected_calls * 10
    assert restored.cost_report()["total"]["output_tokens"] == expected_calls * 7
    await restored_container.aclose()
