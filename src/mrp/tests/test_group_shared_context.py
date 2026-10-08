from types import SimpleNamespace

import pytest

from mrp.engines.fake import FakeEngine
from mrp.orchestrator.context_plan import plan_context
from mrp.orchestrator.session import SessionRunner
from mrp.orchestrator.model_capacity import ModelCapacity
from mrp.response_styles import ResponseStyle
from mrp.shared.actor_labels import build_reply_frame
from mrp.shared.models import (
    Character, CharacterCard, GroupActor, Lorebook, LorebookEntry, Message,
    PinnedFact, Scene, SessionMeta, SessionState, TurnContext,
)
from mrp.shared.prompt import compose_prompt, persona_from_card
from mrp.orchestrator.group_actors import GroupResponder
from mrp.settings import AppSettings
from mrp.storage.prompt_presets import PromptPreset


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("story_output_limit", "expected_output_limit"),
    [(None, 1800), (0, None), (900, 900)],
)
async def test_group_uses_shared_context_sources_visibility_and_frozen_inspection(
    monkeypatch, story_output_limit, expected_output_limit,
):
    async def capacity(*args, **kwargs):
        return ModelCapacity(context_limit=24000, input_limit=22000, output_reserve=2000,
                             source="test")

    monkeypatch.setattr("mrp.orchestrator.model_capacity.resolve_model_capacity", capacity)
    character = Character(id="guide", card=CharacterCard(name="向导", description="守护者"))
    group = GroupActor(
        id="goblins", label="哥布林", scene_id="scene", joined_seq=0,
        public_brief="洞穴中的普通哥布林。", source={"kind": "biology"},
        source_snapshot="怕火，习惯群居。",
    )
    scene = Scene(id="scene", title="洞穴入口", description="清晨", member_ids=[character.id],
                  group_ids=[group.id])
    settings = AppSettings(
        generation={"max_output_tokens": 1800},
        response_styles=[ResponseStyle(id="measured", name="谨慎", content="先确认事实，再谨慎回答。")]
    )
    meta = SessionMeta(
        id="shared-context", title="测试故事", character_ids=[character.id],
        streaming_enabled=False, reply_max_tokens=story_output_limit,
        world_core_brief="世界核心-死者不可复活。", scenario_instructions="本局约定-不要离开洞穴。",
        world_archive_records=[
            {"id": "background-record", "kind": "background", "visibility": "public",
             "title": "大陆背景", "summary": "摘要", "body": "背景正文-三国沿河而立。"},
            {"id": "biology-record", "kind": "biology", "visibility": "public",
             "title": "哥布林", "aliases": ["小绿皮"], "tags": [], "summary": "群居生物。",
             "body": "生物正文-成年哥布林会轮流守夜。", "kind_data": {"habitat": "洞穴"}},
        ],
        response_style_id="measured",
    )
    meta.prompt_preset_snapshot = PromptPreset(
        id="shared-preset", name="测试预设",
        segments=[
            {"id": "opening-rule", "name": "常驻设定", "content": "预设正文-遵守洞穴规矩。"},
            {"id": "depth-rule", "name": "历史深度", "content": "深度锚点-保持上下文连续。",
             "anchor": "at_depth", "depth": 1},
        ],
        transforms=[{"id": "replace-safe", "name": "请求变换", "pattern": "旧词条", "replacement": "新词条"}],
    ).model_dump(mode="json")
    state = SessionState(
        meta=meta, characters=[character], groups=[group], scenes=[scene], active_scene_id=scene.id,
        pinned_facts=[PinnedFact(content="固定事实-" + "这条固定事实需要完整进入上下文。" * 130 + "固定事实尾部标记。")],
        lorebooks=[Lorebook(id="book", name="洞穴书", token_budget=10000,
                            entries=[LorebookEntry(uid=1, keys=["哥布林"], content="世界书条目-哥布林怕火。")])],
        messages=[
            Message(session_id=meta.id, id="player-current", seq=0, turn=1, actor="player",
                    content="哥布林，请回答旧词条附近有什么？", kind="roleplay", scene_id=scene.id),
            Message(session_id=meta.id, id="private-inner", seq=1, turn=1, actor="player",
                    content="内心秘密-我其实害怕他们。", kind="inner", visible_to=["player"], scene_id=scene.id),
        ],
    )
    class RequestArchive:
        def __init__(self):
            self.bodies = []

        def write(self, *args, **kwargs):
            self.bodies.append(args[-1])

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "群体回应"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 12}}

    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["body"] = json
        return Response()

    monkeypatch.setattr("mrp.llm.httpx.post", fake_post)
    archive = RequestArchive()
    container = SimpleNamespace(
        settings=settings, fake_mode=False, memory_store=None,
        config=SimpleNamespace(api_key_env="TEST_ONLY_EMPTY_KEY"), request_archive=archive,
    )
    responder = GroupResponder(container)
    runner = SessionRunner(state, FakeEngine(), lorebooks=state.lorebooks,
                           group_responder=responder, app_settings=settings)

    await runner.turns.run_group_turn(group, 1, participants=[group.id, character.id])

    record = runner.runtime.inspections[(group.id, 1)]
    ctx, composed = record["ctx"], record["composed"]
    sent = composed.text
    for marker in (
        "世界核心-死者不可复活", "背景正文-三国沿河而立", "生物正文-成年哥布林会轮流守夜",
        "世界书条目-哥布林怕火", "本局约定-不要离开洞穴", "固定事实尾部标记",
        "预设正文-遵守洞穴规矩", "深度锚点-保持上下文连续", "地点：洞穴入口",
        "新词条附近有什么", "先确认事实，再谨慎回答。",
    ):
        assert marker in sent
    assert "内心秘密-我其实害怕他们" not in sent
    assert sent.count("哥布林，请回答") == 1
    assert sent.count(group.public_brief) == 1
    assert ctx.planned_prompt is composed
    assert composed.entry_sections["pinned_facts"] == "current_context"
    assert composed.entry_sections["response_style:measured:1"] == "instructions"
    assert composed.message_sections["player-current"] == "current_turn"
    assert record["ctx"].plan_id
    assert captured["body"]["messages"][1]["content"] == sent
    assert captured["body"]["messages"][0]["content"].startswith("你扮演临时群体")
    if expected_output_limit is None:
        assert "max_tokens" not in captured["body"]
    else:
        assert captured["body"]["max_tokens"] == expected_output_limit
    assert archive.bodies == [captured["body"]]

    # The individual actor gets the same shared public sources, while the
    # already established inner-thought behavior remains individual-only.
    visible = state.visible_messages_for(character.id)
    frame = build_reply_frame(state, character.id, 1, "single", visible, [group.id, character.id])
    builder = runner.context_builder
    injections = await builder.build_injections(character, 1, visible=visible,
                                                participants=[group.id, character.id])
    character_ctx = TurnContext(
        session_id=meta.id, character_id=character.id, turn=1, visible_messages=visible,
        reply_frame=frame, actor_labels=frame.actor_labels, injections=injections,
        world_core_brief=meta.world_core_brief, player_persona=meta.player_persona,
    )
    character_prompt = plan_context(
        character_ctx, model=character.llm.model, persona=persona_from_card(character.card)
    ).prompt.text
    for marker in ("背景正文-三国沿河而立", "世界书条目-哥布林怕火", "本局约定-不要离开洞穴"):
        assert marker in character_prompt
    assert "内心秘密-我其实害怕他们" in character_prompt
    assert "玩家情绪暗示" in character_prompt
