"""Synthetic model failures, restart recovery and late-response isolation."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest

from mrp.tests.conftest import make_test_container
from mrp.tests.test_targeted_regeneration import setup
from mrp.server.app import create_app
from mrp.server.routers.messages import SendMessageReq
from mrp.shared.models import MemoryRecord, SessionState
from mrp.shared.player_identity import ensure_player_identity
from mrp.orchestrator.session import SessionRunner
from mrp.orchestrator.turn_runs import TurnRunConflict, TurnCheckpointFailure


async def rig(tmp_path):
    c = make_test_container(tmp_path)
    r, engine, sink, responder = setup(group=True)
    ensure_player_identity(r.state)
    for character in r.state.characters:
        c._apply_global_llm_settings(character)
    c.turn_runs.attach(r)
    c.conversation_runs.attach(r)
    await c._register_runner(r)
    await c.persist_session(r)
    return c, r, engine, sink, responder


def req(mode="serial", **changes):
    values = dict(content="synthetic input", client_message_id="msg-123456789abc", mentions=["a", "b", "c"], reply_mode=mode)
    values.update(changes)
    return SendMessageReq(**values)


@pytest.mark.asyncio
async def test_input_and_serial_prefix_survive_failure_restart_resume(tmp_path):
    c, r, engine, sink, _ = await rig(tmp_path)
    engine.fail_actor = "b"
    result = await c.turn_runs.send(r, req())
    assert result["turn_run"]["status"] == "failed"
    restored = await c.sessions.load_state(r.state.meta.id)
    assert [m.actor for m in restored.messages] == ["player", "a"]
    assert restored.turn_runs[0].slots[0].status == "committed"
    resumed = SessionRunner(restored, engine)
    c.turn_runs.attach(resumed)
    engine.fail_actor = None
    result = await c.turn_runs.resume(resumed, "msg-123456789abc")
    assert result["turn_run"]["status"] == "completed"
    assert [m.actor for m in resumed.state.messages] == ["player", "a", "b", "c"]
    assert [ctx.character_id for ctx in engine.contexts] == ["a", "b", "b", "c"]
    assert [m.actor for m in engine.contexts[-2].visible_messages] == ["player", "a"]
    assert [m.actor for m in engine.contexts[-1].visible_messages] == ["player", "a", "b"]
    assert len({m.id for m in resumed.state.messages}) == 4
    await c.aclose()


@pytest.mark.asyncio
async def test_parallel_retry_uses_original_baseline_and_keeps_successes(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path)
    engine.fail_actor = "b"
    result = await c.turn_runs.send(r, req("parallel"))
    assert result["turn_run"]["status"] == "failed"
    original = {m.actor: m.id for m in r.state.messages}
    assert set(original) == {"player", "a", "c"}
    engine.fail_actor = None
    result = await c.turn_runs.resume(r, "msg-123456789abc")
    assert result["turn_run"]["status"] == "completed"
    assert len(engine.contexts) == 4
    assert [m.actor for m in engine.contexts[-1].visible_messages] == ["player"]
    assert all(next(m.id for m in r.state.messages if m.actor == actor) == mid for actor, mid in original.items())
    assert [m.actor for m in r.state.messages] == ["player", "a", "b", "c"]
    await c.aclose()


@pytest.mark.asyncio
async def test_input_saved_before_model_and_idempotent_payload_guard(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path)
    entered, gate = asyncio.Event(), asyncio.Event()
    async def block(character, ctx):
        restored = await c.sessions.load_state(r.state.meta.id)
        assert restored.messages[0].content == "synthetic input"
        assert restored.turn_runs[0].slots[0].status == "pending"
        entered.set()
        await gate.wait()
    engine.on_generate = block
    job = asyncio.create_task(c.turn_runs.send(r, req(mentions=["a"])))
    await asyncio.wait_for(entered.wait(), 3)
    duplicate = await c.turn_runs.send(r, req(mentions=["a"]))
    assert duplicate["turn_run"]["status"] == "running"
    assert len(engine.contexts) == 1
    with pytest.raises(TurnRunConflict):
        await c.turn_runs.send(r, req(content="different", mentions=["a"]))
    gate.set()
    await job
    duplicate = await c.turn_runs.send(r, req(mentions=["a"]))
    assert duplicate["turn_run"]["status"] == "completed" and len(engine.contexts) == 1
    await c.aclose()


@pytest.mark.asyncio
async def test_stop_discards_late_model_result_and_requires_explicit_resume(tmp_path):
    c, r, engine, sink, _ = await rig(tmp_path)
    entered, gate = asyncio.Event(), asyncio.Event()
    async def block(character, ctx):
        entered.set()
        await gate.wait()
    engine.on_generate = block
    job = asyncio.create_task(c.turn_runs.send(r, req(mentions=["a"])))
    await asyncio.wait_for(entered.wait(), 3)
    stopped = await c.turn_runs.stop(r, "msg-123456789abc")
    assert stopped["turn_run"]["status"] == "interrupted"
    with pytest.raises(TurnRunConflict):
        await c.turn_runs.resume(r, "msg-123456789abc")
    gate.set()
    result = await job
    assert result["turn_run"]["status"] == "interrupted"
    assert [m.actor for m in r.state.messages] == ["player"]
    assert not any(event == "message.final" and payload["message"]["actor"] == "a" for event, payload in sink.events)
    engine.on_generate = None
    result = await c.turn_runs.resume(r, "msg-123456789abc")
    assert result["turn_run"]["status"] == "completed"
    await c.aclose()


@pytest.mark.asyncio
async def test_running_checkpoint_becomes_interrupted_without_auto_generation(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path)
    entered, gate = asyncio.Event(), asyncio.Event()
    async def block(character, ctx):
        entered.set(); await gate.wait()
    engine.on_generate = block
    job = asyncio.create_task(c.turn_runs.send(r, req(mentions=["a"])))
    await asyncio.wait_for(entered.wait(), 3)
    restored = await c.sessions.load_state(r.state.meta.id)
    rebuilt = SessionRunner(restored, engine)
    c.turn_runs.attach(rebuilt)
    assert rebuilt.state.turn_runs[0].status == "interrupted"
    assert rebuilt.state.turn_runs[0].epoch == 1
    assert [m.actor for m in rebuilt.state.messages] == ["player"]
    job.cancel()
    with pytest.raises(asyncio.CancelledError): await job
    gate.set()
    await c.aclose()


@pytest.mark.asyncio
async def test_resume_rejects_identity_or_scene_or_message_edits(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path)
    engine.fail_actor = "a"
    await c.turn_runs.send(r, req(mentions=["a"]))
    original = r.state.model_dump(mode="json")
    r.state.meta.player_identity_id = "different"
    with pytest.raises(TurnRunConflict): await c.turn_runs.resume(r, "msg-123456789abc")
    r.state = SessionState.model_validate(original)
    r.state.scenes[0].title = "changed"
    with pytest.raises(TurnRunConflict): await c.turn_runs.resume(r, "msg-123456789abc")
    await c.aclose()


@pytest.mark.asyncio
async def test_director_proposal_restores_and_confirmation_is_durable(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path)
    class Judge:
        def decide(self, data): return {"action": "pick_speaker", "chosen": ["a", "b"], "rationale": "synthetic"}
    r.director_judge = Judge()
    r.state.meta.director_mode = "confirm"
    result = await c.turn_runs.send(r, req(mentions=[]))
    assert result["turn_run"]["status"] == "awaiting_director" and not engine.contexts
    state = await c.sessions.load_state(r.state.meta.id)
    restored = SessionRunner(state, engine)
    c.turn_runs.attach(restored)
    assert restored.pending_director is not None
    engine.fail_actor = "b"
    result = await c.turn_runs.resolve_director(restored, accept=True)
    assert result["turn_run"]["status"] == "failed"
    assert restored.pending_director is None and [m.actor for m in restored.state.messages] == ["player", "a"]
    engine.fail_actor = None
    result = await c.turn_runs.resume(restored, "msg-123456789abc")
    assert result["turn_run"]["status"] == "completed"
    assert [ctx.character_id for ctx in engine.contexts] == ["a", "b", "b"]
    await c.aclose()


@pytest.mark.asyncio
async def test_scene_switch_is_applied_once_when_first_speaker_fails(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path)
    class Judge:
        def decide(self, data): return {"action": "switch_scene", "chosen": ["a"], "scene": {"title": "new scene", "members": ["a", "b", "c"]}}
    r.director_judge = Judge()
    r.state.meta.director_mode = "auto"
    engine.fail_actor = "a"
    result = await c.turn_runs.send(r, req(mentions=[]))
    assert result["turn_run"]["status"] == "failed"
    assert len(r.state.scenes) == 2
    engine.fail_actor = None
    result = await c.turn_runs.resume(r, "msg-123456789abc")
    assert result["turn_run"]["status"] == "completed"
    assert len(r.state.scenes) == 2
    assert len([m for m in r.state.messages if m.actor == "director" and m.kind == "scene"]) == 1
    await c.aclose()


@pytest.mark.asyncio
async def test_http_failure_snapshot_and_resume_endpoint(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path)
    engine.fail_actor = "a"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)), base_url="http://127.0.0.1",
                                 headers={"Origin": "http://127.0.0.1"}) as client:
        response = await client.post(f"/api/v1/sessions/{r.state.meta.id}/messages", json=req(mentions=["a"]).model_dump())
        assert response.status_code == 200 and response.json()["turn_run"]["status"] == "failed"
        response = await client.get(f"/api/v1/sessions/{r.state.meta.id}")
        assert response.status_code == 200 and response.json()["turn_runs"][0]["status"] == "failed"
        engine.fail_actor = None
        response = await client.post(f"/api/v1/sessions/{r.state.meta.id}/turn-runs/msg-123456789abc/resume", json={})
        assert response.status_code == 200 and response.json()["turn_run"]["status"] == "completed"
    await c.aclose()


@pytest.mark.asyncio
async def test_failed_input_checkpoint_rolls_back_without_model_call(tmp_path, monkeypatch):
    c, r, engine, _, _ = await rig(tmp_path)
    async def fail(_runner): raise OSError("synthetic storage failure")
    monkeypatch.setattr(c, "persist_session", fail)
    with pytest.raises(TurnCheckpointFailure):
        await c.turn_runs.send(r, req())
    assert not r.state.messages and not r.state.turn_runs and not engine.contexts
    await c.aclose()


@pytest.mark.asyncio
async def test_real_container_eviction_reload_preserves_operation_and_identity(tmp_path):
    c, r, engine, _, responder = await rig(tmp_path)
    engine.fail_actor = "b"
    await c.turn_runs.send(r, req())
    sid = r.state.meta.id
    await c.drop_runner(sid)
    restored = await c.load_session(sid)
    restored.engines, restored.group_responder, restored.app_settings = engine, responder, None
    engine.fail_actor = None
    result = await c.turn_runs.resume(restored, "msg-123456789abc")
    assert result["turn_run"]["status"] == "completed"
    assert [m.actor for m in restored.state.messages] == ["player", "a", "b", "c"]
    await c.aclose()


@pytest.mark.asyncio
async def test_parallel_resume_keeps_memory_watermark_and_clears_completed_baseline(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path)
    r.memory = c.memory_store
    c.memory_store.add(MemoryRecord(character_id="b", session_id=r.state.meta.id,
        kind="manual", content="BASELINE MEMORY", important=True))
    engine.fail_actor = "b"
    await c.turn_runs.send(r, req("parallel"))
    run = r.state.turn_runs[0]
    assert run.parallel_memory_watermark is not None and run.parallel_context is not None
    c.memory_store.add(MemoryRecord(character_id="b", session_id=r.state.meta.id,
        kind="manual", content="LATE MEMORY", important=True, created_at=run.created_at - timedelta(days=1)))
    engine.fail_actor = None
    await c.turn_runs.resume(r, "msg-123456789abc")
    injected = "\n".join(i.content for i in engine.contexts[-1].injections)
    assert "BASELINE MEMORY" in injected and "LATE MEMORY" not in injected
    assert engine.contexts[-1].provenance.baseline_memory_watermark == run.parallel_memory_watermark
    assert r.state.turn_runs[0].parallel_context is None
    restored = await c.sessions.load_state(r.state.meta.id)
    assert restored.turn_runs[0].parallel_context is None
    await c.aclose()


@pytest.mark.asyncio
async def test_parallel_prefix_is_checkpointed_before_all_peers_finish(tmp_path):
    c, r, engine, sink, _ = await rig(tmp_path)
    blocked, release = asyncio.Event(), asyncio.Event()
    async def block(character, ctx):
        if character.id == "b":
            blocked.set(); await release.wait()
    engine.on_generate = block
    job = asyncio.create_task(c.turn_runs.send(r, req("parallel", mentions=["a", "b"])))
    await asyncio.wait_for(blocked.wait(), 3)
    async def wait_saved():
        while not any(event == "message.final" and data["message"]["actor"] == "a" for event, data in sink.events):
            await asyncio.sleep(0)
    await asyncio.wait_for(wait_saved(), 3)
    state = await c.sessions.load_state(r.state.meta.id)
    assert [m.actor for m in state.messages] == ["player", "a"]
    assert state.turn_runs[0].status == "running"
    release.set(); await job
    await c.aclose()


@pytest.mark.asyncio
async def test_stop_during_scene_transition_keeps_old_scene_intact(tmp_path, monkeypatch):
    c, r, engine, _, _ = await rig(tmp_path)
    class Judge:
        def decide(self, data): return {"action": "switch_scene", "chosen": ["a"],
            "scene": {"title": "new scene", "members": ["a", "b", "c"]}}
    r.director_judge, r.state.meta.director_mode = Judge(), "auto"
    entered, gate = asyncio.Event(), asyncio.Event()
    async def transition(*args):
        entered.set(); await gate.wait(); return "synthetic transition"
    monkeypatch.setattr(r.director_flow, "generate_transition", transition)
    job = asyncio.create_task(c.turn_runs.send(r, req(mentions=[])))
    await asyncio.wait_for(entered.wait(), 3)
    await c.turn_runs.stop(r, "msg-123456789abc")
    state = await c.sessions.load_state(r.state.meta.id)
    assert len(state.scenes) == 1 and state.scenes[0].turn_end is None
    assert state.groups[0].status == "active"
    gate.set(); await job
    assert len(r.state.scenes) == 1 and not engine.contexts
    result = await c.turn_runs.resume(r, "msg-123456789abc")
    assert result["turn_run"]["status"] == "completed" and len(r.state.scenes) == 2
    await c.aclose()


@pytest.mark.asyncio
async def test_proactive_choice_is_not_redecided_after_restart(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path)
    class Judge:
        decisions = 0
        followups = 0
        def decide(self, data):
            self.decisions += 1
            return {"action": "pick_speaker", "chosen": ["a", "b"]}
        def judge_followup(self, data):
            self.followups += 1
            return {"speak": True, "chosen": "c"}
    judge = Judge()
    r.director_judge, r.state.meta.director_mode = judge, "auto"
    r.state.meta.proactive_turn_limit = 1
    r.state.character("c").followup_enabled = True
    engine.fail_actor = "c"
    result = await c.turn_runs.send(r, req(mentions=[], reply_mode=None))
    assert result["turn_run"]["status"] == "failed"
    assert [m.actor for m in r.state.messages] == ["player", "a"]
    engine.fail_actor = None
    result = await c.turn_runs.resume(r, "msg-123456789abc")
    assert result["turn_run"]["status"] == "completed"
    assert [m.actor for m in r.state.messages] == ["player", "a", "c", "b"]
    assert judge.decisions == judge.followups == 1
    assert [ctx.character_id for ctx in engine.contexts] == ["a", "c", "c", "b"]
    await c.aclose()


@pytest.mark.asyncio
async def test_reply_checkpoint_failure_preserves_paid_usage_and_retry_gap(tmp_path, monkeypatch):
    c, r, engine, _, _ = await rig(tmp_path)
    persist, failed = c.persist_session, False
    async def transient(runner):
        nonlocal failed
        if not failed and any(m.actor == "a" for m in runner.state.messages):
            failed = True
            raise OSError("synthetic one-shot checkpoint failure")
        await persist(runner)
    monkeypatch.setattr(c, "persist_session", transient)
    result = await c.turn_runs.send(r, req(mentions=["a", "b"]))
    assert result["turn_run"]["status"] == "failed"
    assert [m.actor for m in r.state.messages] == ["player"]
    assert len(r.state.usage_records) == 1
    state = await c.sessions.load_state(r.state.meta.id)
    assert len(state.usage_records) == 1 and [m.actor for m in state.messages] == ["player"]
    result = await c.turn_runs.resume(r, "msg-123456789abc")
    assert result["turn_run"]["status"] == "completed"
    assert [m.actor for m in r.state.messages] == ["player", "a", "b"]
    assert [ctx.character_id for ctx in engine.contexts] == ["a", "a", "b"]
    assert len(r.state.usage_records) == 3
    await c.aclose()


@pytest.mark.asyncio
async def test_snapshot_during_reply_save_exposes_pending_not_uncommitted_final(tmp_path, monkeypatch):
    from mrp.server.deps import state_dict
    c, r, engine, _, _ = await rig(tmp_path)
    entered, gate = asyncio.Event(), asyncio.Event()
    persist = c.persist_session
    async def delayed(runner):
        if any(m.actor == "a" for m in runner.state.messages):
            entered.set()
            await gate.wait()
        await persist(runner)
    monkeypatch.setattr(c, "persist_session", delayed)
    job = asyncio.create_task(c.turn_runs.send(r, req(mentions=["a"])))
    await asyncio.wait_for(entered.wait(), 3)
    assert [m.actor for m in r.state.messages] == ["player"]
    projected = state_dict(r)
    pending = next(message for message in projected["messages"] if message["actor"] == "a")
    assert pending["status"] == "pending"
    assert projected["turn_runs"][0]["slots"][0]["status"] == "pending"
    gate.set()
    result = await job
    assert result["turn_run"]["status"] == "completed"
    assert r.state.messages[-1].status == "final"
    await c.aclose()


@pytest.mark.asyncio
async def test_snapshot_during_input_save_never_acknowledges_unaccepted_input(tmp_path, monkeypatch):
    from mrp.server.deps import state_dict
    c, r, engine, _, _ = await rig(tmp_path)
    entered, gate = asyncio.Event(), asyncio.Event()
    persist, delayed_once = c.persist_session, False
    async def delayed(runner):
        nonlocal delayed_once
        if not delayed_once and runner.state.messages and not runner.state.turn_runs[0].slots:
            delayed_once = True
            entered.set()
            await gate.wait()
        await persist(runner)
    monkeypatch.setattr(c, "persist_session", delayed)
    job = asyncio.create_task(c.turn_runs.send(r, req(mentions=["a"])))
    await asyncio.wait_for(entered.wait(), 3)
    assert state_dict(r)["messages"] == []
    assert state_dict(r)["turn_runs"] == []
    assert not engine.contexts
    gate.set()
    result = await job
    assert result["turn_run"]["status"] == "completed"
    assert [m.actor for m in r.state.messages] == ["player", "a"]
    await c.aclose()
