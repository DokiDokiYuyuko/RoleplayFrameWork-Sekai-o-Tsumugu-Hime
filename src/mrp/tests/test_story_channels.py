"""故事输入模式与显式通道的回归测试。"""
from __future__ import annotations

import pytest

from mrp.orchestrator.channels import parse_channels
from mrp.orchestrator.director_llm import FakeDirectorJudge
from mrp.tests.test_integration import make_runner


def test_channel_parser_defaults_and_explicit_markers():
    text = "推开门（内心：先观察）又走进大厅（旁白：灯忽然熄灭）"
    assert parse_channels(text) == [
        ("roleplay", "推开门"), ("inner", "先观察"),
        ("roleplay", "又走进大厅"), ("scene", "灯忽然熄灭"),
    ]
    assert parse_channels(text, default_kind="scene") == [
        ("scene", "推开门"), ("inner", "先观察"),
        ("scene", "又走进大厅"), ("scene", "灯忽然熄灭"),
    ]


@pytest.mark.parametrize("mode,channel,public_kind", [
    ("角色聊天", "dialogue", "roleplay"),
    ("冒险行动", "dialogue", "roleplay"),
    ("故事写作", "narration", "scene"),
])
async def test_modes_split_inner_and_public_content(mode, channel, public_kind):
    runner, _ = make_runner(replies=["我听见了。"])
    judge = FakeDirectorJudge(mode="pick")
    runner.director_judge = judge
    created = await runner.player_say("公开开头（内心：秘密想法）（旁白：风吹过）", channel=channel)
    player = [message for message in created if message.actor == "player"]
    assert [(message.kind, message.content) for message in player] == [
        (public_kind, "公开开头"), ("inner", "秘密想法"), ("scene", "风吹过"),
    ], mode
    assert player[1].visible_to == ["player"]
    assert player[0].visible_to != ["player"] and player[2].visible_to != ["player"]
    assert judge.calls, mode
    assert "秘密想法" not in judge.calls[-1].player_text
    assert "秘密想法" not in " ".join(judge.calls[-1].recent_messages)


@pytest.mark.parametrize("channel", ["dialogue", "narration"])
async def test_pure_marked_inner_does_not_trigger_reply(channel):
    runner, _ = make_runner(replies=["不该回复"])
    judge = FakeDirectorJudge(mode="pick")
    runner.director_judge = judge
    created = await runner.player_say("（内心：别让他们知道）", channel=channel)
    assert len(created) == 1
    assert created[0].kind == "inner" and created[0].visible_to == ["player"]
    assert not judge.calls
    assert not any(message.actor != "player" for message in runner.state.messages)


async def test_reply_plan_attaches_to_last_public_player_part():
    runner, _ = make_runner(replies=["甲回应", "乙回应"])
    created = await runner.player_say_with_reply_mode(
        "公开内容（内心：最后的秘密）", mentions=["char-a", "char-b"],
        channel="dialogue", reply_mode="serial",
    )
    public, private = [message for message in created if message.actor == "player"]
    assert public.executed_reply_mode == "serial"
    assert private.executed_reply_mode is None
