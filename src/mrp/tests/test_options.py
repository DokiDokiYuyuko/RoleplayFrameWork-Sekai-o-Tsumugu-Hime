"""OptionsGenerator 单测（R27 选项生成）——全部 fake llm_call，不碰网络。

覆盖：正常解析与 mention 映射、别名匹配、匹配不到置 None、
补齐/截断/去重、风格进 system prompt、上下文窗口截取 8 条、
LLM 异常与解析失败降级、围栏/裸 JSON 两种返回、make_llm_call 包装。
"""
from __future__ import annotations

import pytest

from mrp.llm import LlmConfig
from mrp.orchestrator.options import OptionChoice, OptionsGenerator, make_llm_call
from mrp.shared.models import Character, CharacterCard, Message


class FakeLlm:
    """捕获 messages、按预设回复或抛错的测试替身。"""

    def __init__(self, reply: str | None = None, error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[list[dict[str, str]]] = []

    def __call__(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        assert self.reply is not None
        return self.reply


def make_character(cid: str, name: str, aliases: list[str] | None = None) -> Character:
    return Character(id=cid, card=CharacterCard(name=name), aliases=aliases or [])


def make_message(actor: str, content: str, seq: int, turn: int) -> Message:
    return Message(session_id="sess-1", seq=seq, turn=turn, actor=actor, content=content)


VALID_REPLY = """```json
[
  {"text": "追问测试甲灯的事", "mention": "测试甲"},
  {"text": "向测试乙打招呼", "mention": "测试乙"},
  {"text": "继续观察四周", "mention": null},
  {"text": "沉默片刻", "mention": null}
]
```"""


@pytest.fixture
def characters() -> list[Character]:
    return [
        make_character("char-synthetic-alpha", "测试甲", ["测试别名甲"]),
        make_character("char-synthetic-beta", "测试乙"),
    ]


@pytest.fixture
def messages() -> list[Message]:
    return [
        make_message("char-synthetic-alpha", "图书馆的灯忽然闪了一下。", 0, 0),
        make_message("player", "抬头看了看灯。", 1, 1),
        make_message("char-synthetic-alpha", "\"你也看到了？\"她压低声音。", 2, 1),
        make_message("char-synthetic-beta", "测试乙从书架后走出来。", 3, 2),
    ]


# ---------------------------------------------------------------- 正常生成


def test_normal_generation(characters: list[Character], messages: list[Message]):
    fake = FakeLlm(reply=VALID_REPLY)
    options = OptionsGenerator(fake).generate(messages, characters)

    assert [o.text for o in options] == [
        "追问测试甲灯的事",
        "向测试乙打招呼",
        "继续观察四周",
        "沉默片刻",
    ]
    # mention 名字 -> 角色 id 映射
    assert options[0].mention_character_id == "char-synthetic-alpha"
    assert options[1].mention_character_id == "char-synthetic-beta"
    # 被动项保留
    assert any(o.text in ("继续观察四周", "沉默片刻") for o in options)
    assert all(o.mention_character_id is None for o in options[2:])

    # user prompt：对话文本（名字查 card.name，玩家用"玩家"）+ 角色名单
    user = fake.calls[0][1]
    assert user["role"] == "user"
    assert "玩家: 抬头看了看灯。" in user["content"]
    assert "测试甲: 图书馆的灯忽然闪了一下。" in user["content"]
    assert "char-synthetic-alpha（测试甲）" in user["content"]
    assert "char-synthetic-beta（测试乙）" in user["content"]


def test_alias_mention(characters: list[Character], messages: list[Message]):
    fake = FakeLlm(
        reply='[{"text": "递给测试别名甲一本书", "mention": "测试别名甲"},'
        ' {"text": "继续观察", "mention": null},'
        ' {"text": "沉默片刻", "mention": null}]'
    )
    options = OptionsGenerator(fake).generate(messages, characters)
    assert options[0].mention_character_id == "char-synthetic-alpha"  # 别名精确匹配


def test_mention_unmatched(characters: list[Character], messages: list[Message]):
    fake = FakeLlm(
        reply='[{"text": "向陌生人提问", "mention": "路人甲"},'
        ' {"text": "继续观察", "mention": null},'
        ' {"text": "转身离开图书馆", "mention": 123}]'  # 非字符串也置 None
    )
    options = OptionsGenerator(fake).generate(messages, characters)
    assert len(options) == 3
    assert all(o.mention_character_id is None for o in options)


# ---------------------------------------------------------------- 补齐/截断/去重


def test_pad_when_fewer_than_three(characters: list[Character], messages: list[Message]):
    fake = FakeLlm(reply='[{"text": "翻开那本书", "mention": "测试甲"}]')
    options = OptionsGenerator(fake).generate(messages, characters)

    assert len(options) == 3
    assert options[0].text == "翻开那本书"
    assert options[0].mention_character_id == "char-synthetic-alpha"
    # 补的全是被动项，且不与已有重复
    padded = [o.text for o in options[1:]]
    assert all(t in ("继续观察", "沉默片刻", "环顾四周") for t in padded)
    assert "继续观察" in padded


def test_truncate_when_more_than_five(characters: list[Character], messages: list[Message]):
    fake = FakeLlm(
        reply='['
        '{"text": "选项一"}, {"text": "选项二"}, {"text": "选项三"},'
        '{"text": "选项四"}, {"text": "选项五"}, {"text": "选项六"}]'
    )
    options = OptionsGenerator(fake).generate(messages, characters)
    assert [o.text for o in options] == ["选项一", "选项二", "选项三", "选项四", "选项五"]


def test_dedupe_same_text(characters: list[Character], messages: list[Message]):
    fake = FakeLlm(
        reply='['
        '{"text": "继续观察", "mention": null},'
        '{"text": "继续观察", "mention": "测试甲"},'  # 重复 text，应被丢弃（保首个）
        '{"text": "敲门", "mention": null},'
        '{"text": "敲门", "mention": null},'
        '{"text": "离开", "mention": null}]'
    )
    options = OptionsGenerator(fake).generate(messages, characters)
    assert [o.text for o in options] == ["继续观察", "敲门", "离开"]
    assert options[0].mention_character_id is None  # 保的是首个（无 mention）


# ---------------------------------------------------------------- prompt 构造


def test_style_in_system_prompt(characters: list[Character], messages: list[Message]):
    fake = FakeLlm(reply=VALID_REPLY)
    gen = OptionsGenerator(fake)

    gen.generate(messages, characters, style="dialogue")
    system = fake.calls[0][0]
    assert system["role"] == "system"
    assert "偏对话与追问" in system["content"]

    gen.generate(messages, characters, style="action")
    assert "偏行动与场景互动" in fake.calls[1][0]["content"]

    gen.generate(messages, characters, style="mixed")
    assert "行动与对话混合" in fake.calls[2][0]["content"]


def test_context_window_last_eight(characters: list[Character]):
    msgs = [
        make_message("player" if i % 2 else "char-synthetic-alpha", f"内容{i}", i, i)
        for i in range(10)
    ]
    fake = FakeLlm(
        reply='[{"text": "选项一"}, {"text": "选项二"}, {"text": "选项三"}]'
    )
    OptionsGenerator(fake).generate(msgs, characters)
    content = fake.calls[0][1]["content"]
    assert "内容2" in content and "内容9" in content  # 最近 8 条进入
    assert "内容0" not in content and "内容1" not in content  # 更早的被截掉


# ---------------------------------------------------------------- 降级


def test_llm_failure_fallback(characters: list[Character], messages: list[Message]):
    fake = FakeLlm(error=RuntimeError("LLM 网关 500"))
    options = OptionsGenerator(fake).generate(messages, characters)

    assert [o.text for o in options] == ["继续观察", "主动发起话题", "暂时离开"]
    assert all(o.mention_character_id is None for o in options)


def test_unparseable_reply_fallback(characters: list[Character], messages: list[Message]):
    fake = FakeLlm(reply="今天天气不错，不输出 JSON。")
    options = OptionsGenerator(fake).generate(messages, characters)
    assert [o.text for o in options] == ["继续观察", "主动发起话题", "暂时离开"]


# ---------------------------------------------------------------- JSON 提取


def test_bare_json_with_prose(characters: list[Character], messages: list[Message]):
    # 裸 JSON 前后带解释文字，extract_json 应能提取（围栏格式已在 VALID_REPLY 覆盖）
    fake = FakeLlm(
        reply='好的，以下是选项：\n'
        '[{"text": "点头回应", "mention": "测试甲"},'
        ' {"text": "继续观察", "mention": null},'
        ' {"text": "沉默片刻", "mention": null}]\n'
        "希望对你有帮助。"
    )
    options = OptionsGenerator(fake).generate(messages, characters)
    assert len(options) == 3
    assert options[0].text == "点头回应"
    assert options[0].mention_character_id == "char-synthetic-alpha"


def test_non_array_reply_fallback(characters: list[Character], messages: list[Message]):
    # JSON 但不是数组 -> 解析失败 -> 降级
    fake = FakeLlm(reply='{"text": "追问", "mention": null}')
    options = OptionsGenerator(fake).generate(messages, characters)
    assert [o.text for o in options] == ["继续观察", "主动发起话题", "暂时离开"]


# ---------------------------------------------------------------- make_llm_call


def test_make_llm_call(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    def fake_chat_text(messages, config, **kw):
        captured["messages"] = messages
        captured["config"] = config
        return "ok"

    monkeypatch.setattr("mrp.llm.chat_text", fake_chat_text)
    config = LlmConfig(model="m", base_url="http://x", api_key_env="K")
    call = make_llm_call(config)

    assert call([{"role": "user", "content": "hi"}]) == "ok"
    assert captured["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["config"] is config
