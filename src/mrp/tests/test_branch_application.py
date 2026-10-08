"""Application boundaries tested only with synthetic states and fake engines."""
import asyncio
import pytest
import httpx
from mrp.tests.test_ordinary_turn_runs import rig, req
from mrp.tests.test_conversation_runs import rig as conversation_rig
from mrp.server.app import create_app
from mrp.server.deps import state_dict
from mrp.server.routers.messages import SendMessageReq
from mrp.orchestrator.conversation_context import ConversationConflict


def test_constructor_rejects_maintenance_before_initialization(tmp_path, monkeypatch):
    from mrp.server.container import AppContainer
    from mrp.storage.data_lease import DataRootBusy, DataRootLease
    calls = []
    monkeypatch.setattr(AppContainer, "_initialize", lambda *args, **kwargs: calls.append(True))
    with DataRootLease(tmp_path):
        with pytest.raises(DataRootBusy):
            AppContainer(tmp_path)
    assert calls == []
    assert {entry.name for entry in tmp_path.iterdir()} == {".story-storage.lease"}


def test_constructor_rejects_partial_cutover_before_schema_writes(tmp_path):
    import sqlite3
    from mrp.server.container import AppContainer, AppContainerConfig
    folder = tmp_path / "memories"
    folder.mkdir()
    database = folder / "memory.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE migrations(id TEXT,status TEXT)")
        db.execute("INSERT INTO migrations VALUES('synthetic','activating')")
    with pytest.raises(RuntimeError, match="recover"):
        AppContainer(tmp_path, config=AppContainerConfig(fake_mode=True))
    with sqlite3.connect(database) as db:
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert tables == [("migrations",)]
    assert not (tmp_path / "sessions").exists()


@pytest.mark.asyncio
async def test_derived_schedule_failure_keeps_committed_story(tmp_path, monkeypatch):
    c, runner, *_ = await rig(tmp_path)
    def failed(*args):
        raise OSError("synthetic derived scheduling failure")
    monkeypatch.setattr(c.story_backups, "schedule", failed)
    monkeypatch.setattr(c.story_search, "schedule", failed)
    revision = runner.state.meta.branch_revision
    async with c.branch_commit.edit(runner) as staged:
        staged.meta.title = "durable despite derived failure"
    saved = await c.sessions.load_state(runner.state.meta.id)
    assert saved.meta.title == runner.public_head.meta.title == "durable despite derived failure"
    assert saved.meta.branch_revision == revision + 1
    await c.aclose()


@pytest.mark.asyncio
async def test_cold_load_single_flight_and_cancel_retry(tmp_path, monkeypatch):
    c, runner, *_ = await rig(tmp_path)
    sid = runner.state.meta.id
    await c.drop_runner(sid)
    load = c.sessions.load_state
    calls = 0
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked(sid):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return await load(sid)
    monkeypatch.setattr(c.sessions, "load_state", blocked)
    first = asyncio.create_task(c.load_session(sid))
    await entered.wait()
    second = asyncio.create_task(c.load_session(sid))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    recovered = await second
    others = await asyncio.gather(*(c.load_session(sid) for _ in range(8)))
    assert all(row is recovered for row in others)
    assert calls == 2
    await c.aclose()


@pytest.mark.asyncio
async def test_lease_prevents_idle_eviction(tmp_path):
    c, r, *_ = await rig(tmp_path)
    c.runner_cache = 1
    with c.branch_runtimes.request_scope():
        assert await c.load_session(r.state.meta.id) is r
        state = r.state.model_copy(deep=True)
        state.meta.id = "sess-synthetic-other"
        other = c._make_runner(state)
        await c._register_runner(other)
        assert c.runners.get(r.state.meta.id) is r and not r.runtime.closed
    state = r.state.model_copy(deep=True)
    state.meta.id = "sess-synthetic-third"
    third = c._make_runner(state)
    await c._register_runner(third)
    assert r.runtime.closed
    await c.aclose()


@pytest.mark.asyncio
async def test_staged_delete_hides_uncommitted_result_and_failure(tmp_path, monkeypatch):
    c, r, _, sink, _ = await rig(tmp_path)
    await c.turn_runs.send(r, req())
    target = r.state.messages[-1]
    before = state_dict(r)
    events = len(sink.events)
    entered, release = asyncio.Event(), asyncio.Event()
    async def failed(_runner):
        entered.set()
        await release.wait()
        raise OSError("synthetic save failure")
    monkeypatch.setattr(c, "persist_owned_branch_commit", failed)
    async def delete():
        async with c.branch_commit.edit(r, memory=True):
            assert await r.delete_message(target.id)
    task = asyncio.create_task(delete())
    await asyncio.wait_for(entered.wait(), 3)
    assert state_dict(r) == before
    assert len(sink.events) == events
    release.set()
    with pytest.raises(OSError):
        await task
    assert state_dict(r) == before
    assert any(m.id == target.id for m in r.state.messages)
    assert len(sink.events) == events
    await c.aclose()


@pytest.mark.asyncio
async def test_patch_cas_and_rejected_preset_leave_no_title_change(tmp_path):
    c, r, *_ = await rig(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)), base_url="http://127.0.0.1",
        headers={"Origin":"http://127.0.0.1"}) as client:
        url=f"/api/v1/sessions/{r.state.meta.id}"
        revision=r.state.meta.branch_revision
        rejected=await client.patch(url,json={"title":"bad staged title","prompt_preset_id":"missing","expected_branch_revision":revision})
        assert rejected.status_code == 404
        assert r.state.meta.title != "bad staged title"
        success=await client.patch(url,json={"title":"accepted title","expected_branch_revision":revision})
        assert success.status_code == 200
        conflict=await client.patch(url,json={"title":"stale title","expected_branch_revision":revision})
        assert conflict.status_code == 409
        assert r.state.meta.title == "accepted title"
    await c.aclose()


@pytest.mark.asyncio
async def test_free_explicit_operation_retry_and_conflict(tmp_path):
    c, r, engine, *_ = await conversation_rig(tmp_path, ["a"])
    request=SendMessageReq(content="synthetic", operation_id="shared-operation", mentions=["a"], reply_mode="free", max_replies=1,
        expected_player_identity_id=r.state.meta.player_identity_id)
    first=await c.conversation_runs.send_free(r, request)
    count=len(engine.contexts)
    again=await c.conversation_runs.send_free(r, request)
    assert first["conversation_run"]["id"] == again["conversation_run"]["id"]
    assert len(engine.contexts) == count
    with pytest.raises(ConversationConflict):
        await c.conversation_runs.send_free(r, request.model_copy(update={"content":"different"}))
    await c.aclose()


@pytest.mark.asyncio
async def test_cancel_during_save_settles_committed_head_before_unlock(tmp_path, monkeypatch):
    c, r, *_ = await rig(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    persist = c.persist_owned_branch_commit
    async def blocked(value):
        entered.set()
        await release.wait()
        await persist(value)
    monkeypatch.setattr(c, "persist_owned_branch_commit", blocked)
    async def patch():
        async with c.branch_commit.edit(r):
            r.state.meta.title = "durable despite disconnected request"
    task = asyncio.create_task(patch())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    await asyncio.sleep(0)
    assert r.runtime.turn_lock.locked()
    assert state_dict(r)["meta"]["title"] != "durable despite disconnected request"
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not r.runtime.turn_lock.locked()
    assert state_dict(r)["meta"]["title"] == "durable despite disconnected request"
    assert (await c.sessions.load_state(r.state.meta.id)).meta.title == r.state.meta.title
    await c.aclose()


@pytest.mark.asyncio
async def test_deferred_generation_returns_isolated_draft_without_story_commit():
    from mrp.tests.test_targeted_regeneration import setup
    from mrp.application.reply_generation import GenerationInput
    from dataclasses import FrozenInstanceError
    r, _, sink, _ = setup(group=True)
    actor = r.state.characters[0]
    request = GenerationInput.capture(r.state, actor, 1)
    original = request.actor_json
    actor.card.name = "edited after capture"
    assert request.actor_json == original
    with pytest.raises(FrozenInstanceError):
        request.turn = 99
    before = [m.model_dump() for m in r.state.messages]
    draft = await r.turns.run_character_turn(actor, 1, defer_commit=True, emit_final=False)
    assert [m.model_dump() for m in r.state.messages] == before
    assert draft.status == "final" and draft.content
    assert not any(event == "message.final" for event, _ in sink.events)
    await r.aclose()


@pytest.mark.asyncio
async def test_sqlite_story_and_source_memory_invalidate_in_one_commit(tmp_path, monkeypatch):
    from mrp.shared.models import MemoryRecord
    c, r, *_ = await rig(tmp_path)
    r.memory = c.memory_store
    await c.turn_runs.send(r, req())
    target = r.state.messages[-1]
    record = MemoryRecord(character_id=target.actor, session_id=r.state.meta.id, kind="episodic",
        content="synthetic summary", source_message_ids=[target.id], turn_start=1, turn_end=1)
    c.memory_store.add(record)
    def forbidden(*args):
        raise AssertionError("SQLite staged commands must not invalidate before story commit")
    monkeypatch.setattr(c.memory_store, "invalidate_sources", forbidden)
    async with c.branch_commit.edit(r, memory=True):
        assert await r.delete_message(target.id)
        current = c.memory_store.records_for(target.actor, session_id=r.state.meta.id)
        assert not current[0].invalidated
    current = c.memory_store.records_for(target.actor, session_id=r.state.meta.id)
    assert current[0].invalidated or current[0].source_changed
    assert all(m.id != target.id for m in (await c.sessions.load_state(r.state.meta.id)).messages)
    await c.aclose()


def test_background_context_cannot_resurrect_closed_request_lease():
    from contextvars import copy_context
    from mrp.application.branch_runtime import BranchRuntimeRegistry
    registry = BranchRuntimeRegistry()
    with registry.request_scope():
        registry.pin("synthetic")
        copied = copy_context()
        assert registry.leased("synthetic")
    copied.run(registry.pin, "synthetic")
    copied.run(registry.pin, "another")
    assert not registry.leased("synthetic") and not registry.leased("another")
