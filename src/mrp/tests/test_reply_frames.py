from __future__ import annotations

from types import SimpleNamespace

import pytest

from mrp.orchestrator.group_actors import GroupResponder
from mrp.orchestrator.model_capacity import ModelCapacity
from mrp.orchestrator.scene_frame import build_shared_scene_frame, recent_public_scene_context
from mrp.shared.actor_labels import build_actor_labels, build_reply_frame
from mrp.shared.models import Character, CharacterCard, EngineReply, GroupActor, Message, Scene, SessionMeta, SessionState, TokenUsage, TurnContext
from mrp.shared.prompt import compose_prompt


def _state() -> SessionState:
    character = Character(id="char-a", card=CharacterCard(name="向导"))
    group = GroupActor(id="group-a", label="街边摊主们", scene_id="scene-a", joined_seq=0)
    scene = Scene(id="scene-a", title="街道", member_ids=[character.id], group_ids=[group.id])
    state = SessionState(
        meta=SessionMeta(id="sess-frame", character_ids=[character.id], player_persona="姓名：叶清让\n身份：旅人"),
        characters=[character], groups=[group], scenes=[scene], active_scene_id=scene.id,
    )
    state.messages.extend([
        Message(session_id=state.meta.id, id="player-turn", seq=0, turn=1, actor="player",
                content="向导，你先看看门口。", scene_id=scene.id),
        Message(session_id=state.meta.id, id="char-reply", seq=1, turn=1, actor=character.id,
                content="我检查过了，门后没有动静。", scene_id=scene.id),
        Message(session_id=state.meta.id, id="private-note", seq=2, turn=1, actor="player",
                content="群体不应该知道的备注。", kind="inner", visible_to=["player"], scene_id=scene.id),
    ])
    return state


def test_actor_labels_use_names_and_disambiguate_missing_roster_entries():
    state = _state()
    state.characters.append(Character(id="char-b", card=CharacterCard(name="向导")))
    visible = [
        state.messages[0],
        Message(session_id=state.meta.id, seq=3, turn=2, actor="char-b", content="在。"),
        Message(session_id=state.meta.id, seq=4, turn=2, actor="deleted-char", content="旧发言。"),
    ]

    labels = build_actor_labels(state, visible, speaker_id="group-a")

    assert labels["player"] == "玩家·叶清让"
    assert labels["char-b"] == "角色·向导〔2〕"
    assert labels["deleted-char"] == "已退出角色〔1〕"
    assert labels["group-a"] == "群体·街边摊主们"


def test_explicit_roster_ids_survive_name_collision_loop():
    state = _state()
    labels = build_actor_labels(state, [], actor_ids=['char-a', 'group-a'])
    assert set(labels) == {'char-a', 'group-a'}


def test_tasks_separate_direct_questions_from_mentions_of_another_person():
    state = _state()
    state.characters.append(Character(id='char-b', card=CharacterCard(name='守卫')))
    message = state.messages[0].model_copy(update={
        'content': '向导，请说出门牌号。守卫，你和向导是同事吗？然后请向导说明你的职业。'
    })
    frame = build_reply_frame(state, 'char-a', 1, 'serial', [message], ['char-a', 'char-b'])
    assert len(frame.addressed_inputs) == 2
    assert len(frame.other_addressed_inputs) == 1
    assert '你和向导是同事吗' in frame.other_addressed_inputs[0]
    assert '逐项回答' in compose_prompt(TurnContext(session_id='s', character_id='char-a', turn=1,
        visible_messages=[message], reply_frame=frame)).text


def test_tasks_use_unique_aliases_and_include_groups():
    state = _state()
    state.characters[0].aliases = ['老周']
    message = state.messages[0].model_copy(update={'content': '老周，先说明情况。街边摊主们，只回答摊位几点关门。'})
    frame = build_reply_frame(state, 'group-a', 1, 'parallel', [message], ['char-a', 'group-a'])
    assert frame.addressed_inputs == ['街边摊主们，只回答摊位几点关门。']
    assert len(frame.other_addressed_inputs) == 1


def test_shared_questions_and_private_thoughts_are_not_assigned_to_npcs():
    state = _state()
    public = state.messages[0].model_copy(update={'content': '大家，请说说天气。你们准备好了吗？'})
    frame = build_reply_frame(state, 'char-a', 1, 'parallel', [public])
    assert not frame.addressed_inputs and not frame.other_addressed_inputs
    assert len(frame.shared_inputs) == 2
    assert '备注' not in str(frame.model_dump())


def test_duplicate_aliases_and_quoted_names_do_not_force_a_recipient():
    state = _state()
    state.characters.append(Character(id='char-b', card=CharacterCard(name='守卫'), aliases=['向导']))
    message = state.messages[0].model_copy(update={'content': '向导，你怎么看？他说“守卫，你先走。”你怎么看这句话？'})
    frame = build_reply_frame(state, 'char-a', 1, 'parallel', [message])
    assert not frame.addressed_inputs and not frame.other_addressed_inputs


def test_scene_directions_are_shared_instead_of_treated_as_spoken_questions():
    state = _state()
    message = state.messages[0].model_copy(update={'kind': 'scene', 'content': '向导，你先走。'})
    frame = build_reply_frame(state, 'char-a', 1, 'serial', [message])
    assert not frame.addressed_inputs
    assert frame.shared_inputs == ['向导，你先走。']


def test_same_sentence_vocatives_split_without_assigning_quoted_dialogue():
    state = _state()
    state.characters.append(Character(id='char-b', card=CharacterCard(name='守卫')))
    message = state.messages[0].model_copy(update={'content': '向导，请说口令，守卫，请说时间。有人说“向导，你先走。守卫，你留下。”'})
    frame = build_reply_frame(state, 'char-a', 1, 'serial', [message], ['char-a', 'char-b'])
    assert frame.addressed_inputs == ['向导，请说口令，']
    assert len(frame.other_addressed_inputs) == 1
    assert len(frame.shared_inputs) == 1
    assert '有人说' in frame.shared_inputs[0]


def test_title_name_cards_recognize_explicit_unique_name_suffix():
    state = _state()
    state.characters[0].card.name = '城门向导-周安'
    state.characters.append(Character(id='char-b', card=CharacterCard(name='守卫-沈乐')))
    message = state.messages[0].model_copy(update={'content': '周安，请说出你的职业。沈乐，只说值班时间。'})
    frame = build_reply_frame(state, 'char-a', 1, 'serial', [message], ['char-a', 'char-b'])
    assert len(frame.addressed_inputs) == 1
    assert len(frame.other_addressed_inputs) == 1


def test_multiple_obligations_for_one_actor_are_separate_checklist_items():
    state = _state()
    message = state.messages[0].model_copy(update={'content': '向导，请复述时间，再说你自己的职业。'})
    frame = build_reply_frame(state, 'char-a', 1, 'serial', [message])
    assert len(frame.addressed_inputs) == 2
    assert frame.addressed_inputs[1] == '再说你自己的职业。'


def test_two_relationship_pairs_do_not_collapse_into_one_obligation():
    state = _state()
    message = state.messages[0].model_copy(update={'content': '向导，请说明你和我的关系，以及你与守卫的关系。'})
    frame = build_reply_frame(state, 'char-a', 1, 'serial', [message])
    assert len(frame.addressed_inputs) == 2
    assert frame.addressed_inputs[1] == '以及你与守卫的关系。'


def test_serial_prompt_uses_visible_speakers_and_preserves_required_replies():
    state = _state()
    visible = [
        Message(session_id=state.meta.id, id="old", seq=0, turn=0, actor="char-a", content="旧记录。" * 1200),
        state.messages[0],
        state.messages[1],
    ]
    frame = build_reply_frame(state, "group-a", 1, "serial", visible, ["char-a", "group-a"])
    ctx = TurnContext(
        session_id=state.meta.id, character_id="group-a", turn=1,
        visible_messages=visible, actor_labels=frame.actor_labels, reply_frame=frame,
        budget_tokens=1024,
    )

    composed = compose_prompt(ctx)

    assert frame.visible_prior_reply_ids == ["char-reply"]
    assert "本轮你已读到的前序发言者：角色·向导" in composed.text
    assert "[角色·向导] 我检查过了，门后没有动静。" in composed.text
    assert "char-reply" in composed.included_message_ids
    assert "player-turn" in composed.included_message_ids
    assert "char-a:" not in composed.text
    assert "旧记录" not in composed.text


def test_serial_prompt_fails_instead_of_dropping_required_context():
    state = _state()
    huge = Message(session_id=state.meta.id, id="player-turn", seq=0, turn=1,
                   actor="player", content="超长触发内容" * 500)
    prior = Message(session_id=state.meta.id, id="char-reply", seq=1, turn=1,
                    actor="char-a", content="前序回复" * 500)
    visible = [huge, prior]
    frame = build_reply_frame(state, "group-a", 1, "serial", visible, ["char-a", "group-a"])
    ctx = TurnContext(session_id=state.meta.id, character_id="group-a", turn=1,
                      visible_messages=visible, reply_frame=frame, budget_tokens=1024)

    with pytest.raises(ValueError, match="串行接力所需消息超过"):
        compose_prompt(ctx)


@pytest.mark.asyncio
async def test_group_prompt_includes_visible_serial_reply_and_excludes_private_text(monkeypatch):
    async def capacity(*args, **kwargs):
        return ModelCapacity(context_limit=None, input_limit=None, output_reserve=512, source="test")

    monkeypatch.setattr("mrp.orchestrator.model_capacity.resolve_model_capacity", capacity)
    from mrp.settings import AppSettings

    state = _state()
    group = state.groups[0]
    responder = GroupResponder(SimpleNamespace(fake_mode=True, settings=AppSettings()))

    _, _, trace = await responder.generate(
        state, group, reply_mode="serial", participants=["char-a", "group-a"],
    )

    assert "[角色·向导] 我检查过了，门后没有动静。" in trace["user"]
    assert "private-note" not in [message.id for message in trace["visible_messages"]]
    assert "群体不应该知道的备注" not in trace["user"]
    assert trace["reply_frame"].visible_prior_reply_ids == ["char-reply"]
    assert "本轮你已读到的前序发言者：角色·向导" in trace["user"]


@pytest.mark.asyncio
async def test_group_parallel_prompt_uses_generation_snapshot(monkeypatch):
    async def capacity(*args, **kwargs):
        return ModelCapacity(context_limit=None, input_limit=None, output_reserve=512, source="test")

    monkeypatch.setattr("mrp.orchestrator.model_capacity.resolve_model_capacity", capacity)
    from mrp.settings import AppSettings

    state = _state()
    state.messages = [state.messages[0], state.messages[2]]
    responder = GroupResponder(SimpleNamespace(fake_mode=True, settings=AppSettings()))

    _, _, trace = await responder.generate(
        state, state.groups[0], reply_mode="parallel", participants=["char-a", "group-a"],
    )

    assert trace["reply_frame"].mode == "parallel"
    assert trace["reply_frame"].visible_prior_reply_ids == []
    assert "我检查过了，门后没有动静" not in trace["user"]
    assert "群体不应该知道的备注" not in trace["user"]
    assert "并行回应" in trace["user"]


def test_public_scene_summaries_use_labels_and_never_private_text():
    state = _state()

    recent = recent_public_scene_context(state, 1)
    frame = build_shared_scene_frame(state, 1)

    assert "角色·向导：我检查过了" in recent
    assert "char-a" not in recent + frame
    assert "群体不应该知道的备注" not in recent + frame
    assert "玩家·叶清让" in frame


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [["char-a", "char-b"], ["char-b", "char-a"]])
async def test_serial_character_order_passes_actual_labeled_reply(order):
    from mrp.orchestrator.session import SessionRunner

    first = Character(id="char-a", card=CharacterCard(name="向导"))
    second = Character(id="char-b", card=CharacterCard(name="守卫"))
    scene = Scene(id="scene-a", title="城门", member_ids=[first.id, second.id])
    state = SessionState(
        meta=SessionMeta(id="sess-role-chain", character_ids=[first.id, second.id],
                         director_mode="rules", hygiene_enabled=False,
                         short_input_padding=False, proactive_turn_limit=0),
        characters=[first, second], scenes=[scene], active_scene_id=scene.id,
    )

    class CapturingEngine:
        def __init__(self):
            self.contexts = []

        async def generate(self, character, ctx, *, on_delta=None):
            self.contexts.append(ctx)
            return EngineReply(content=f"{character.card.name}的实际回复", usage=TokenUsage())

    engine = CapturingEngine()
    runner = SessionRunner(state, engine)
    created = await runner.player_say_with_reply_mode(
        "请依次商量怎么过城门。", mentions=order, reply_mode="serial",
    )

    second_ctx = engine.contexts[1]
    assert second_ctx.reply_frame.mode == "serial"
    assert second_ctx.reply_frame.trigger_message_ids == [created[0].id]
    assert second_ctx.reply_frame.visible_prior_reply_ids == [created[1].id]
    assert second_ctx.reply_frame.visible_prior_speaker_labels == [
        "角色·向导" if order[0] == "char-a" else "角色·守卫"
    ]
    assert created[1].content in second_ctx.planned_prompt.text
    assert "char-a:" not in second_ctx.planned_prompt.text
    assert "char-b:" not in second_ctx.planned_prompt.text


@pytest.mark.asyncio
async def test_parallel_scene_planner_only_returns_known_scene_facts(monkeypatch):
    from mrp.settings import AppSettings

    captured = []

    def planner(messages, config, **kwargs):
        captured.extend(messages)
        return '{"stage":"门口已经打开，众人仍在街道上。"}', {
            "input_tokens": 3, "output_tokens": 2, "cached_tokens": 0,
        }

    monkeypatch.setattr("mrp.orchestrator.group_actors.chat_text_with_usage", planner)
    responder = GroupResponder(SimpleNamespace(
        fake_mode=False, settings=AppSettings(), config=SimpleNamespace(api_key_env="TEST_KEY"),
    ))

    planned, _ = await responder.plan_parallel_scene(
        _state(), 1, {"char-a": "向导", "group-a": "街边摊主们"},
    )

    assert "已经发生或明确给出的事实" in captured[0]["content"]
    assert "不设计任何参与者接下来要说的话、动作" in captured[0]["content"]
    assert "beats" not in captured[0]["content"]
    assert "向导" not in planned and "摊主们" not in planned
    assert "门口已经打开" in planned

