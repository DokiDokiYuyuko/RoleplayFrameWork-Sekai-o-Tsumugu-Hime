from mrp.shared.models import Injection, Message, ReplyFrame, TurnContext
from mrp.shared.prompt import compose_prompt, reply_frame_instruction


def test_current_input_is_once_and_follows_history_and_recalled_facts():
    old = Message(session_id="story", id="old", seq=0, turn=2, actor="char-a", content="旧场景事实。")
    player = Message(session_id="story", id="player-now", seq=1, turn=3, actor="player",
                     content="当前问题标记-她在哪里？")
    previous = Message(session_id="story", id="prior-now", seq=2, turn=3, actor="char-b",
                       content="我刚才在码头看见她。")
    frame = ReplyFrame(
        speaker_id="char-a", speaker_kind="character", speaker_label="向导",
        mode="serial", turn=3, trigger_message_ids=[player.id],
        visible_prior_reply_ids=[previous.id], visible_prior_speaker_labels=["角色·守卫"],
        actor_labels={"char-a": "角色·向导", "char-b": "角色·守卫", "player": "玩家·旅人"},
        addressed_inputs=[player.content], other_addressed_inputs=["角色·守卫：其他问题"],
    )
    ctx = TurnContext(
        session_id="story", character_id="char-a", turn=3,
        visible_messages=[old, player, previous], reply_frame=frame,
        world_core_brief="不可复活的世界规则。", player_persona="姓名：旅人",
        injections=[
            Injection(source="memory", entry_id="promise", content="历史约定：在码头寻找她。"),
            Injection(source="system", entry_id="scene", content="当前地点：码头。", placement="current"),
            Injection(source="system", entry_id="style", content="回答克制、谨慎。", placement="instructions"),
        ],
    )

    result = compose_prompt(ctx)

    assert result.text.count("当前问题标记-她在哪里？") == 1
    assert result.text.index("世界核心") < result.text.index("旧场景事实")
    assert result.text.index("旧场景事实") < result.text.index("相关记忆")
    assert result.text.index("相关记忆") < result.text.index("当前地点：码头")
    assert result.text.index("当前地点：码头") < result.text.index("当前问题标记-她在哪里？")
    assert result.text.index("当前问题标记-她在哪里？") < result.text.index("我刚才在码头看见她")
    assert result.text.index("回答克制、谨慎。") > result.text.index("=== 现在轮到你 ===")
    assert result.included_message_ids == [old.id, player.id, previous.id]
    assert result.message_sections == {
        old.id: "history", player.id: "current_turn", previous.id: "current_turn"
    }
    assert "自然口语" not in result.text and "华丽修辞" not in result.text


def test_current_facts_are_not_truncated_by_the_old_pinned_fact_cap():
    full_text = "固定事实-" + "这个事实必须完整保留。" * 1800 + "尾部哨兵-不可截断"
    ctx = TurnContext(
        session_id="story", character_id="char-a", turn=1,
        injections=[Injection(source="system", entry_id="pinned_facts", content=full_text,
                              placement="current")],
    )

    result = compose_prompt(ctx)

    assert full_text in result.text
    assert result.entry_sections["pinned_facts"] == "current_context"


def test_reply_frame_guidance_does_not_duplicate_player_message_text():
    frame = ReplyFrame(
        speaker_id="char-a", speaker_kind="character", speaker_label="向导",
        mode="single", turn=1, addressed_inputs=["测试中不得重复的玩家原句"],
    )

    guidance = reply_frame_instruction(frame)

    assert "逐项回答" in guidance
    assert "测试中不得重复的玩家原句" not in guidance


def test_at_depth_anchor_is_resolved_against_the_full_retained_message_sequence():
    old = Message(session_id="story", id="old", seq=0, turn=0, actor="char-a", content="旧历史。")
    player = Message(session_id="story", id="player", seq=1, turn=1, actor="player", content="当前输入。")
    prior = Message(session_id="story", id="prior", seq=2, turn=1, actor="char-b", content="前序接话。")
    frame = ReplyFrame(
        speaker_id="char-a", speaker_kind="character", speaker_label="向导",
        mode="serial", turn=1, trigger_message_ids=[player.id],
        visible_prior_reply_ids=[prior.id], actor_labels={},
    )
    ctx = TurnContext(
        session_id="story", character_id="char-a", turn=1,
        visible_messages=[old, player, prior], reply_frame=frame,
        injections=[Injection(source="system", entry_id="depth-one", content="深度一锚点。",
                              anchor="at_depth", depth=1)],
    )

    text = compose_prompt(ctx).text

    assert text.index("当前输入。") < text.index("深度一锚点。") < text.index("前序接话。")
