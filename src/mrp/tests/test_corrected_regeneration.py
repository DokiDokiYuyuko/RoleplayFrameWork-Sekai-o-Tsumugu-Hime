"""Synthetic input-correction acceptance; no live data or provider requests."""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from mrp.orchestrator.corrected_regeneration import CorrectedRegenerationRequest, correction_options, correction_transform
from mrp.orchestrator.memory import MemoryStore
from mrp.orchestrator.message_regeneration import RegenerationConflict, RegenerationFailure, RegenerationRequest
from mrp.orchestrator.session import SessionRunner
from mrp.shared.models import Character, CharacterCard, EngineReply, MemoryRecord, Message, Scene, SessionMeta, SessionState


class SyntheticEngine:
    def __init__(self):
        self.contexts = []
        self.fail = False

    async def generate(self, character, ctx, *, on_delta=None):
        self.contexts.append(ctx)
        if self.fail:
            raise RuntimeError("synthetic generation failure")
        return EngineReply(content=f"answer-{len(self.contexts)}")


@pytest.fixture
def correction_story(tmp_path):
    store = MemoryStore(tmp_path / "synthetic.db", tmp_path / "mirror", None)
    character = Character(id="char-synthetic", card=CharacterCard(name="守卫", description="WRONG STATIC", personality="KEEP OLD PERSONALITY"))
    scene = Scene(id="scene-synthetic", title="旧场景", description="KEEP ORIGINAL SCENE", member_ids=[character.id])
    sid = "sess-synthetic"
    history = [Message(id=f"msg-old-{i}", session_id=sid, seq=i, turn=i // 2 + 1,
                       actor="player" if i % 2 == 0 else character.id,
                       content=f"synthetic history {i}", scene_id=scene.id) for i in range(16)]
    state = SessionState(schema_version=3, meta=SessionMeta(id=sid, character_ids=[character.id],
        director_mode="rules", short_input_padding=False, hygiene_enabled=False, proactive_turn_limit=0,
        memory_interval_turns=1000), characters=[character], scenes=[scene], active_scene_id=scene.id, messages=history)
    memory = MemoryRecord(id="mem-synthetic", character_id=character.id, session_id=sid,
        content="WRONG MEMORY", important=True, source_message_ids=[history[0].id],
        source_fingerprints={history[0].id: history[0].fingerprint}, participant_ids=["player", character.id],
        effective_message_id=history[0].id, turn_start=1, turn_end=1)
    store.add(memory)
    engine = SyntheticEngine()
    runner = SessionRunner(state, engine, memory_store=store)
    yield runner, engine, store, memory
    store.close()


def _ordinary(runner, target, op):
    return RegenerationRequest(operation_id=op, expected_branch_revision=runner.state.meta.branch_revision,
        expected_fingerprint=target.fingerprint, expected_player_identity_id=runner.state.meta.player_identity_id)


def _corrected(runner, target, report, *, fields=None, memories=None, op="corrected"):
    return CorrectedRegenerationRequest(**_ordinary(runner, target, op).model_dump(),
        options_digest=report["options_digest"], selected_static_fields=fields or [],
        selected_memory_revisions=memories or {})


@pytest.mark.asyncio
async def test_confirmed_corrections_replace_actual_input_and_remain_in_future_ordinary_regeneration(correction_story):
    runner, engine, store, memory = correction_story
    target = (await runner.player_say("open the gate", force_character="char-synthetic"))[-1]
    original = target.model_copy(deep=True)
    original_prompt = engine.contexts[-1].planned_prompt.text
    assert "WRONG MEMORY" in original_prompt
    assert "WRONG STATIC" in engine.contexts[-1]._planned_persona
    other_branch = runner.state.model_copy(deep=True)
    other_branch.meta.id = "sess-other"
    other_branch_memory = memory.model_copy(update={"id": "mem-other", "session_id": other_branch.meta.id})
    store.add(other_branch_memory)
    runner.state.characters[0].card.description = "CORRECT STATIC"
    runner.state.characters[0].card.personality = "UNSELECTED FUTURE PERSONALITY"
    runner.state.scenes[0].description = "UNSELECTED FUTURE SCENE"
    revised = store.records_for(memory.character_id, session_id=runner.state.meta.id)[0]
    revised.content = "CORRECT MEMORY"
    revised.revision = 2
    revised.manually_revised = True
    store.update_record(revised)
    # Ordinary alternatives keep old static snapshots and do not silently adopt the new memory.
    await runner.regeneration.run("one", target.id, _ordinary(runner, target, "ordinary-before"))
    assert "WRONG STATIC" in engine.contexts[-1]._planned_persona
    assert "CORRECT MEMORY" not in engine.contexts[-1].planned_prompt.text
    # Select from the original candidate which actually used this memory.
    await runner.regeneration.run("switch", target.id, _ordinary(runner, target, "switch-original"), variant_index=0)
    report = correction_options(runner, target, store)[0]
    assert next(x for x in report["memories"] if x["id"] == memory.id)["eligible"]
    key = "character:char-synthetic:description"
    req = _corrected(runner, target, report, fields=[key], memories={memory.id: 2})
    before_sources = target.generation_meta.provenance.baseline_message_ids[:]
    result = await runner.regeneration.run("one", target.id, req,
        baseline_transform=correction_transform(runner, req, store))
    ctx = engine.contexts[-1]
    assert "CORRECT STATIC" in ctx._planned_persona and "WRONG STATIC" not in ctx._planned_persona
    assert "KEEP OLD PERSONALITY" in ctx._planned_persona and "UNSELECTED FUTURE PERSONALITY" not in ctx._planned_persona
    assert "CORRECT MEMORY" in ctx.planned_prompt.text and "WRONG MEMORY" not in ctx.planned_prompt.text
    assert "KEEP ORIGINAL SCENE" in ctx.planned_prompt.text and "UNSELECTED FUTURE SCENE" not in ctx.planned_prompt.text
    assert target.generation_meta.provenance.baseline_message_ids == before_sources
    assert target.generation_meta.provenance.baseline_state["_corrected_memory_records"][0]["revision"] == 2
    assert result["operation_id"] == "corrected" and len(target.variants) >= 2
    # A later third memory edit cannot change the already-confirmed candidate's baseline.
    revised.content = "UNCONFIRMED THIRD MEMORY"
    revised.revision = 3
    store.update_record(revised)
    await runner.regeneration.run("one", target.id, _ordinary(runner, target, "ordinary-after"))
    ctx = engine.contexts[-1]
    assert "CORRECT STATIC" in ctx._planned_persona and "CORRECT MEMORY" in ctx.planned_prompt.text
    assert "UNCONFIRMED THIRD MEMORY" not in ctx.planned_prompt.text
    assert other_branch.characters[0].card.description == "WRONG STATIC"
    assert store.records_for(memory.character_id, session_id=other_branch.meta.id)[0].content == "WRONG MEMORY"
    assert engine.contexts[0].planned_prompt.text == original_prompt


@pytest.mark.asyncio
async def test_correction_refuses_future_evidence_revision_races_identity_and_scene_changes(correction_story):
    runner, engine, store, memory = correction_story
    target = (await runner.player_say("gate", force_character="char-synthetic"))[-1]
    report = correction_options(runner, target, store)[0]
    request = _corrected(runner, target, report, memories={memory.id: 1}, op="future")
    later = Message(id="msg-future", session_id=runner.state.meta.id, seq=runner.state.next_seq(),
        turn=target.turn, actor=memory.character_id, content="PRIVATE FUTURE CONTENT", scene_id=target.scene_id)
    runner.state.messages.append(later)
    record = store.records_for(memory.character_id, session_id=runner.state.meta.id)[0]
    record.source_message_ids.append(later.id)
    record.source_fingerprints[later.id] = later.fingerprint
    record.revision = 2
    store.update_record(record)
    new = correction_options(runner, target, store)[0]
    row = next(x for x in new["memories"] if x["id"] == memory.id)
    assert not row["eligible"] and row["content"] is None
    before_calls = len(engine.contexts)
    with pytest.raises(RegenerationConflict):
        await runner.regeneration.run("one", target.id, request,
            baseline_transform=correction_transform(runner, request, store))
    assert len(engine.contexts) == before_calls
    request = _corrected(runner, target, new, memories={memory.id: 2}, op="future-confirmed")
    with pytest.raises(RegenerationConflict, match="后续来源"):
        await runner.regeneration.run("one", target.id, request,
            baseline_transform=correction_transform(runner, request, store))
    runner.state.meta.branch_revision += 1
    with pytest.raises(RegenerationConflict, match="路线内容"):
        await runner.regeneration.run("one", target.id, request,
            baseline_transform=correction_transform(runner, request, store))
    runner.state.meta.branch_revision -= 1
    runner.state.meta.player_identity_id = "changed-identity"
    with pytest.raises(RegenerationConflict, match="控制身份"):
        await runner.regeneration.run("one", target.id, request,
            baseline_transform=correction_transform(runner, request, store))
    runner.state.meta.player_identity_id = request.expected_player_identity_id
    runner.state.active_scene_id = "changed-scene"
    with pytest.raises(RegenerationConflict, match="当前控制阶段和场景"):
        correction_options(runner, target, store)
    assert len(engine.contexts) == before_calls


@pytest.mark.asyncio
async def test_corrected_candidate_freezes_unselected_global_policy_but_new_turn_uses_current_policy(correction_story):
    from mrp.orchestrator.model_capacity import ModelCapacity
    runner, engine, store, _ = correction_story
    runner.app_settings = SimpleNamespace(break_armor_prompt="ORIGINAL GLOBAL POLICY", break_armor_mode="interval",
                                         break_armor_interval=1, active_prompt_preset_id=None, response_styles=[])
    async def capacity(*args, **kwargs):
        return ModelCapacity(None, None, 8192, "synthetic")
    runner.context_builder.model_capacity = capacity
    target = (await runner.player_say("gate", force_character="char-synthetic"))[-1]
    assert "ORIGINAL GLOBAL POLICY" in engine.contexts[-1].planned_prompt.text
    runner.app_settings.break_armor_prompt = "UNSELECTED NEW GLOBAL POLICY"
    runner.state.characters[0].card.description = "CORRECT STATIC"
    report = correction_options(runner, target, store)[0]
    req = _corrected(runner, target, report, fields=["character:char-synthetic:description"])
    await runner.regeneration.run("one", target.id, req, baseline_transform=correction_transform(runner, req, store))
    assert "ORIGINAL GLOBAL POLICY" in engine.contexts[-1].planned_prompt.text
    assert "UNSELECTED NEW GLOBAL POLICY" not in engine.contexts[-1].planned_prompt.text
    await runner.regeneration.run("one", target.id, _ordinary(runner, target, "ordinary-after-policy"))
    assert "ORIGINAL GLOBAL POLICY" in engine.contexts[-1].planned_prompt.text
    assert "UNSELECTED NEW GLOBAL POLICY" not in engine.contexts[-1].planned_prompt.text
    await runner.player_say("next input", force_character="char-synthetic")
    assert "UNSELECTED NEW GLOBAL POLICY" in engine.contexts[-1].planned_prompt.text


@pytest.mark.asyncio
async def test_failed_corrected_generation_keeps_original_candidates_and_confirmed_memory(correction_story):
    runner, engine, store, memory = correction_story
    target = (await runner.player_say("gate", force_character="char-synthetic"))[-1]
    runner.state.characters[0].card.description = "CORRECT STATIC"
    revised = store.records_for(memory.character_id, session_id=runner.state.meta.id)[0]
    revised.content = "CORRECT MEMORY"
    revised.revision = 2
    store.update_record(revised)
    report = correction_options(runner, target, store)[0]
    req = _corrected(runner, target, report, fields=["character:char-synthetic:description"], memories={memory.id: 2})
    before = runner.state.model_dump(mode="json")
    engine.fail = True
    with pytest.raises(RegenerationFailure, match="原回复和候选已保留"):
        await runner.regeneration.run("one", target.id, req,
            baseline_transform=correction_transform(runner, req, store))
    after = runner.state.model_dump(mode="json")
    # A failed model request can still be billed; the story itself is restored.
    for key in ("usage_records", "usage_incomplete"):
        before.pop(key, None)
        after.pop(key, None)
    assert after == before
    assert target.status == "final" and req.operation_id not in runner.state.generation_operations
    assert store.records_for(memory.character_id, session_id=runner.state.meta.id)[0].content == "CORRECT MEMORY"
