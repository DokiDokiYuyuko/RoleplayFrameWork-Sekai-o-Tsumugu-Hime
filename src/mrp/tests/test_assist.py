"""M12-R41/R39 辅助候选生成器单测——全部注入式 fake llm_call，不碰网络。

覆盖：三种上下文（open/scene/turn）的 prompt 材料与措辞、风格指令、
解析硬约束（长度/mention/kind/去重/上限）、失败静默、数组提取（围栏/裸 JSON/非数组）、
假生成器、开场白预算。
"""
from __future__ import annotations

import json

from mrp.orchestrator.assist import (
    MAX_TEXT_LEN,
    AssistCharacter,
    AssistGenerator,
    AssistMaterial,
    FakeAssistGenerator,
    extract_option_array,
    opening_lines_with_budget,
)
from mrp.orchestrator.session import parse_channels


def _material(**kw) -> AssistMaterial:
    defaults: dict = dict(
        kind="open",
        scene_id="sc-1",
        event_turn=1,
        persona="合成测试玩家的人设标记。",
        characters=[
            AssistCharacter(id="char-a", name="测试甲", note="合成角色甲标记", aliases=["测试甲", "测试别名甲二"]),
            AssistCharacter(id="char-b", name="测试乙", note="合成角色乙标记", aliases=["测试乙"]),
        ],
        opening_lines=["测试甲：合成开场标记甲。", "测试乙：合成开场标记乙。"],
        scene_title="合成测试场所",
        recent_lines=["测试甲: 合成开场标记甲。", "测试乙: 合成开场标记乙。"],
    )
    defaults.update(kw)
    return AssistMaterial(**defaults)


def _capture(payload: str = "[]"):
    captured: dict = {}

    def call(messages):
        captured["messages"] = messages
        return payload

    return captured, call


# ---------- prompt 材料 ----------


def test_prompt_contains_persona_roster_and_first_mes():
    captured, call = _capture()
    AssistGenerator(llm_call=call).generate(_material())
    user = captured["messages"][1]["content"]
    system = captured["messages"][0]["content"]
    assert "合成测试玩家" in user  # persona
    assert "- 测试甲" in user and "- 测试乙" in user  # 在场名单
    assert "测试甲：合成开场标记甲。" in user  # first_mes 原文
    assert "合成测试场所" in user  # 场景卡
    assert "开场" in system and "接住具体元素" in system


def test_prompt_scene_material_includes_transition_and_samples():
    captured, call = _capture()
    AssistGenerator(llm_call=call).generate(
        _material(
            kind="scene",
            opening_lines=[],
            transition="（夜风灌进楼道，灯光暗了一格。）",
            player_samples=["我先去趟道具间。"],
        )
    )
    user = captured["messages"][1]["content"]
    system = captured["messages"][0]["content"]
    assert "过渡描写：（夜风灌进楼道" in user
    assert "玩家此前发言" in user and "道具间" in user
    assert "开场白" not in user  # scene 型不塞开场白
    assert "场景切换" in system


def test_prompt_turn_material_and_style():
    captured, call = _capture()
    AssistGenerator(llm_call=call).generate(
        _material(kind="turn", opening_lines=[], style="dialogue")
    )
    system = captured["messages"][0]["content"]
    user = captured["messages"][1]["content"]
    assert "正在与角色对话" in system
    assert "接住话头" in system
    assert "偏对话与追问" in system  # 会话级风格进入 prompt
    assert "开场白" not in user


def test_prompt_draft_includes_intent_and_expansion_rules():
    """R40 代笔：意图进 prompt，且多样性轴换成"同一意思的三种表达方式"。"""
    captured, call = _capture()
    AssistGenerator(llm_call=call).generate(
        _material(kind="draft", intent="拒绝她的邀请", opening_lines=[])
    )
    system = captured["messages"][0]["content"]
    user = captured["messages"][1]["content"]
    assert "玩家写下的意思（需要扩写）：拒绝她的邀请" in user
    assert "三条都必须表达玩家写下的那个意思" in system
    assert "三种表达方式各写一条" in system
    assert "直球表达" in system and "带着动作说" in system and "含蓄一点说" in system
    assert "三种姿态各写一条" not in system  # 不混用开场/接话的姿态话术


def test_opening_lines_budget_prefers_latest():
    items = [(f"角色{i}", "长" * 400) for i in range(6)]
    lines = opening_lines_with_budget(items)
    assert lines[-1].startswith("角色5：")  # 最后一条无条件保留
    assert len(lines) < 6  # 预算截断（较早条目被丢）
    assert all(len(line) <= len("角色0：") + 400 for line in lines)


# ---------- 解析硬约束 ----------


def test_parse_mention_aliases_only_present_and_kind_normalized():
    raw = json.dumps(
        [
            {"text": "（看向测试甲）你来啦。", "mention": "测试别名甲二", "kind": "hook"},
            {"text": "我说两句吧。", "mention": "路人甲", "kind": "weird"},
            {"text": "转身就走。", "mention": 123},
        ]
    )
    choices = AssistGenerator(llm_call=lambda m: raw).generate(_material())
    assert choices[0].mention_character_id == "char-a"  # 别名解析
    assert choices[0].kind == "hook"
    assert choices[1].mention_character_id is None  # 不在场 → 不路由
    assert choices[1].kind == ""  # 非法 kind 归一
    assert choices[2].mention_character_id is None  # 非字符串 mention 置 None


def test_parse_drops_overlength_dedupes_and_caps():
    raw = json.dumps(
        [
            {"text": "字" * (MAX_TEXT_LEN + 1)},
            {"text": "重复"},
            {"text": "重复"},
            {"text": "第三条"},
            {"text": "第四条"},
            {"text": "第五条"},
        ]
    )
    choices = AssistGenerator(llm_call=lambda m: raw).generate(_material())
    assert [c.text for c in choices] == ["重复", "第三条", "第四条"]  # 超长丢/去重/上限 3


def test_candidates_parse_channels_lossless():
    raw = json.dumps([{"text": "（内心：先看看。）你好，我是合成测试玩家。", "kind": "soft"}])
    choices = AssistGenerator(llm_call=lambda m: raw).generate(_material())
    parts = parse_channels(choices[0].text)
    assert ("inner", "先看看。") in parts
    assert ("roleplay", "你好，我是合成测试玩家。") in parts


def test_generation_failure_returns_empty():
    def boom(messages):
        raise RuntimeError("网关 500")

    assert AssistGenerator(llm_call=boom).generate(_material()) == []
    assert AssistGenerator(llm_call=lambda m: "这不是 JSON").generate(_material()) == []
    assert AssistGenerator(llm_call=lambda m: "[]").generate(_material()) == []
    assert AssistGenerator().generate(_material()) == []  # 未配置 → 跳过


def test_fake_generator_deterministic_three_postures():
    choices = FakeAssistGenerator().generate(_material())
    assert len(choices) == 3
    assert choices[0].mention_character_id == "char-a"  # @ 在场首位
    assert {c.kind for c in choices} == {"hook", "push", "soft"}
    assert FakeAssistGenerator().generate(_material(kind="scene", characters=[]))[0].mention_character_id is None


# ---------- 数组提取（原 options.py 覆盖迁移） ----------


def test_extract_fenced_and_bare_and_prose_wrapped():
    assert extract_option_array('```json\n[{"text": "A"}]\n```') == [{"text": "A"}]
    assert extract_option_array('好的：\n[{"text": "A"},{"text": "B"}]\n希望有帮助') == [
        {"text": "A"},
        {"text": "B"},
    ]
    # 裸数组不得被截成首个 {...} 字典
    assert extract_option_array('[{"a":1},{"b":2}]') == [{"a": 1}, {"b": 2}]


def test_extract_non_array_raises():
    import pytest

    with pytest.raises(ValueError):
        extract_option_array('{"text": "追问", "mention": null}')
    with pytest.raises(ValueError):
        extract_option_array("今天天气不错，不输出 JSON。")
