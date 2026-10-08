"""Synthetic import behaviors are verified at execution and outgoing request boundaries."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from mrp.engines.openrouter import OpenRouterEngine
from mrp.importers.bundle import build_bundle, scan_bundle
from mrp.importers.character_card import import_card_json
from mrp.importers.exporters import lorebook_to_st_dict
from mrp.importers.lorebook import import_lorebook, import_lorebook_risu
from mrp.orchestrator.context_plan import plan_context
from mrp.orchestrator.lorebook import LorebookEngine
from mrp.shared.models import Character, CharacterCard, Lorebook, LorebookEntry, Message, TurnContext
from mrp.shared.prompt import BASE_ROLE_INSTRUCTION, estimate_tokens, persona_from_card
from mrp.storage.prompt_presets import preview_import


@pytest.mark.parametrize("logic,secondary,expected", [
    (0, "银钥匙", True), (0, "", False),
    (1, "银钥匙", True), (1, "银钥匙 戒指", False),
    (2, "", True), (2, "银钥匙", False),
    (3, "银钥匙 戒指", True), (3, "银钥匙", False),
])
def test_selective_logic_single_file_and_bundle_roundtrip(logic, secondary, expected):
    original = Lorebook(name="合成世界", description="仅用于测试", scan_depth=7,
        token_budget=1777, recursive_scanning=False, entries=[LorebookEntry(uid=31,
            keys=["城门"], secondary_keys=["银钥匙", "戒指"], selective=True,
            selective_logic=logic, content="守卫核验物品。")])
    single = import_lorebook(lorebook_to_st_dict(original))
    package = next(item.lorebook for item in scan_bundle(build_bundle([], [original])) if item.lorebook)
    for restored in (single, package):
        assert (restored.name, restored.description, restored.scan_depth, restored.token_budget,
                restored.recursive_scanning) == ("合成世界", "仅用于测试", 7, 1777, False)
        assert restored.entries[0].selective_logic == logic
        messages = [Message(session_id="synthetic", actor="player", seq=1, turn=1,
                            content=f"城门 {secondary}")]
        assert bool(LorebookEngine.scan(restored, messages)) is expected


@pytest.mark.parametrize("field,value", [("sticky", 2), ("cooldown", 4), ("delay", 10),
    ("delayUntilRecursion", True), ("characterFilter", {"names": ["甲"], "isExclude": False}),
    ("role", 1)])
def test_unexecuted_rules_are_retained_and_do_not_reveal_early(field, value):
    raw = {"uid": 1, "constant": True, "content": "十轮之后才揭示的合成秘密", field: value}
    book = import_lorebook({"entries": {"1": raw}})
    entry = book.entries[0]
    assert entry.enabled is False
    assert entry.extensions["mrp.import_compatibility"]["original_entry"] == raw
    assert book.import_report["unsupported"]
    for turn in range(1, 11):
        assert LorebookEngine.scan(book, [Message(session_id="s", actor="player", seq=turn,
                                                 turn=turn, content="继续")]) == []


@pytest.mark.parametrize("directive", ["activate_only_after 10", "exclude_keys 禁止", "exclude_keys_all 隐藏,秘密", "role user"])
def test_risu_directives_never_become_ordinary_prose_or_activate_in_first_ten_turns(directive):
    content = f"@@{directive}\n合成秘密"
    book = import_lorebook_risu({"entries": [{"id": 7, "insertorder": 1,
                                           "alwaysActive": True, "content": content}]})
    assert book.entries[0].content == content
    assert book.import_report["unsupported"]
    for turn in range(1, 11):
        assert LorebookEngine.scan(book, [Message(session_id="s", actor="player", seq=turn,
                                                 turn=turn, content="继续")]) == []


def test_disabled_and_inactive_st_presets_keep_state_and_order_and_report_roles():
    obj = {"name": "合成方案", "prompts": [
        {"identifier": "later", "name": "后段", "content": "乙", "role": "system"},
        {"identifier": "off", "name": "关闭", "content": "不能发送", "role": "system"},
        {"identifier": "first", "name": "前段", "content": "甲", "role": "system"},
        {"identifier": "absent", "name": "未列入", "content": "不能发送"},
        {"identifier": "assistant", "name": "助手身份", "content": "禁止伪装", "role": "assistant"},
        {"identifier": "depth", "name": "深度", "content": "深度片段", "role": "system",
         "injection_position": 1, "injection_depth": 3, "injection_order": 117},
        {"identifier": "macro", "name": "未知宏", "content": "{{setvar::secret}}"},
        {"identifier": "marker", "name": "动态历史", "marker": True}],
        "prompt_order": [{"character_id": 100000, "order": [
            {"identifier": "first", "enabled": True}, {"identifier": "off", "enabled": False},
            {"identifier": "later", "enabled": True}, {"identifier": "assistant", "enabled": True},
            {"identifier": "depth", "enabled": True}, {"identifier": "macro", "enabled": True},
            {"identifier": "marker", "enabled": True}]}]}
    result = preview_import(json.dumps(obj).encode())
    rows = {item["name"]: item for item in result["draft"]["segments"]}
    assert rows["前段"]["order"] < rows["后段"]["order"]
    assert rows["深度"]["anchor"] == "at_depth" and rows["深度"]["depth"] == 3
    assert rows["深度"]["order"] == 117
    for name in ("关闭", "未列入", "助手身份", "未知宏", "动态历史"):
        assert rows[name]["enabled"] is False
    assert any("assistant" in row for row in result["unsupported"])
    assert result["draft"]["extensions"]["foreign_fields"] == obj


async def test_examples_and_current_identity_macros_reach_final_request_and_capacity(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-key")
    card = import_card_json({"name": "守塔人", "description": "{{char}}认识{{user}}。",
        "system_prompt": "{{original}}\n称呼 {{user}}。",
        "mes_example": "<START>\n{{user}}: 合成示例问题\n{{char}}: 保持这种简洁口吻。"})
    character = Character(card=card)
    ctx = TurnContext(session_id="synthetic", character_id=character.id, turn=1,
                      player_identity_source={"name": "当前旅行者", "person_id": "synthetic-player"})
    persona = persona_from_card(card, "当前旅行者")
    plan = plan_context(ctx, model=character.llm.model, persona=persona)
    assert plan.estimated_input_tokens >= estimate_tokens(persona)
    assert estimate_tokens(persona) > estimate_tokens(persona_from_card(CharacterCard(name="守塔人")))
    ctx.planned_prompt = plan.prompt
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "合成回应"}, "finish_reason": "stop"}]})
    engine = OpenRouterEngine(client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    await engine.start(character)
    await engine.generate(ctx)
    final = seen[0]["messages"][0]["content"]
    assert "守塔人认识当前旅行者" in final
    assert "当前旅行者: 合成示例问题" in final
    assert "非剧情事实" in final and BASE_ROLE_INSTRUCTION in final
    assert "{{" not in final
    ctx.context_limit = estimate_tokens(persona) - 1
    with pytest.raises(ValueError, match="最终请求"):
        await engine.generate(ctx)
    assert len(seen) == 1
    await engine.stop()


async def test_dsh_identity_changes_refresh_system_prompt_without_real_harness(monkeypatch, tmp_path):
    from mrp.engines.dsh.engine import DshEngine
    seen = []
    class Harness:
        def __init__(self, config):
            seen.append(config.env["DSH_SYSTEM_PROMPT"])
        def start(self): pass
        def close(self): pass
        def run(self, text, session_id):
            return SimpleNamespace(final_response="合成回应", finish_reason="stop", events=[])
    monkeypatch.setattr("mrp.engines.dsh.engine.DeepSeekHarness", Harness)
    engine = DshEngine(engines_root=tmp_path)
    card = CharacterCard(name="守塔人", mes_example="{{user}}: 测试\n{{char}}: 您好")
    character = Character(card=card)
    await engine.start(character)
    for name in ("第一旅人", "第二旅人"):
        await engine.generate(TurnContext(session_id="s", character_id=character.id, turn=1,
            player_identity_source={"name": name}))
    assert "第一旅人: 测试" in seen[-2]
    assert "第二旅人: 测试" in seen[-1]
    await engine.stop()


def test_read_only_preflight_explains_embedded_rules_without_creating_assets(mrp_client):
    card = {"name": "合成卡", "mes_example": "{{char}}: 简洁示例",
        "character_book": {"name": "合成内嵌书", "entries": [{"id": 1, "content": "合成秘密",
                         "constant": True, "extensions": {"delay": 10}}]}}
    result = mrp_client.post('/api/v1/characters/import-preview',
        files={"file": ("synthetic.json", json.dumps(card).encode(), "application/json")})
    assert result.status_code == 200
    assert any("内嵌世界书" in row and "delay" in row for row in result.json()["unsupported"])
    assert mrp_client.get('/api/v1/characters').json() == []
    assert mrp_client.get('/api/v1/lorebooks').json() == []
    book = {"entries": {"0": {"constant": True, "content": "合成秘密", "cooldown": 3}}}
    result = mrp_client.post('/api/v1/lorebooks/import-preview',
        files={"file": ("synthetic.json", json.dumps(book).encode(), "application/json")})
    assert result.status_code == 200
    assert result.json()["draft"]["entries"][0]["enabled"] is False
    assert "cooldown" in result.json()["unsupported"][0]
    assert mrp_client.get('/api/v1/lorebooks').json() == []
