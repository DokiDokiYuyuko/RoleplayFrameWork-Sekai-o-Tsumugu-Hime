"""工坊单测（R23/R29/R31）：角色卡/世界书生成、扩展、AI 改写、试聊预览。

全部 fake llm_call 替身（捕获 messages + 返回预置文本），无网络无 IO。
JSON 返回体分别用 ```json 围栏与裸格式覆盖（extract_json 兼容）。
"""
from __future__ import annotations

import json

import pytest

from mrp.shared.prompt import persona_from_card
from mrp.orchestrator.workshop import (
    ai_edit_field,
    extend_lorebook,
    generate_character_cards,
    generate_lorebook,
    persona_preview,
    preview_turn,
)
from mrp.shared.models import CharacterCard, Lorebook, LorebookEntry


class FakeLlm:
    """llm_call 替身：记录每次调用的 messages，返回预置回复。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def __call__(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.reply


def user_msg(fake: FakeLlm) -> str:
    assert fake.calls, "llm_call 未被调用"
    msgs = fake.calls[0]
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    return msgs[1]["content"]


CARDS = [
    {
        "name": "林澈",
        "description": "灯塔看守人，三十岁。",
        "personality": "合成性格：标记甲。",
        "scenario": "海边小镇的灯塔。",
        "first_mes": "（放下望远镜）今晚风大，你别站太靠边。",
        "mes_example": "{{user}}: 你好\n{{char}}: （点头）。",
        "aliases": ["阿澈", "小澈"],
    },
    {
        "name": "苏晚",
        "description": "镇上唯一诊所的医生。",
        "personality": "絮叨热心。",
        "scenario": "雾溪镇诊所。",
        "first_mes": "又来蹭体检？坐下吧。",
        "mes_example": "",
        "aliases": ["晚晚"],
    },
]
CARDS_JSON = json.dumps(CARDS, ensure_ascii=False)

LORE = {
    "entries": [
        {
            "keys": ["灯塔", "守塔人"],
            "content": "灯塔由初代守塔人建于百年前的风暴之夜，至今仍有人值守。",
            "constant": True,
            "anchor": "system",
            "order": 90,
        },
        {
            "keys": ["海雾"],
            "content": "每逢无月的夜晚，海雾会漫进小镇的街巷。",
            "anchor": "at_depth",
        },
        {
            "keys": ["旧渔市"],
            "content": "废弃的渔市如今只在退潮时露出地基。",
            "probability": 60,
        },
    ]
}
LORE_JSON = json.dumps(LORE, ensure_ascii=False)


# ---------------------------------------------------------------- 角色卡生成


def test_generate_character_cards_fenced():
    fake = FakeLlm(f"好的，以下是：\n```json\n{CARDS_JSON}\n```\n")
    book = Lorebook(
        name="雾溪世界观",
        entries=[
            LorebookEntry(uid=0, keys=["小镇"], content="甲" * 250),
        ],
    )
    result = generate_character_cards("要两个海边角色", "偏安静", [book], 2, fake)

    assert len(result) == 2
    card, aliases = result[0]
    assert isinstance(card, CharacterCard)
    assert card.name == "林澈"
    assert card.spec == "chara_card_v2"
    assert card.description == "灯塔看守人，三十岁。"
    assert card.personality == "合成性格：标记甲。"
    assert card.scenario == "海边小镇的灯塔。"
    assert card.first_mes.startswith("（放下望远镜）")
    assert card.mes_example == "{{user}}: 你好\n{{char}}: （点头）。"
    assert aliases == ["阿澈", "小澈"]
    assert result[1][0].name == "苏晚"
    assert result[1][1] == ["晚晚"]

    # 参考世界书进 prompt：书名 + content 前 200 字（截断）
    msg = user_msg(fake)
    assert "雾溪世界观" in msg
    assert "甲" * 200 in msg
    assert "甲" * 201 not in msg
    assert "要两个海边角色" in msg
    assert "偏安静" in msg


def test_generate_character_cards_count_and_bare_json():
    fake = FakeLlm(CARDS_JSON)  # 裸 JSON（无围栏），extract_json 兼容
    # 超 count 截断
    assert len(generate_character_cards("x", "", [], 1, fake)) == 1
    assert fake.calls[0][1]["role"] == "user"
    # 不足 count：返回实际数（无法凭空补齐）
    assert len(generate_character_cards("x", "", [], 5, fake)) == 2


# ---------------------------------------------------------------- 世界书生成


def test_generate_lorebook_fenced():
    ref = Lorebook(
        name="旧设定",
        entries=[
            LorebookEntry(uid=0, keys=["小镇"], content="小镇名叫雾溪镇，禁止入海。"),
        ],
    )
    fake = FakeLlm(f"```json\n{LORE_JSON}\n```")
    book = generate_lorebook("雾溪镇传说", ["灯塔", "海雾"], ref, 3, fake)

    assert book.name == "雾溪镇传说"
    assert book.source_format == "st"
    assert book.scan_depth == 2
    assert book.token_budget == 1024
    assert [e.uid for e in book.entries] == [0, 1, 2]

    e0, e1, e2 = book.entries
    assert e0.keys == ["灯塔", "守塔人"]
    assert e0.constant is True
    assert e0.anchor == "system"
    assert e0.order == 90
    assert e1.anchor == "at_depth"
    assert e1.constant is False
    assert e1.order == 100  # 缺省
    assert e1.probability == 100  # 缺省
    assert e2.probability == 60
    assert e2.anchor == "system"

    # 参考书进 prompt：内容 + 兼容性要求
    msg = user_msg(fake)
    assert "雾溪镇，禁止入海" in msg
    assert "不得与其矛盾" in msg
    assert "灯塔" in msg and "海雾" in msg  # concepts 进 prompt


def test_generate_lorebook_name_truncated_and_bare():
    fake = FakeLlm(LORE_JSON)  # 裸 JSON
    topic = "题" * 40
    book = generate_lorebook(topic, [], None, 3, fake)
    assert book.name == topic[:30]
    assert len(book.name) == 30
    assert len(book.entries) == 3


# ---------------------------------------------------------------- 世界书扩展


def test_extend_lorebook():
    existing = Lorebook(
        name="雾溪镇",
        entries=[
            LorebookEntry(uid=0, keys=["灯塔", "守塔人"], content="灯塔高三十米。"),
            LorebookEntry(uid=5, keys=["海雾"], content="海雾有毒。"),
        ],
    )
    extend = {
        "entries": [
            # keys 与 uid=0 条目完全重复（集合相等，顺序不同）→ 丢弃
            {"keys": ["守塔人", "灯塔"], "content": "守塔人世代住在灯塔底层。"},
            {"keys": ["沉船"], "content": "湾底沉着一艘百年前的商船。"},
            {"keys": ["灯塔节"], "content": "每年夏至小镇点亮灯塔。"},
        ]
    }
    fake = FakeLlm(json.dumps(extend, ensure_ascii=False))  # 裸 JSON
    new = extend_lorebook(existing, "补充节日与传说", 3, fake)

    assert len(new) == 2
    assert [e.uid for e in new] == [6, 7]  # max(现有 uid)=5 → 从 6 续号
    assert new[0].keys == ["沉船"]
    assert new[1].keys == ["灯塔节"]

    # 现有条目与方向进 prompt
    msg = user_msg(fake)
    assert "灯塔高三十米" in msg
    assert "海雾有毒" in msg
    assert "补充节日与传说" in msg


def test_extend_lorebook_fenced_and_truncate():
    existing = Lorebook(name="空书", entries=[])
    fake = FakeLlm(f"```json\n{LORE_JSON}\n```")
    new = extend_lorebook(existing, "从零开始", 2, fake)
    assert [e.uid for e in new] == [0, 1]  # 空书 → uid 从 0
    assert len(new) == 2  # 超 count 截断（fake 返回 3 条）


# ---------------------------------------------------------------- AI 改写


def test_ai_edit_field():
    card = CharacterCard(name="林澈", description="灯塔看守人。")
    fake = FakeLlm("年过五十的灯塔看守人，左腿有旧伤。")
    out = ai_edit_field(card, "description", "把年龄改大并加上腿部旧伤", fake)

    assert out == "年过五十的灯塔看守人，左腿有旧伤。"
    msg = user_msg(fake)
    assert "灯塔看守人。" in msg  # 当前值进 prompt
    assert "腿部旧伤" in msg  # 指令进 prompt
    assert card.description == "灯塔看守人。"  # 不改 card 本身


def test_ai_edit_field_invalid():
    card = CharacterCard(name="x")
    with pytest.raises(ValueError):
        ai_edit_field(card, "name", "改个名", FakeLlm(""))
    with pytest.raises(ValueError):
        ai_edit_field(card, "aliases", "加别名", FakeLlm(""))


# ---------------------------------------------------------------- 预览


def test_persona_preview():
    card = CharacterCard(
        name="林澈", description="守塔人", personality="寡言", scenario="海边"
    )
    pv = persona_preview(card)
    assert pv["persona"] == persona_from_card(card)
    assert pv["tokens"] > 0
    assert set(pv) == {"persona", "tokens"}


def test_preview_turn():
    card = CharacterCard(name="林澈", description="灯塔看守人。")
    fake = FakeLlm("（望向海面）今晚风大。")
    out = preview_turn(card, "晚上好", fake)

    assert out == "（望向海面）今晚风大。"
    msgs = fake.calls[0]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == persona_from_card(card)  # system 含 persona
    assert msgs[1] == {"role": "user", "content": "晚上好"}
