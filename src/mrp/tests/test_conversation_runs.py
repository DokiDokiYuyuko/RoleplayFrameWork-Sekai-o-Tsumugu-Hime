"""Only synthetic stories/models: durable finite conversations and cancellation races."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from mrp.tests.conftest import make_test_container
from mrp.tests.test_targeted_regeneration import setup, request
from mrp.orchestrator.conversation_context import ConversationConflict
from mrp.orchestrator.conversation_selector import ScriptedConversationSelector, Selection, ConversationSelector
from mrp.orchestrator.generation_sources import generation_baseline, make_provenance, recompute_dependency_state
from mrp.orchestrator.message_regeneration import RegenerationConflict
from mrp.orchestrator.model_capacity import ModelCapacity
from mrp.orchestrator.scene_frame import build_shared_scene_frame, recent_public_scene_context
from mrp.shared.models import Message, ConversationRun, TokenUsage, GenerationMeta, SessionState
from mrp.shared.player_identity import ensure_player_identity
from mrp.server.app import create_app


async def rig(tmp_path, script, *, group=False, runner_cache=4):
    container = make_test_container(tmp_path, runner_cache=runner_cache)
    runner, engine, sink, responder = setup(group=group)
    ensure_player_identity(runner.state)
    runner.app_settings = None  # synthetic engines need no model-catalog network
    container.conversation_runs.attach(runner)
    container.conversation_runs.selector = ScriptedConversationSelector(script, TokenUsage(input_tokens=2, output_tokens=1))
    await container._register_runner(runner)
    await container.persist_session(runner)
    return container, runner, engine, sink, responder


async def start(container, runner, **changes):
    args = dict(operation_id="operation", participant_ids=["a", "b"], max_replies=6,
        expected_branch_revision=runner.state.meta.branch_revision,
        expected_player_identity_id=runner.state.meta.player_identity_id)
    args.update(changes)
    return await container.conversation_runs.start(runner, **args)


async def finish(container, runner, run):
    task = container.conversation_runs.tasks.get((runner.state.meta.id, run.id))
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 3)
    return await container.conversation_runs.get(runner, run.id)


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), 3)


@pytest.mark.asyncio
async def test_dynamic_repetition_real_triggers_audit_and_durable_prefix(tmp_path):
    c, r, engine, sink, _ = await rig(tmp_path, ["a", "b", "a", "stop"])
    run = await start(c, r)
    run = await finish(c, r, run)
    assert run.status == "completed" and run.stop_reason == "natural_stop"
    assert [m.actor for m in r.state.messages] == ["a", "b", "a"]
    assert not any(m.actor == "player" for m in r.state.messages)
    assert [ctx.reply_frame.mode for ctx in engine.contexts] == ["free"] * 3
    assert engine.contexts[1].reply_frame.trigger_message_ids == [r.state.messages[0].id]
    assert engine.contexts[2].reply_frame.trigger_message_ids == [r.state.messages[1].id]
    assert len({s.generation_id for s in run.steps}) == 3
    assert len({s.id for s in run.steps}) == 3
    assert run.cumulative_usage.input_tokens == 30 + 8
    assert run.cumulative_usage.output_tokens == 12 + 4
    restored = await c.sessions.load_state(r.state.meta.id)
    assert restored.conversation_runs[0].completed_replies == 3
    assert restored.conversation_runs[0].last_committed_message_id == restored.messages[-1].id
    assert all(m.generation_meta.provenance.run_id == run.id for m in restored.messages)
    assert all(r.runtime.inspections.generation(m.id, m.generation_id) for m in r.state.messages)
    await c.aclose()


@pytest.mark.asyncio
async def test_completed_can_continue_new_segment_preserving_counts_usage(tmp_path):
    c, r, _, _, _ = await rig(tmp_path, ["a", "b", "a", "b"])
    run = await finish(c, r, await start(c, r, max_replies=2))
    old_usage = run.cumulative_usage.input_tokens
    run = await c.conversation_runs.resume(r, run.id, expected_branch_revision=r.state.meta.branch_revision,
        expected_player_identity_id=r.state.meta.player_identity_id, additional_replies=2)
    run = await finish(c, r, run)
    assert (run.completed_replies, run.max_replies, len(run.steps)) == (4, 4, 4)
    assert run.cumulative_usage.input_tokens == old_usage * 2
    assert run.stop_reason == "max_replies"
    await c.aclose()


@pytest.mark.asyncio
async def test_committed_segment_keeps_memory_policy_outside_scheduler_usage_context(tmp_path, monkeypatch):
    c, r, _, _, _ = await rig(tmp_path, ["a", "stop"])
    from mrp.orchestrator.conversation_context import owner, usage_hook
    calls = []
    def schedule(turn, reason):
        calls.append((turn, reason, len(r.state.messages), owner.get(), usage_hook.get()))
    monkeypatch.setattr(r.memory_pipeline, "maybe_spawn_consolidation", schedule)
    run = await finish(c, r, await start(c, r))
    assert calls == [(run.turn, "interval", 1, None, None)]
    c.conversation_runs.selector.script = ["stop"]
    run = await finish(c, r, await start(c, r, operation_id="empty-segment"))
    assert run.completed_replies == 0 and len(calls) == 1
    await c.aclose()


@pytest.mark.asyncio
async def test_idempotent_start_one_job_and_wrong_payload_conflict(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a"])
    gate, entered = asyncio.Event(), asyncio.Event()
    async def blocked(*args): entered.set(); await gate.wait()
    engine.on_generate = blocked
    run = await start(c, r, max_replies=1)
    await entered.wait()
    again = await start(c, r, max_replies=1)
    assert run.id == again.id and len(c.conversation_runs.tasks) == 1
    with pytest.raises(ConversationConflict): await start(c, r, max_replies=2)
    gate.set()
    await finish(c, r, run)
    assert len(r.state.messages) == 1
    await c.aclose()


@pytest.mark.asyncio
async def test_pause_nonblocking_complete_boundary_intervene_resume(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a", "b"])
    gate, entered = asyncio.Event(), asyncio.Event()
    async def blocked(*args): entered.set(); await gate.wait()
    engine.on_generate = blocked
    run = await start(c, r)
    await entered.wait()
    response = await asyncio.wait_for(c.conversation_runs.pause(r, run.id), .2)
    assert response.pause_requested and response.status == "running" and r.busy()
    with pytest.raises(ConversationConflict): await r.player_say("cannot insert during generation")
    gate.set()
    run = await finish(c, r, run)
    assert run.status == "paused" and run.completed_replies == 1 and not r.busy()
    engine.on_generate = None
    await r.player_say("synthetic intervention", mentions=["b"], channel="inner")
    await c.persist_session(r)
    run = await c.conversation_runs.resume(r, run.id, expected_branch_revision=r.state.meta.branch_revision,
        expected_player_identity_id=r.state.meta.player_identity_id, additional_replies=1)
    run = await finish(c, r, run)
    assert run.completed_replies == 2 and run.max_replies == 2
    assert "synthetic intervention" in engine.contexts[-1].planned_prompt.text  # existing individual emotion-subtext rule
    assert all(m.kind != "inner" for data in c.conversation_runs.selector.calls for m in data["public_messages"])
    await c.aclose()


@pytest.mark.asyncio
async def test_stop_discards_and_rejects_cancellation_resistant_late_reply(tmp_path):
    c, r, engine, sink, _ = await rig(tmp_path, ["a"])
    entered, canceled, gate = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def ignores_cancellation(*args):
        entered.set()
        try: await gate.wait()
        except asyncio.CancelledError: canceled.set(); await gate.wait()
    engine.on_generate = ignores_cancellation
    run = await start(c, r)
    await entered.wait()
    task = c.conversation_runs.tasks[(r.state.meta.id, run.id)]
    run = await asyncio.wait_for(c.conversation_runs.stop(r, run.id), .2)
    await canceled.wait()
    assert run.status == "cancelled" and run.usage_incomplete
    assert run.steps[-1].status == "cancelled"
    retired = run.current_generation_id
    gate.set()
    await task
    assert not r.state.messages
    assert not any(e in {"message.delta", "message.final"} and p.get("generation_id") == retired for e, p in sink.events)
    error = next(p for e, p in sink.events if e == "message.error" and p.get("discard_pending"))
    assert error["generation_id"] == retired and error["attempt_id"] == run.current_attempt_id
    assert (await c.conversation_runs.get(r, run.id)).cumulative_usage.input_tokens == 12
    restored = await c.sessions.load_state(r.state.meta.id)
    assert restored.conversation_runs[0].status == "cancelled" and not restored.messages
    await c.aclose()


@pytest.mark.asyncio
async def test_failed_stop_save_preserves_running_job_and_pending(tmp_path, monkeypatch):
    c, r, engine, sink, _ = await rig(tmp_path, ["a"])
    entered, gate = asyncio.Event(), asyncio.Event()
    async def block(*args): entered.set(); await gate.wait()
    engine.on_generate = block
    run = await start(c, r, max_replies=1)
    await entered.wait()
    old = r.state.meta.branch_revision
    original = c.persist_session
    async def fail_once(runner):
        runner.state.meta.branch_revision += 1
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(c, "persist_session", fail_once)
    with pytest.raises(OSError): await c.conversation_runs.stop(r, run.id)
    assert r.state.meta.branch_revision == old
    assert (await c.conversation_runs.get(r, run.id)).status == "running" and r.busy()
    assert not any(e == "message.error" for e, _ in sink.events)
    assert not r.runtime.retired_conversation_generations
    monkeypatch.setattr(c, "persist_session", original)
    gate.set()
    run = await finish(c, r, run)
    assert run.status == "completed" and len(r.state.messages) == 1
    await c.aclose()


@pytest.mark.asyncio
async def test_save_failure_reverts_uncommitted_story_and_revision_keeps_known_usage(tmp_path, monkeypatch):
    c, r, _, sink, _ = await rig(tmp_path, ["a", "b"])
    original = c.persist_session
    failed_revision = None
    async def fail_second(runner):
        nonlocal failed_revision
        if len(runner.state.messages) == 2:
            failed_revision = runner.state.meta.branch_revision
            runner.state.meta.branch_revision += 1
            raise OSError("synthetic checkpoint failure")
        await original(runner)
    monkeypatch.setattr(c, "persist_session", fail_second)
    run = await finish(c, r, await start(c, r))
    assert run.status == "failed" and run.completed_replies == 1
    assert len(r.state.messages) == 1 and run.steps[-1].status == "failed"
    assert run.cumulative_usage.input_tokens == 24
    assert r.state.meta.branch_revision == failed_revision + 1  # only successful failed-state checkpoint
    finals = [p for e, p in sink.events if e == "message.final"]
    assert len(finals) == 1
    restored = await c.sessions.load_state(r.state.meta.id)
    assert len(restored.messages) == 1 and restored.conversation_runs[0].status == "failed"
    await c.aclose()


@pytest.mark.asyncio
async def test_failure_known_usage_keeps_prefix_and_retry_new_generation(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a", "b"])
    async def fail(actor, ctx):
        if actor.id == "b":
            error = RuntimeError("synthetic billed failure")
            error.usage = {"input_tokens": 7, "output_tokens": 2, "cached_tokens": 0}
            raise error
    engine.on_generate = fail
    run = await finish(c, r, await start(c, r))
    assert run.status == "failed" and run.cumulative_usage.input_tokens == 10 + 7 + 4
    assert not run.usage_incomplete
    assert len(r.state.messages) == 1
    old_gen = run.steps[-1].generation_id
    engine.on_generate = None
    c.conversation_runs.selector.script = ["b"]
    run = await c.conversation_runs.resume(r, run.id, expected_branch_revision=r.state.meta.branch_revision,
        expected_player_identity_id=r.state.meta.player_identity_id, additional_replies=1)
    run = await finish(c, r, run)
    assert run.completed_replies == 2 and run.steps[-1].generation_id != old_gen
    await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["selecting", "generating"])
async def test_provider_failure_unknown_usage_keeps_confirmed_prefix_and_marks_incomplete(tmp_path, phase):
    script = ["a", RuntimeError("synthetic selector disconnected")] if phase == "selecting" else ["a", "b"]
    c, r, engine, _, _ = await rig(tmp_path, script)
    if phase == "generating":
        engine.fail_actor = "b"
    run = await finish(c, r, await start(c, r))
    assert run.status == "failed" and run.usage_incomplete
    assert run.completed_replies == 1 and len(r.state.messages) == 1
    assert run.cumulative_usage.input_tokens == (12 if phase == "selecting" else 14)
    restored = await c.sessions.load_state(r.state.meta.id)
    assert restored.conversation_runs[0].usage_incomplete
    await c.aclose()


@pytest.mark.asyncio
async def test_selecting_owner_blocks_mutations_and_lru_eviction(tmp_path):
    gate, entered = asyncio.Event(), asyncio.Event()
    async def selecting(data): entered.set(); await gate.wait(); return "a"
    c, r, _, _, _ = await rig(tmp_path, [selecting], runner_cache=1)
    seed = r._append_message(Message(session_id=r.state.meta.id, seq=0, turn=0, actor="a", content="synthetic old answer"))
    seed.generation_meta = GenerationMeta(provenance=make_provenance(r.state))
    run = await start(c, r, max_replies=1)
    await entered.wait()
    assert r.runtime.turn_lock.locked() and r.busy()
    with pytest.raises(ConversationConflict): await r.set_presence("a", False)
    with pytest.raises(RegenerationConflict): await r.regeneration.run("one", seed.id, request(r, seed))
    other, _, _, _ = setup()
    other.state.meta.id = "synthetic-other"
    await c._register_runner(other)
    assert r.state.meta.id in c.runners
    gate.set()
    await finish(c, r, run)
    await c.aclose()


@pytest.mark.asyncio
async def test_shared_selector_permissions_control_joins_stale_and_reply_sources(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, [])
    s = r.state
    rows = [("public", "all", None, False), ("private", ["a"], None, False),
            ("unknown-b", "all", ["a"], False), ("stale", "all", None, True)]
    for i, (mid, visible, known, stale) in enumerate(rows):
        s.messages.append(Message(id=mid, session_id=s.meta.id, seq=i, turn=0, actor="a", content=mid,
            scene_id=s.active_scene_id, visible_to=visible, known_to=known, dependency_stale=stale))
    c.conversation_runs.selector.script = [Selection("speak", "b", reply_to_message_ids=["public"])]
    run = await finish(c, r, await start(c, r, max_replies=1))
    public = c.conversation_runs.selector.calls[0]["public_messages"]
    assert [m.id for m in public] == ["public"]
    assert engine.contexts[0].reply_frame.reply_to_message_ids == ["public"]
    provenance = s.messages[-1].generation_meta.provenance
    assert [x.message_id for x in provenance.scheduling_sources] == ["public"]
    assert "unknown-b" not in [x.message_id for x in provenance.sources]
    s.character_joined_at_seq["b"] = 9
    assert not c.conversation_runs.shared_messages(s, c.conversation_runs._candidates(r, ["a", "b"]))
    identity = next(x for x in s.player_identities if x.id == s.meta.player_identity_id)
    identity.person_id = "a"
    with pytest.raises(ConversationConflict): await start(c, r, operation_id="controlled")
    await c.aclose()


@pytest.mark.asyncio
async def test_group_and_character_free_share_kernel_without_future_or_private(tmp_path):
    c, r, engine, _, responder = await rig(tmp_path, ["group", "a", "group"], group=True)
    run = await finish(c, r, await start(c, r, participant_ids=["a", "group"], max_replies=3, directive="作者希望聊聊洞穴"))
    assert run.status == "completed" and [m.actor for m in r.state.messages] == ["group", "a", "group"]
    assert len(responder.contexts[0].messages) == 0
    assert [m.actor for m in responder.contexts[1].messages] == ["group", "a"]
    assert "作者希望聊聊洞穴" in engine.contexts[0].planned_prompt.text
    assert not any(m.actor in {"player", "director"} for m in r.state.messages)
    await c.aclose()


@pytest.mark.asyncio
async def test_restart_checkpoint_is_persisted_no_auto_replay_and_foreign_run_no_resume(tmp_path):
    c, r, _, _, _ = await rig(tmp_path, [])
    run = ConversationRun(session_id=r.state.meta.id, operation_id="restored", participant_ids=["a"],
        scene_id=r.state.active_scene_id, player_identity_id=r.state.meta.player_identity_id, status="running",
        current_step_id="unfinished", current_message_id="draft", current_generation_id="gen-old", current_speaker_id="a")
    r.state.conversation_runs.append(run)
    r.state.messages.append(Message(id="draft", session_id=r.state.meta.id, seq=0, turn=1, actor="a", content="unfinished", status="pending"))
    await c.persist_session(r)
    c.runners.pop(r.state.meta.id)
    restored = await c.load_session(r.state.meta.id)
    assert restored.state.conversation_runs[0].status == "interrupted" and not restored.state.messages
    disk = await c.sessions.load_state(r.state.meta.id)
    assert disk.conversation_runs[0].status == "interrupted" and not disk.messages
    assert not c.conversation_runs.tasks
    clone = restored.state.model_copy(deep=True)
    clone.meta.id = "synthetic-child"
    child = await c.restore_runner(clone)
    with pytest.raises(ConversationConflict):
        await c.conversation_runs.resume(child, run.id, expected_branch_revision=child.state.meta.branch_revision,
            expected_player_identity_id=child.state.meta.player_identity_id)
    await c.aclose()


@pytest.mark.asyncio
async def test_free_http_contract_and_real_seed_no_duplicate_start(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a", "b", "a"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c), client=("127.0.0.1", 12345)),
                                 base_url="http://127.0.0.1", headers={"Origin": "http://127.0.0.1"}) as client:
        req = {"content": "synthetic player dialogue", "mentions": ["a", "b"], "reply_mode": "free", "max_replies": 3,
            "client_message_id": "msg-123456abcdef", "expected_player_identity_id": r.state.meta.player_identity_id,
            "conversation_directive": "导演要求：讨论公开火把，受控人物不自动行动"}
        response = await client.post(f"/api/v1/sessions/{r.state.meta.id}/messages", json=req)
        assert response.status_code == 200, response.text
        dto = response.json()
        assert [m["actor"] for m in dto["messages"]] == ["player", "a", "b", "a"]
        assert dto["conversation_run"]["completed_replies"] == 3
        assert all(req["conversation_directive"] in ctx.planned_prompt.text for ctx in engine.contexts)
        assert [m["content"] for m in dto["messages"] if m["actor"] == "player"] == [req["content"]]
        again = await client.post(f"/api/v1/sessions/{r.state.meta.id}/messages", json=req)
        assert again.status_code == 200 and len(r.state.messages) == 4
        rid = dto["conversation_run"]["id"]
        get = await client.get(f"/api/v1/sessions/{r.state.meta.id}/conversation-runs/{rid}")
        assert get.status_code == 200 and get.json()["id"] == rid
        assert (await client.get(f"/api/v1/sessions/{r.state.meta.id}")).json()["conversation_runs"][0]["id"] == rid
    await c.aclose()


@pytest.mark.asyncio
async def test_disconnected_free_request_does_not_cancel_service_run(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a"])
    entered, gate = asyncio.Event(), asyncio.Event()
    async def blocked(*args): entered.set(); await gate.wait()
    engine.on_generate = blocked
    req = SimpleNamespace(content="synthetic seed", channel="dialogue", mentions=["a"], force_character=None,
        client_message_id="msg-123456abcdef", expected_player_identity_id=r.state.meta.player_identity_id, max_replies=1)
    request_task = asyncio.create_task(c.conversation_runs.send_free(r, req))
    await entered.wait()
    request_task.cancel()
    with pytest.raises(asyncio.CancelledError): await request_task
    assert r.busy()
    gate.set()
    run = await finish(c, r, r.state.conversation_runs[0])
    assert run.status == "completed" and run.completed_replies == 1
    await c.aclose()


@pytest.mark.asyncio
async def test_selector_capacity_provider_thinking_partial_trace_and_multi_reply(tmp_path, monkeypatch):
    c, r, _, _, _ = await rig(tmp_path, [])
    c.settings.auxiliary_model = "synthetic-aux"
    c.settings.gateway = "https://openrouter.ai/api/v1"
    c.settings.auxiliary_provider = "synthetic-provider"
    c.settings.thinking = "on"
    async def capacity(settings, model, **kwargs):
        assert model == "synthetic-aux" and settings.model_provider == "synthetic-provider"
        return ModelCapacity(4000, 1500, 600, "synthetic")
    monkeypatch.setattr("mrp.orchestrator.model_capacity.resolve_model_capacity", capacity)
    def fake_chat(prompt, cfg, **kwargs):
        assert cfg.provider == "synthetic-provider" and cfg.sampling["reasoning"] == {"effort": "low"}
        assert "调度材料节录" in prompt[-1]["content"]
        return json.dumps({"action": "speak", "speaker_id": "a", "reply_to_message_ids": ["long"]}), {"input_tokens": 30, "output_tokens": 9}
    monkeypatch.setattr("mrp.orchestrator.conversation_selector.chat_text_with_usage", fake_chat)
    public = [Message(id="long", session_id=r.state.meta.id, seq=0, turn=0, actor="b", content="合成很长的对白" * 9000)]
    selector = ConversationSelector(c)
    run = ConversationRun(session_id=r.state.meta.id, operation_id="selector", scene_id=r.state.active_scene_id)
    selected = await selector.select(state=r.state, run=run, candidates={"a": r.state.characters[0]}, public_messages=public)
    assert selected.reply_to_message_ids == ["long"] and selected.trace["shortened_message_ids"] == ["long"]
    await c.aclose()


def test_stale_public_scene_and_legacy_missing_snapshot_rejected():
    r, _, _, _ = setup()
    target = Message(session_id=r.state.meta.id, seq=1, turn=1, actor="a", content="stale scene fact",
                     scene_id=r.state.active_scene_id, dependency_stale=True)
    r.state.messages.append(target)
    assert "stale scene fact" not in build_shared_scene_frame(r.state, 1)
    assert "stale scene fact" not in recent_public_scene_context(r.state, 1)
    with pytest.raises(RegenerationConflict, match="历史材料快照"):
        generation_baseline(r.state, target)


@pytest.mark.asyncio
async def test_compressed_history_sources_keep_original_visible_dependency_not_private(tmp_path, monkeypatch):
    c, r, engine, _, _ = await rig(tmp_path, ["a"])
    old = Message(id="old-public", session_id=r.state.meta.id, seq=0, turn=0, actor="b", content="original public source", scene_id="old")
    private = Message(id="old-private", session_id=r.state.meta.id, seq=1, turn=0, actor="b", content="private secret", scene_id="old", visible_to=["b"])
    r.state.messages.extend([old, private])
    summary = Message(id="summary-old", session_id=r.state.meta.id, seq=0, turn=0, actor="director", kind="scene", content="synthetic summary")
    monkeypatch.setattr(r.context_builder, "compress_history", lambda *args, **kwargs: [summary])
    run = await finish(c, r, await start(c, r, max_replies=1, participant_ids=["a"]))
    assert run.completed_replies == 1
    generated = r.state.messages[-1]
    sources = [ref.message_id for ref in generated.generation_meta.provenance.sources]
    assert "old-public" in sources and "old-private" not in sources and "summary-old" not in sources
    old.content = "changed original"
    from mrp.shared.models import fingerprint
    old.fingerprint = fingerprint(old.actor, old.seq, old.content)
    recompute_dependency_state(r.state)
    assert generated.dependency_stale
    await c.aclose()


@pytest.mark.asyncio
async def test_pause_resume_old_epoch_finally_cannot_clear_new_owner(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a", "b"])
    old = await start(c, r, max_replies=1)
    old_task = c.conversation_runs.tasks[(r.state.meta.id, old.id)]
    next_entered, next_gate = asyncio.Event(), asyncio.Event()
    async def generation(actor, ctx):
        if actor.id == "b": next_entered.set(); await next_gate.wait()
    engine.on_generate = generation
    # Observe the boundary before the old task's next scheduling yield/finally.
    await until(lambda: r.state.conversation_runs[0].status == "completed" and not r.busy())
    assert not old_task.done()
    resumed = await c.conversation_runs.resume(r, old.id, expected_branch_revision=r.state.meta.branch_revision,
        expected_player_identity_id=r.state.meta.player_identity_id, additional_replies=1)
    new_task = c.conversation_runs.tasks[(r.state.meta.id, old.id)]
    assert new_task is not old_task
    await old_task
    await next_entered.wait()
    assert r.runtime.active_conversation_run_id == resumed.id and r.busy()
    with pytest.raises(ConversationConflict): await r.player_say("cannot insert")
    next_gate.set()
    await new_task
    assert r.state.conversation_runs[0].completed_replies == 2
    await c.aclose()


@pytest.mark.asyncio
async def test_stop_during_second_attempt_error_matches_latest_pending(tmp_path):
    c, r, engine, sink, _ = await rig(tmp_path, ["a"])
    # A known previous candidate causes the existing duplicate-output retry.
    engine.on_generate = None
    old = await r.turns.run_character_turn(r.state.characters[0], 0)
    old.content = "这是一段用来触发旧回复重复检查的纯合成对白与行动记录。" * 15
    from mrp.shared.models import fingerprint
    old.fingerprint = fingerprint(old.actor, old.seq, old.content)
    entered, gate = asyncio.Event(), asyncio.Event()
    # A deterministic custom engine avoids depending on prompt quality heuristics.
    count = 0
    original = engine.generate
    async def generate(actor, ctx, *, on_delta=None):
        nonlocal count
        count += 1
        if count == 1:
            from mrp.shared.models import EngineReply
            return EngineReply(content=old.content, usage=TokenUsage(input_tokens=5, output_tokens=2))
        entered.set(); await gate.wait()
        return await original(actor, ctx, on_delta=on_delta)
    engine.generate = generate
    run = await start(c, r, participant_ids=["a"], max_replies=1)
    await asyncio.wait_for(entered.wait(), 3)
    pending = [p for e, p in sink.events if e == "message.pending" and p.get("operation_id") == "operation"]
    assert len(pending) == 2 and pending[0]["attempt_id"] != pending[1]["attempt_id"]
    run = await c.conversation_runs.stop(r, run.id)
    await finish(c, r, run)
    error = [p for e, p in sink.events if e == "message.error" and p.get("operation_id") == "operation"][-1]
    assert error["attempt_id"] == pending[-1]["attempt_id"]
    assert run.steps[-1].attempt_id == pending[-1]["attempt_id"]
    assert len(r.state.messages) == 1 and run.completed_replies == 0
    assert run.cumulative_usage.input_tokens == 7
    await c.aclose()


@pytest.mark.asyncio
async def test_free_synchronous_request_stop_returns_prefix_and_cancelled_run(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a"])
    entered, gate = asyncio.Event(), asyncio.Event()
    async def blocked(*args): entered.set(); await gate.wait()
    engine.on_generate = blocked
    req = SimpleNamespace(content="synthetic seed", channel="dialogue", mentions=["a"], force_character=None,
        client_message_id="msg-123456abcdef", expected_player_identity_id=r.state.meta.player_identity_id, max_replies=1)
    request_task = asyncio.create_task(c.conversation_runs.send_free(r, req))
    await entered.wait()
    await c.conversation_runs.stop(r, r.state.conversation_runs[0].id)
    dto = await request_task
    assert dto["conversation_run"]["status"] == "cancelled"
    assert [m["actor"] for m in dto["messages"]] == ["player"]
    await c.aclose()


@pytest.mark.asyncio
async def test_get_during_step_persist_never_exposes_uncommitted_reply(tmp_path, monkeypatch):
    c, r, _, sink, _ = await rig(tmp_path, ["a"])
    original = c.persist_session
    entered, gate = asyncio.Event(), asyncio.Event()
    async def held_commit(runner):
        if runner.state.messages:
            entered.set(); await gate.wait()
        await original(runner)
    monkeypatch.setattr(c, "persist_session", held_commit)
    run = await start(c, r, max_replies=1)
    await entered.wait()
    assert not r.state.messages and r.state.conversation_runs[0].completed_replies == 0
    assert not any(e == "message.final" for e, _ in sink.events)
    disk = await c.sessions.load_state(r.state.meta.id)
    assert not disk.messages and disk.conversation_runs[0].completed_replies == 0
    gate.set()
    run = await finish(c, r, run)
    assert run.completed_replies == 1 and len(r.state.messages) == 1
    await c.aclose()


@pytest.mark.asyncio
async def test_observe_http_start_pause_stop_and_conflicts(tmp_path):
    entered, gate = asyncio.Event(), asyncio.Event()
    async def selecting(data): entered.set(); await gate.wait(); return "a"
    c, r, _, _, _ = await rig(tmp_path, [selecting])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c), client=("127.0.0.1", 12345)),
                                 base_url="http://127.0.0.1", headers={"Origin": "http://127.0.0.1"}) as client:
        url = f"/api/v1/sessions/{r.state.meta.id}/conversation-runs"
        req = {"operation_id": "http-observe", "participant_ids": ["a", "b"], "max_replies": 6,
            "expected_branch_revision": r.state.meta.branch_revision, "expected_player_identity_id": r.state.meta.player_identity_id}
        started = await client.post(url, json=req)
        assert started.status_code == 200
        rid = started.json()["id"]
        await entered.wait()
        assert (await client.post(url, json=req)).json()["id"] == rid
        assert (await client.post(f"/api/v1/sessions/{r.state.meta.id}/messages", json={"content": "blocked"})).status_code == 409
        assert (await client.patch("/api/v1/settings", json={"auxiliary_model": "changed"})).status_code == 409
        pause = await client.post(f"{url}/{rid}/pause", json={})
        assert pause.status_code == 200 and pause.json()["pause_requested"]
        stopped = await client.post(f"{url}/{rid}/stop", json={})
        assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
        assert (await client.get(url)).json()["runs"][0]["id"] == rid
    await c.aclose()


@pytest.mark.asyncio
async def test_interrupted_resume_keeps_committed_prefix_and_no_replay(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a", "b"])
    entered, gate = asyncio.Event(), asyncio.Event()
    async def blocked(actor, ctx):
        if actor.id == "b": entered.set(); await gate.wait()
    engine.on_generate = blocked
    run = await start(c, r)
    await entered.wait()
    sid, rid = r.state.meta.id, run.id
    await c.conversation_runs.close()
    c.runners.pop(sid)
    restored = await c.load_session(sid)
    restored.app_settings = None
    restored.engines = engine
    engine.on_generate = None
    c.conversation_runs.selector.script = ["b"]
    run = await c.conversation_runs.resume(restored, rid, expected_branch_revision=restored.state.meta.branch_revision,
        expected_player_identity_id=restored.state.meta.player_identity_id, additional_replies=1)
    run = await finish(c, restored, run)
    assert run.completed_replies == 2 and [m.actor for m in restored.state.messages] == ["a", "b"]
    assert len([s for s in run.steps if s.status == "committed"]) == 2
    assert len({s.generation_id for s in run.steps}) == 3
    await c.aclose()


@pytest.mark.asyncio
async def test_awaiting_user_and_resume_scene_control_checks_no_scene_start(tmp_path):
    c, r, _, _, _ = await rig(tmp_path, ["needs_user"])
    run = await finish(c, r, await start(c, r))
    assert run.status == "awaiting_user" and not r.state.messages
    r.state.active_scene_id = None
    with pytest.raises(ConversationConflict, match="场景"):
        await c.conversation_runs.resume(r, run.id, expected_branch_revision=r.state.meta.branch_revision,
            expected_player_identity_id=r.state.meta.player_identity_id)
    with pytest.raises(ConversationConflict, match="场景"):
        await start(c, r, operation_id="no-scene")
    r.state.active_scene_id = "scene"
    r.state.meta.player_identity_id = "changed-control"
    with pytest.raises(ConversationConflict, match="场景或控制身份"):
        await c.conversation_runs.resume(r, run.id, expected_branch_revision=r.state.meta.branch_revision,
            expected_player_identity_id=r.state.meta.player_identity_id)
    await c.aclose()


@pytest.mark.asyncio
async def test_presence_scoped_public_list_is_shared_but_actor_private_is_not(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["b"])
    r.state.character("c").present = False
    public = r._append_message(Message(id="presence-public", session_id=r.state.meta.id, seq=0, turn=0,
        actor="a", content="A和B共同听见的公开问题", visible_to=["a", "b", "player"]))
    private = r._append_message(Message(id="only-a", session_id=r.state.meta.id, seq=1, turn=0,
        actor="a", content="仅A知情", visible_to=["a"]))
    run = await finish(c, r, await start(c, r, max_replies=1))
    assert run.completed_replies == 1
    seen = c.conversation_runs.selector.calls[0]["public_messages"]
    assert [m.id for m in seen] == [public.id]
    assert private.id not in engine.contexts[0].reply_frame.trigger_message_ids
    assert "共同听见" in build_shared_scene_frame(r.state, r.state.current_turn(), viewer_ids=["b"])
    await c.aclose()


@pytest.mark.asyncio
async def test_selector_json_parse_failure_records_completed_request_usage(tmp_path, monkeypatch):
    c, r, _, _, _ = await rig(tmp_path, [])
    async def capacity(*args, **kwargs): return ModelCapacity(None, None, 600, "synthetic")
    monkeypatch.setattr("mrp.orchestrator.model_capacity.resolve_model_capacity", capacity)
    monkeypatch.setattr("mrp.orchestrator.conversation_selector.chat_text_with_usage",
        lambda *args, **kwargs: ("{synthetic malformed JSON", {"input_tokens": 51, "output_tokens": 7}))
    c.conversation_runs.selector = ConversationSelector(c)
    run = await finish(c, r, await start(c, r))
    assert run.status == "failed" and not r.state.messages
    assert run.cumulative_usage.input_tokens == 51 and run.cumulative_usage.output_tokens == 7
    assert "解析" in run.last_error
    await c.aclose()


@pytest.mark.asyncio
async def test_free_local_regeneration_restores_directive_frame_and_precise_reply_to(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a", "b", "a"])
    run = await finish(c, r, await start(c, r, max_replies=3, directive="作者要求聊聊当前洞穴；不让受控人物自动行动"))
    target = r.state.messages[1]
    before = target.generation_meta.provenance.model_copy(deep=True)
    await r.regeneration.run("one", target.id, request(r, target), persist=lambda: c.persist_session(r),
        rollback_persist=lambda: c.sessions.restore_state(r.state))
    ctx = engine.contexts[-1]
    assert ctx.reply_frame.mode == "free" and ctx.reply_frame.trigger_message_ids == before.trigger_message_ids
    assert ctx.reply_frame.reply_to_message_ids == before.reply_to_message_ids
    assert before.director_directive in ctx.planned_prompt.text
    assert ctx.provenance.director_directive == before.director_directive
    assert r.state.messages[-1].dependency_stale
    assert r.state.messages[-1].id not in [m.id for m in ctx.visible_messages]
    await c.aclose()


@pytest.mark.asyncio
async def test_group_shared_scene_before_join_sources_tracked_without_private(tmp_path, monkeypatch):
    c, r, _, _, _ = await rig(tmp_path, [], group=True)
    from mrp.orchestrator.group_actors import GroupResponder
    async def capacity(*args, **kwargs): return ModelCapacity(30000, 24000, 2000, "synthetic")
    monkeypatch.setattr("mrp.orchestrator.model_capacity.resolve_model_capacity", capacity)
    r.group_responder = GroupResponder(c)
    r.state.groups[0].joined_seq = 10
    public = Message(id="old-scene", session_id=r.state.meta.id, seq=0, turn=0, actor="director", kind="scene",
        content="公开当前洞穴里有火把", scene_id=r.state.active_scene_id, visible_to="all", known_to=["group", "a", "b", "c"])
    secret = Message(id="old-secret", session_id=r.state.meta.id, seq=1, turn=0, actor="a",
        content="人物私密线索", scene_id=r.state.active_scene_id, visible_to=["a"], known_to=["a"])
    r.state.messages.extend([public, secret])
    c.conversation_runs.selector.script = ["group"]
    run = await finish(c, r, await start(c, r, participant_ids=["group"], max_replies=1))
    assert run.completed_replies == 1
    generated = r.state.messages[-1]
    refs = [source.message_id for source in generated.generation_meta.provenance.sources]
    assert public.id in refs and secret.id not in refs
    assert generated.generation_meta.provenance.scheduling_sources == []  # joins exclude old selector history
    inspected = r.runtime.inspections.generation(generated.id, generated.generation_id)
    assert "火把" in inspected["composed"].text and "人物私密线索" not in inspected["composed"].text
    await c.aclose()


@pytest.mark.asyncio
async def test_snapshot_live_prefix_cursor_reconnect_expiry_and_stop_cleanup(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a"])
    r.sink = c.bus
    sid = r.state.meta.id
    c.bus.current_cursor(sid)
    first, second, end = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def streaming(actor, ctx, *, on_delta=None):
        from mrp.shared.models import EngineReply
        on_delta("合成前半段")
        first.set()
        await second.wait()
        on_delta("合成后半段")
        await end.wait()
        return EngineReply(content="合成前半段合成后半段", usage=TokenUsage(input_tokens=10, output_tokens=6))
    engine.generate = streaming
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c), client=("127.0.0.1", 12345)),
                                 base_url="http://127.0.0.1", headers={"Origin": "http://127.0.0.1"}) as client:
        run = await start(c, r, max_replies=1)
        await first.wait()
        await until(lambda: any(p["content"] == "合成前半段" for p in r.runtime.pending_messages.values()))
        response = (await client.get(f"/api/v1/sessions/{sid}")).json()
        pending = response["messages"][-1]
        assert pending["status"] == "pending" and pending["content"] == "合成前半段"
        assert pending["generation_id"] == response["conversation_runs"][0]["current_generation_id"]
        assert not r.state.messages and not (await c.sessions.load_state(sid)).messages
        cursor = response["event_cursor"]
        second.set()
        await until(lambda: any(p["content"] == "合成前半段合成后半段" for p in r.runtime.pending_messages.values()))
        queue, replay, resync = c.bus.subscribe_replay(sid, cursor)
        assert not resync and any(event["event"] == "message.delta" for event in replay)
        merged = pending["content"]
        for event in replay:
            if event["event"] == "message.delta":
                delta = json.loads(event["data"])
                assert delta["generation_id"] == pending["generation_id"]
                merged = merged[:delta["offset"]] + delta["delta"]
        assert merged == "合成前半段合成后半段"
        c.bus.unsubscribe(sid, queue)
        # Expired journals require resync; their new snapshot remains authoritative.
        c.bus._journals.pop(sid)
        queue, _, resync = c.bus.subscribe_replay(sid, cursor)
        assert resync
        c.bus.unsubscribe(sid, queue)
        fresh = (await client.get(f"/api/v1/sessions/{sid}")).json()
        assert fresh["messages"][-1]["content"] == merged
        stopped = await client.post(f"/api/v1/sessions/{sid}/conversation-runs/{run.id}/stop", json={})
        assert stopped.status_code == 200
        await finish(c, r, run)
        refreshed = (await client.get(f"/api/v1/sessions/{sid}")).json()
        assert refreshed["messages"] == [] and not r.runtime.pending_messages
        child = c._make_runner(r.state.model_copy(deep=True))
        child.state.meta.id = "foreign-pending-projection"
        from mrp.server.deps import state_dict
        assert state_dict(child)["messages"] == []
    await c.aclose()


@pytest.mark.asyncio
async def test_snapshot_retry_resets_live_prefix_and_committed_final_wins(tmp_path):
    c, r, _, _, _ = await rig(tmp_path, [])
    from mrp.server.deps import state_dict
    pending = Message(session_id=r.state.meta.id, seq=0, turn=1, actor="a", content="", status="pending",
        generation_id="synthetic-gen", operation_id="synthetic-op", attempt_id="first-attempt")
    r.turns.prepare_generation(pending)
    await r._emit("message.pending", {"message": pending.model_dump(mode="json")})
    await r._emit("message.delta", {"message_id": pending.id, "delta": "首版未完成", "offset": 0})
    assert state_dict(r)["messages"][0]["content"] == "首版未完成"
    await r.turns.announce_attempt(pending)
    snapshot = state_dict(r)
    assert snapshot["messages"][0]["content"] == "" and snapshot["messages"][0]["attempt_id"] == pending.attempt_id
    r._append_message(pending.model_copy(update={"status": "final", "content": "正式已提交正文"}))
    await c.persist_session(r)  # A final snapshot represents durable story content.
    assert state_dict(r)["messages"][0]["content"] == "正式已提交正文"
    await c.aclose()


@pytest.mark.asyncio
async def test_free_http_busy_bad_participant_and_control_return_conflicts(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a"])
    entered, gate = asyncio.Event(), asyncio.Event()
    async def blocked(*args): entered.set(); await gate.wait()
    engine.on_generate = blocked
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c), client=("127.0.0.1", 12345)),
                                 base_url="http://127.0.0.1", headers={"Origin": "http://127.0.0.1"}) as client:
        url = f"/api/v1/sessions/{r.state.meta.id}/messages"
        base = {"content": "synthetic player", "reply_mode": "free", "mentions": ["a"],
            "expected_player_identity_id": r.state.meta.player_identity_id}
        assert (await client.post(url, json={**base, "mentions": ["missing"]})).status_code == 409
        assert (await client.post(url, json={**base, "expected_player_identity_id": "old-control"})).status_code == 409
        assert (await client.post(url, json={**base, "channel": "inner", "expected_player_identity_id": "old-control"})).status_code == 409
        run = await start(c, r)
        await entered.wait()
        assert (await client.post(url, json=base)).status_code == 409
        await c.conversation_runs.stop(r, run.id)
        await finish(c, r, run)
    await c.aclose()


@pytest.mark.asyncio
async def test_repeat_pause_stop_idempotent_resume_retry_cas_prevents_extra_segment(tmp_path):
    c, r, engine, _, _ = await rig(tmp_path, ["a", "b"])
    entered, gate = asyncio.Event(), asyncio.Event()
    async def blocked(actor, ctx): entered.set(); await gate.wait()
    engine.on_generate = blocked
    run = await start(c, r, max_replies=1)
    await entered.wait()
    paused = await c.conversation_runs.pause(r, run.id)
    revision = r.state.meta.branch_revision
    again = await c.conversation_runs.pause(r, run.id)
    assert again.pause_requested and r.state.meta.branch_revision == revision
    gate.set()
    run = await finish(c, r, run)
    args = {"expected_branch_revision": r.state.meta.branch_revision,
        "expected_player_identity_id": r.state.meta.player_identity_id, "additional_replies": 1}
    engine.on_generate = None
    resumed = await c.conversation_runs.resume(r, run.id, **args)
    with pytest.raises(ConversationConflict): await c.conversation_runs.resume(r, run.id, **args)
    await finish(c, r, resumed)
    with pytest.raises(ConversationConflict): await c.conversation_runs.resume(r, run.id, **args)
    assert r.state.conversation_runs[0].completed_replies == 2
    stopped = await c.conversation_runs.stop(r, run.id)
    revision = r.state.meta.branch_revision
    repeated = await c.conversation_runs.stop(r, run.id)
    assert repeated.status == stopped.status == "cancelled" and r.state.meta.branch_revision == revision
    await c.aclose()
