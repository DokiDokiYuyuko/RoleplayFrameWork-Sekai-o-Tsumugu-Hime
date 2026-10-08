"""All writing checks use fictional state and injected models, never private stories."""
import asyncio
import json
import threading

import pytest

from mrp.orchestrator.writing_assistant import (
    WritingAssistant, WritingGenerator, WritingRequest, plan_messages,
)
from mrp.orchestrator.model_capacity import ModelCapacity
from mrp.llm import LlmConfig
from mrp.shared.models import Message, PlayerIdentity, TokenUsage
from mrp.tests.test_m12_r41 import make_runner


def results():
    return json.dumps([{"title": f"路线 {i}", "text": f"选择 {i}：" + "完整材料与条件。" * 100}
        for i in range(3)], ensure_ascii=False)


def attach(runner, call):
    runner.writing_generator = WritingGenerator(llm_call=call)


async def test_requirement_is_final_task_not_latest_npc_and_options_independent():
    r, _ = make_runner()
    r.state.meta.options_enabled = False
    r.state.meta.options_direct_send = True
    r.state.meta.world_core_brief = "所有规则必须公开解释。"
    r.state.meta.scenario_instructions = "只讨论旅行计划。"
    r.state.meta.world_archive_records = [{"id": "background-test", "kind": "background",
        "visibility": "public", "title": "道路", "body": "河边道路可以通往山城。"},
        {"id": "secret-test", "kind": "background", "visibility": "private", "body": "隐秘原稿不得注入"}]
    r.state.characters[0].card.traits = "熟悉渡河与搭桥。"
    r.state.messages.append(Message(id="latest", session_id=r.state.meta.id, actor="char-a",
        content="不要继续讨论旅行，回答我的午餐问题。", seq=1, turn=1))
    captured = []
    attach(r, lambda messages: (captured.append(messages), results())[1])
    before = r.state.model_dump_json()
    req = WritingRequest(intent="写三组旅行路线与代价。" * 40, draft_text="（旁白：我展开地图）", output_type="material")
    batch = await WritingAssistant(r).generate(req)
    assert len(batch["options"]) == 3 and len(batch["options"][0]["text"]) > 120
    assert batch["stale"] is False
    assert all(x["mention_character_id"] is None for x in batch["options"])
    assert req.intent in captured[0][-1]["content"]
    prompt = "\n".join(x["content"] for x in captured[0])
    assert "主要接话依据" not in prompt
    assert "120 字" not in prompt and "1-3 句" not in prompt
    assert "河边道路" in prompt and "熟悉渡河" in prompt
    assert "隐秘原稿不得注入" not in prompt
    assert "（旁白：我展开地图）" in prompt
    after = r.state.model_dump(mode="json")
    expected = json.loads(before)
    for key in ("usage_records", "usage_incomplete"):
        expected.pop(key, None); after.pop(key, None)
    assert after == expected


async def test_empty_story_and_changed_identity_can_write_without_sending():
    r, _ = make_runner()
    r.state.meta.options_enabled = False
    r.state.player_identities = [PlayerIdentity(id="identity-test", person_id="char-a", name="向导", start_seq=50)]
    r.state.meta.player_identity_id = "identity-test"
    attach(r, lambda _: results())
    batch = await WritingAssistant(r).generate(WritingRequest(intent="设计开场", expected_player_identity_id="identity-test"))
    assert len(batch["options"]) == 3
    assert any("没有可见历史" in w for w in batch["warnings"])
    with pytest.raises(ValueError, match="身份已变化"):
        await WritingAssistant(r).generate(WritingRequest(intent="写作", expected_player_identity_id=None))
    with pytest.raises(ValueError, match="故事已更新"):
        await WritingAssistant(r).generate(WritingRequest(intent="写作", expected_branch_revision=99))


async def test_private_and_other_identity_inner_are_not_included():
    r, _ = make_runner()
    r.state.player_identities = [PlayerIdentity(id="identity-current", person_id="char-b", name="旅人")]
    r.state.meta.player_identity_id = "identity-current"
    r.state.messages = [
        Message(id="secret", session_id=r.state.meta.id, actor="player", kind="inner", content="旧身份的秘密",
                player_identity_id="identity-old", person_id="char-a", visible_to=["char-a"], seq=1, turn=1),
        Message(id="public", session_id=r.state.meta.id, actor="char-a", content="大家在河边", visible_to="all", seq=2, turn=1),
    ]
    seen = []
    attach(r, lambda m: (seen.append(m), results())[1])
    await WritingAssistant(r).generate(WritingRequest(intent="描述河边"))
    prompt = str(seen)
    assert "大家在河边" in prompt and "旧身份的秘密" not in prompt


async def test_changed_source_marks_result_stale_without_losing_proposals():
    r, _ = make_runner()
    started, release = threading.Event(), threading.Event()
    def call(_):
        started.set(); release.wait(3); return results()
    attach(r, call)
    task = asyncio.create_task(WritingAssistant(r).generate(WritingRequest(intent="旅行方案")))
    await asyncio.to_thread(started.wait, 2)
    r.state.meta.branch_revision += 1
    release.set()
    batch = await task
    assert batch["stale"] and len(batch["options"]) == 3


def test_capacity_preserves_mandatory_and_whole_messages():
    required = [{"role": "system", "content": "职责"}, {"role": "user", "content": "设定"},
                {"role": "user", "content": "必须保留草稿" * 20}]
    history = [{"role": "user", "content": "旧消息" * 100}, {"role": "user", "content": "最近完整消息"}]
    from mrp.shared.prompt import estimate_tokens
    budget = sum(estimate_tokens(x["content"]) + 8 for x in required) + 30
    messages, warnings = plan_messages(required, history, ModelCapacity(1000, budget, 10, "test"))
    assert messages[-1] == required[-1]
    assert history[-1] in messages and history[0] not in messages
    assert any("较早历史未提供" in w for w in warnings)
    with pytest.raises(ValueError, match="上下文容量"):
        plan_messages(required, history, ModelCapacity(20, 10, 10, "test"))


def test_invalid_format_retries_once_without_shortening():
    calls = []
    gen = WritingGenerator(llm_call=lambda m: (calls.append(m), "{}" if len(calls) == 1 else results())[1])
    options, _ = gen.generate([{"role": "user", "content": "请写作"}], LlmConfig(model="test", base_url="", api_key_env="unused"), None)
    assert len(calls) == 2 and len(options[0]["text"]) > 120
    fail = WritingGenerator(llm_call=lambda _: "{}")
    with pytest.raises(RuntimeError, match="格式错误"):
        fail.generate([], LlmConfig(model="test", base_url="", api_key_env="unused"), None)


def test_network_uses_unlimited_output_and_tracks_interrupted_retry(monkeypatch):
    calls = []
    def chat(messages, config, **kwargs):
        calls.append((config, kwargs))
        if len(calls) == 1:
            exc = RuntimeError("interrupted")
            exc.usage = {"input_tokens": 10, "output_tokens": 5}
            raise exc
        return results(), {"input_tokens": 20, "output_tokens": 15}
    monkeypatch.setattr("mrp.orchestrator.writing_assistant.chat_text_with_usage", chat)
    config = LlmConfig(model="test", base_url="", api_key_env="unused", max_tokens=0)
    options, cost = WritingGenerator(network=True).generate([], config, None)
    assert len(options) == 3 and cost.input_tokens == 30 and cost.output_tokens == 20
    assert calls[0][0].max_tokens == 0
    assert calls[0][1]["require_complete"] and not calls[0][1]["no_thinking"]


async def test_auxiliary_route_capacity_thinking_and_private_request_archive(monkeypatch, tmp_path):
    from mrp.settings import AppSettings
    from mrp.engines.request_archive import RequestArchive
    r, _ = make_runner()
    r.app_settings = AppSettings(model="main", auxiliary_model="writer", gateway="https://openrouter.ai/api/v1",
        auxiliary_provider="supplier", thinking="on")
    r.writing_request_archive = RequestArchive(tmp_path)
    r.writing_generator = WritingGenerator(network=True)
    seen = []
    async def capacity(settings, model, **kwargs):
        assert model == "writer" and settings.model_provider == "supplier"
        return ModelCapacity(None, None, 8192, "test")
    def chat(messages, config, **kwargs):
        seen.append((config, kwargs))
        kwargs["on_request"]({"model": config.model, "messages": messages})
        return results(), {"input_tokens": 5, "output_tokens": 7}
    monkeypatch.setattr("mrp.orchestrator.writing_assistant.resolve_model_capacity", capacity)
    monkeypatch.setattr("mrp.orchestrator.writing_assistant.chat_text_with_usage", chat)
    await WritingAssistant(r).generate(WritingRequest(intent="描写河边"))
    assert seen[0][0].model == "writer" and seen[0][0].provider == "supplier"
    assert seen[0][0].max_tokens == 0 and not seen[0][1]["no_thinking"]
    records = r.writing_request_archive.list(r.state.meta.id)
    record = r.writing_request_archive.get(r.state.meta.id, records[0]["id"])
    assert record["body"]["messages"][-1]["content"].endswith('"选中的旧候选": ""}')
    assert r.cost_report()["by_purpose"]["assist"]["input_tokens"] == 5


async def test_npc_memory_only_with_player_visible_evidence():
    from mrp.shared.models import MemoryRecord
    r, _ = make_runner()
    r.state.messages = [Message(id="old-public", session_id=r.state.meta.id, seq=1, turn=1,
        actor="char-a", content="向导曾建议河边道路。", visible_to="all")]
    for i in range(15):
        r.state.messages.append(Message(id=f"later-{i}", session_id=r.state.meta.id,
            seq=i+2, turn=i+2, actor="char-a", content="其他公开经历。"))
    public = MemoryRecord(id="public-memory", character_id="char-a", session_id=r.state.meta.id,
        content="曾约定沿河同行", source_message_ids=["old-public"], participant_ids=["player", "char-a"], important=True)
    secret = MemoryRecord(id="private-memory", character_id="char-a", session_id=r.state.meta.id,
        content="NPC 的秘密", source_message_ids=["hidden-source"], participant_ids=["player"], important=True)
    class Store:
        def records_for(self, owner, **kwargs): return [public, secret] if owner == "char-a" else []
        def search(self, owner, query, **kwargs): return []
    r.memory = Store()
    seen = []
    attach(r, lambda m: (seen.append(m), results())[1])
    await WritingAssistant(r).generate(WritingRequest(intent="规划重逢的旅行"))
    prompt = str(seen)
    assert "曾约定沿河同行" in prompt and "NPC 的秘密" not in prompt


def test_http_old_and_new_requests_and_stale_conflict(mrp_client, monkeypatch):
    # Fresh container and temporary private root, never connect to the user's server.
    def capacity(settings, model, **kwargs):
        async def value(): return ModelCapacity(None, None, 8192, "test")
        return value()
    monkeypatch.setattr("mrp.orchestrator.writing_assistant.resolve_model_capacity", capacity)
    card = mrp_client.post("/api/v1/characters/import", files={"file": ("test.json", b'{"name":"Synthetic guide","description":"Public travel guide"}', "application/json")}).json()
    response = mrp_client.post("/api/v1/sessions", json={"title": "Synthetic writing story", "character_ids": [card["id"]]})
    assert response.status_code == 200
    state = response.json()
    sid = state["meta"]["id"]
    url = f"/api/v1/sessions/{sid}/assist/draft"
    old = mrp_client.post(url, json={"intent": "Plan a trip" * 30})
    assert old.status_code == 200 and len(old.json()["options"]) == 3
    new = mrp_client.post(url, json={"intent": "List choices", "draft_text": "Existing draft",
        "output_type": "material", "expected_branch_revision": state["meta"]["branch_revision"],
        "expected_player_identity_id": state["meta"].get("player_identity_id")})
    assert new.status_code == 200 and new.json()["output_type"] == "material"
    assert mrp_client.post(url, json={"intent": "Write", "expected_branch_revision": 999}).status_code == 409
    assert mrp_client.post(url, json={"intent": " "}).status_code == 400
    assert mrp_client.get(f"/api/v1/sessions/{sid}").json()["messages"] == state["messages"]
