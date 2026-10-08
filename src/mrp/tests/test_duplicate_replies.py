from __future__ import annotations

import pytest

from mrp.shared.reply_quality import is_duplicate_reply
from mrp.tests.test_integration import make_runner


OLD = (
    "（我把港口地图摊在桌上，指着东侧灯塔说）我们先沿着石桥走到旧仓库，"
    "在那里等巡逻队换班。屋外还在下雨，你记得拿上雨衣和提灯。"
    "仓库门边有两只木箱，箱子里的货单可以证明昨天到港的船来自北方。"
    "我已经问过码头的守卫，他们看见一辆蓝色马车在日落前离开。"
    "等你准备好，我们再去街口找车夫，他应该知道货物送到了哪里。"
    "记下这些线索，回来之后我们一起核对地图上的路线。）"
)
COPY = OLD.replace("（我把", "（我将", 1).removesuffix("）")
NEW = "你问的是山路，不是码头。暴雨已经冲断北侧的桥，我们改走南边的林间小道。"


@pytest.mark.parametrize("copy", [COPY, "\n" + OLD.replace("，", ", ").replace("。", ".\n")])
def test_long_copy_allows_minor_wording_and_format_changes(copy):
    assert is_duplicate_reply(copy, OLD)


def test_shared_topic_and_short_repeated_answers_are_allowed():
    assert not is_duplicate_reply("好的。", "好的。")
    assert not is_duplicate_reply(NEW, OLD)
    assert not is_duplicate_reply(OLD[:70] + NEW + OLD[70:], OLD)


async def test_edited_segmented_input_retries_copy_and_keeps_latest_context():
    runner, sink = make_runner(replies=[OLD, "这是编辑之前的回答。", COPY, NEW])
    contexts = []
    generate = runner.engines.generate

    async def record(character, context, **kwargs):
        contexts.append(context)
        return await generate(character, context, **kwargs)

    runner.engines.generate = record
    try:
        await runner.player_say("测试甲，告诉我港口路线。", mentions=["char-a"])
        created = await runner.player_say("测试甲，往哪里走？（旁白：雨已经停了。）", mentions=["char-a"])
        parts = [message for message in created if message.actor == "player"]
        updates = ["测试甲，北侧山路还能通行吗？", "暴雨冲断了桥，我们在山脚下。"]
        async with runner.runtime.turn_lock:
            await runner.message_ops.edit_input_group_locked(
                parts[0].id,
                [(part.id, part.fingerprint, text) for part, text in zip(parts, updates)],
                expected_branch_revision=runner.state.meta.branch_revision,
            )
        result = await runner.regenerate_turn(parts[-1].id)
        assert result[-1].content == NEW
        assert len(contexts) == 4  # initial, original, copy, one retry
        for context in contexts[-2:]:
            visible = {message.id: message.content for message in context.visible_messages}
            assert [visible[part.id] for part in parts] == updates
            assert created[-1].id not in visible
        assert "duplicate_reply_feedback" in result[-1].generation_meta.injected_entry_ids
        assert contexts[-1].planned_prompt.text.index("几乎照抄") > contexts[-1].planned_prompt.text.index("=== 对话记录 ===")
        assert result[-1].generation_meta.usage.input_tokens == 20
        assert result[-1].generation_meta.usage.output_tokens == 10
        finals = [payload["message"] for name, payload in sink.events if name == "message.final"]
        assert finals[-1]["content"] == NEW
        assert all(item["content"] != COPY for item in finals)
    finally:
        await runner.engines.shutdown_all()


async def test_second_copy_rejected_and_regenerate_restores_original_reply():
    runner, sink = make_runner(replies=[OLD, "原回复应该保留。", COPY, COPY])
    try:
        await runner.player_say("测试甲，讲讲港口路线。", mentions=["char-a"])
        created = await runner.player_say("测试甲，北边的桥还能走吗？", mentions=["char-a"])
        original = created[-1]
        with pytest.raises(RuntimeError, match="相同回复"):
            await runner.regenerate_turn(created[0].id)
        assert runner.state.messages[-1].id == original.id
        assert runner.state.messages[-1].content == "原回复应该保留。"
        assert not any(message.status == "pending" for message in runner.state.messages)
        errors = [payload for name, payload in sink.events if name == "message.error"]
        assert errors[-1]["discard_pending"] is True
    finally:
        await runner.engines.shutdown_all()


async def test_swipe_compares_replaced_candidate_and_preserves_variants():
    runner, _ = make_runner(replies=[OLD, COPY, NEW])
    try:
        created = await runner.player_say("测试甲，讲讲路线。", mentions=["char-a"])
        result = await runner.swipe(created[-1].id)
        assert result.content == NEW
        assert [variant.content for variant in result.variants] == [OLD, NEW]
    finally:
        await runner.engines.shutdown_all()
