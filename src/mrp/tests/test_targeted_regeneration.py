"""Synthetic acceptance for per-node alternatives, dependencies and durable compensation."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from datetime import timedelta

import pytest

from mrp.orchestrator.session import SessionRunner
from mrp.orchestrator.message_regeneration import RegenerationRequest, RegenerationConflict, RegenerationFailure
from mrp.orchestrator.generation_sources import make_provenance, source_ref, generation_baseline
from mrp.orchestrator.memory import MemoryStore
from mrp.shared.models import Character, CharacterCard, EngineReply, GroupActor, Message, Scene, SessionMeta, SessionState, TokenUsage, MemoryRecord, Lorebook, LorebookEntry


class Sink:
    def __init__(self): self.events = []
    async def publish(self, sid, event, payload, **kwargs): self.events.append((event, payload))


class Engine:
    def __init__(self): self.contexts = []; self.fail_actor = None; self.on_generate = None
    async def generate(self, character, ctx, *, on_delta=None):
        self.contexts.append(ctx)
        if self.on_generate is not None:
            await self.on_generate(character, ctx)
        if character.id == self.fail_actor:
            raise RuntimeError("synthetic model failure")
        content = f"{character.id} synthetic answer {len(self.contexts)}"
        if on_delta: on_delta(content)
        return EngineReply(content=content, usage=TokenUsage(input_tokens=10, output_tokens=4))


class Group:
    def __init__(self): self.contexts = []; self.fail = False
    async def generate(self, state, group, **kwargs):
        self.contexts.append(state.model_copy(deep=True))
        if self.fail: raise RuntimeError("synthetic group failure")
        trace = {"system": "synthetic", "user": "synthetic", "visible_messages": state.messages,
                 "group_details": "synthetic", "source_id": group.id,
                 "tokens_by_section": {"group": 1}}
        return f"group answer {len(self.contexts)}", {"input_tokens": 8, "output_tokens": 3}, trace


def setup(group=False, memory=None):
    chars = [Character(id=name, card=CharacterCard(name=name)) for name in ["a", "b", "c"]]
    scene = Scene(id="scene", title="synthetic scene", member_ids=[c.id for c in chars])
    groups = [GroupActor(id="group", label="synthetic group", scene_id=scene.id, joined_seq=0)] if group else []
    scene.group_ids = [g.id for g in groups]
    state = SessionState(schema_version=3, meta=SessionMeta(id="synthetic-session", character_ids=[c.id for c in chars],
        director_mode="rules", short_input_padding=False, hygiene_enabled=False, proactive_turn_limit=0),
        characters=chars, scenes=[scene], active_scene_id=scene.id, groups=groups)
    engine, sink, responder = Engine(), Sink(), Group()
    return SessionRunner(state, engine, sink=sink, group_responder=responder, memory_store=memory), engine, sink, responder


def request(runner, target, operation_id="operation"):
    return RegenerationRequest(operation_id=operation_id, expected_branch_revision=runner.state.meta.branch_revision,
                              expected_fingerprint=target.fingerprint,
                              expected_player_identity_id=runner.state.meta.player_identity_id)


async def round_(runner, mode="serial", actors=None):
    return await runner.player_say_with_reply_mode("synthetic input", mentions=actors or ["a", "b", "c"], reply_mode=mode)


@pytest.mark.asyncio
async def test_parallel_any_peer_keeps_others_and_excludes_all_current_peers():
    runner, engine, sink, _ = setup()
    messages = await round_(runner, "parallel")
    target = messages[1]
    peer_before = [m.model_dump(mode="json") for m in messages[2:]]
    original_id_seq = target.id, target.seq
    result = await runner.regeneration.run("one", target.id, request(runner, target))
    ctx = engine.contexts[-1]
    assert [m.actor for m in ctx.visible_messages] == ["player"]
    assert ctx.reply_frame.mode == "parallel"
    assert [m.model_dump(mode="json") for m in messages[2:]] == peer_before
    assert (target.id, target.seq) == original_id_seq and len(target.variants) == 2
    assert not any(m.dependency_stale for m in runner.state.messages)
    assert result["affected_message_ids"] == []
    assert "messages" not in target.generation_meta.provenance.baseline_state
    assert "generation_operations" not in target.generation_meta.provenance.baseline_state
    assert target.generation_id and target.generation_meta.message_id == target.id
    pending = [p for e, p in sink.events if e == "message.pending" and p["message"]["id"] == target.id][-1]
    assert pending["generation_id"] == target.generation_id and pending["attempt_id"] == target.attempt_id


@pytest.mark.asyncio
async def test_serial_front_node_no_future_stale_and_switch_original_clears():
    runner, engine, _, _ = setup()
    messages = await round_(runner)
    a, b, c = messages[1:]
    old_b_c = b.content, c.content
    await runner.regeneration.run("one", a.id, request(runner, a))
    assert [m.actor for m in engine.contexts[-1].visible_messages] == ["player"]
    assert b.dependency_stale and c.dependency_stale
    assert (b.content, c.content) == old_b_c
    assert {m.id for m in runner.state.visible_messages_for("c")} == {messages[0].id, a.id}
    await runner.switch_variant(a.id, 0)
    assert not b.dependency_stale and not c.dependency_stale
    assert len(a.variants) == 2


@pytest.mark.asyncio
async def test_stale_clicked_node_accepts_itself_and_descendants_with_audit():
    runner, _, _, _ = setup()
    _, a, b, c = await round_(runner)
    old = b.content, c.content
    await runner.regeneration.run("one", a.id, request(runner, a))
    result = await runner.regeneration.run("accept", b.id, request(runner, b, "accept"))
    assert not b.dependency_stale and not c.dependency_stale
    assert (b.content, c.content) == old
    assert b.accepted_dependency_sources and b.generation_meta.provenance.sources
    assert {m["id"] for m in result["messages"]} == {b.id, c.id}
    await runner.regeneration.run("one", a.id, request(runner, a, "again"))
    assert b.dependency_stale and c.dependency_stale


@pytest.mark.asyncio
async def test_dependency_replay_is_atomic_and_stale_clicked_node_is_included():
    runner, engine, _, _ = setup()
    _, a, b, c = await round_(runner)
    await runner.regeneration.run("one", a.id, request(runner, a))
    before = runner.state.model_dump(mode="json")
    cost_before = sum(item["input_tokens"] for item in runner.cost_by_model.values())
    engine.fail_actor = "c"
    with pytest.raises(RegenerationFailure):
        await runner.regeneration.run("dependents", b.id, request(runner, b, "failed-dependents"))
    after = runner.state.model_dump(mode="json")
    # Calls already made remain billed even when story commits roll back.
    for key in ("usage_records", "usage_incomplete"):
        before.pop(key, None)
        after.pop(key, None)
    assert after == before
    assert sum(item["input_tokens"] for item in runner.cost_by_model.values()) > cost_before
    engine.fail_actor = None
    result = await runner.regeneration.run("dependents", b.id, request(runner, b, "dependents"))
    assert not b.dependency_stale and not c.dependency_stale
    assert len(b.variants) == len(c.variants) == 2
    assert {m["id"] for m in result["messages"]} == {b.id, c.id}
    assert engine.contexts[-1].visible_messages[-1].id == b.id


@pytest.mark.asyncio
async def test_idempotency_cas_and_current_identity_fail_without_generation():
    runner, engine, _, _ = setup()
    _, a, _, _ = await round_(runner)
    req = request(runner, a)
    result = await runner.regeneration.run("one", a.id, req)
    call_count = len(engine.contexts)
    assert await runner.regeneration.run("one", a.id, req) == result
    assert len(engine.contexts) == call_count
    for update in ({"expected_fingerprint": "changed"}, {"expected_branch_revision": 9},
                   {"expected_player_identity_id": "another-private-identity"}):
        with pytest.raises(RegenerationConflict):
            await runner.regeneration.run("one", a.id, request(runner, a, "bad").model_copy(update=update))
    assert len(engine.contexts) == call_count
    with pytest.raises(RegenerationConflict):
        await runner.regeneration.run("one", a.id, request(runner, a, "operation"))


@pytest.mark.asyncio
async def test_group_same_service_preserves_peers_and_has_candidate_sources():
    runner, engine, _, group = setup(group=True)
    messages = await round_(runner, "parallel", ["a", "group"])
    peer, target = messages[1:]
    before = peer.model_dump(mode="json")
    await runner.regeneration.run("one", target.id, request(runner, target))
    assert peer.model_dump(mode="json") == before
    assert [m.actor for m in group.contexts[-1].messages] == ["player"]
    assert len(target.variants) == 2 and target.generation_meta.provenance.mode == "parallel"
    assert target.generation_meta.provenance.sources


@pytest.mark.asyncio
async def test_whole_parallel_rerun_failure_restores_original_group_and_cost():
    runner, engine, _, group = setup(group=True)
    player, *_ = await round_(runner, "parallel", ["a", "group"])
    before = runner.state.model_dump(mode="json")
    old_tokens = sum(x["input_tokens"] for x in runner.cost_by_model.values())
    group.fail = True
    with pytest.raises(RuntimeError):
        await runner.regenerate_turn(player.id)
    after = runner.state.model_dump(mode="json")
    assert len(after["usage_records"]) > len(before["usage_records"])
    for key in ("usage_records", "usage_incomplete"):
        before.pop(key, None); after.pop(key, None)
    assert after == before
    assert sum(x["input_tokens"] for x in runner.cost_by_model.values()) > old_tokens


@pytest.mark.asyncio
async def test_saving_failure_restores_story_exact_memory_flags_and_windows(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite", tmp_path / "mirror", None)
    try:
        runner, engine, sink, _ = setup(memory=store)
        _, a, b, _ = await round_(runner)
        record = MemoryRecord(character_id="b", session_id=runner.state.meta.id, kind="episodic", content="synthetic memory",
            source_message_ids=[a.id, b.id], source_fingerprints={a.id: a.fingerprint, b.id: b.fingerprint},
            turn_start=1, turn_end=1, participant_ids=["a", "b"], important=True)
        store.commit_window(runner.state.meta.id, "b", 1, 1, record.source_fingerprints, [record])
        before = runner.state.model_dump(mode="json")
        memories = [m.model_dump(mode="json") for m in store.records_for("b")]
        windows = store.window_rows("b", runner.state.meta.id)
        async def fail_save():
            assert not any(e == "message.final" and p.get("operation_id") == "failed-save" for e, p in sink.events)
            raise OSError("synthetic save failure")
        with pytest.raises(RegenerationFailure):
            await runner.regeneration.run("one", a.id, request(runner, a, "failed-save"), persist=fail_save)
        assert runner.state.model_dump(mode="json") == before
        assert [m.model_dump(mode="json") for m in store.records_for("b")] == memories
        assert store.window_rows("b", runner.state.meta.id) == windows
        assert store.conn.execute("SELECT COUNT(*) FROM memory_generation_recovery").fetchone()[0] == 0
    finally: store.close()


@pytest.mark.asyncio
async def test_cropped_worldbook_inner_and_memory_never_read_future(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite", tmp_path / "mirror", None)
    try:
        runner, engine, _, _ = setup(memory=store)
        _, a, b, c = await round_(runner)
        original_baseline = a.generation_meta.provenance.baseline_state
        runner.state.lorebooks = [Lorebook(id="future-book", name="future", entries=[LorebookEntry(uid=1, constant=True,
                                   content="FUTURE WORLD TEXT")])]
        runner.lorebooks = runner.state.lorebooks
        future = Message(id="future-inner", session_id=runner.state.meta.id, seq=runner.state.next_seq(), turn=1,
                         actor="player", kind="inner", content="FUTURE INNER TEXT", visible_to=["player"], scene_id="scene")
        runner.state.messages.append(future)
        store.add(MemoryRecord(character_id="a", session_id=runner.state.meta.id, kind="manual", content="FUTURE MEMORY TEXT",
            source_message_ids=[c.id], source_fingerprints={c.id: c.fingerprint}, participant_ids=["player", "a"], important=True))
        await runner.regeneration.run("one", a.id, request(runner, a))
        prompt = engine.contexts[-1].planned_prompt.text
        assert all(text not in prompt for text in ("FUTURE WORLD TEXT", "FUTURE INNER TEXT", "FUTURE MEMORY TEXT", b.content, c.content))
        assert "messages" not in original_baseline
    finally: store.close()


@pytest.mark.asyncio
async def test_frozen_resolver_preset_scene_and_late_old_source_memory(tmp_path):
    from mrp.storage.prompt_presets import PromptPreset, PromptSegment
    store = MemoryStore(tmp_path / "memory.sqlite", tmp_path / "mirror", None)
    try:
        runner, engine, _, _ = setup(memory=store)
        old_preset = PromptPreset(id="preset", name="old", segments=[PromptSegment(name="old", content="ORIGINAL PRESET")])
        new_preset = PromptPreset(id="preset", name="new", segments=[PromptSegment(name="new", content="FUTURE PRESET")])
        runner.app_settings = SimpleNamespace(active_prompt_preset_id="preset", model="synthetic")
        from mrp.orchestrator.model_capacity import ModelCapacity
        async def capacity(*args, **kwargs):
            return ModelCapacity(None, None, 8192, "synthetic")
        runner.context_builder.model_capacity = capacity
        runner.prompt_preset_resolver = lambda _: old_preset
        runner.state.meta.source_world_id = "synthetic-world"
        runner.state.meta.source_world_revision = 1
        runner.state.meta.world_archive_records = [{"id": "original", "kind": "background", "title": "old", "body": "ORIGINAL ARCHIVE"}]
        _, a, _, _ = await round_(runner)
        assert "ORIGINAL PRESET" in engine.contexts[0].planned_prompt.text
        runner.prompt_preset_resolver = lambda _: new_preset
        calls = []
        class Record:
            def model_dump(self, **kwargs):
                return {"id": "future", "kind": "background", "title": "future", "body": "FUTURE RESOLVED ARCHIVE"}
        def resolve(_):
            calls.append(True)
            return SimpleNamespace(revision=2, archive_records=[Record()])
        runner.world_resolver = resolve
        runner.state.scenes[0].description = "FUTURE SCENE DESCRIPTION"
        store.add(MemoryRecord(character_id="a", session_id=runner.state.meta.id, kind="manual",
                              content="FUTURE UNANCHORED MEMORY", important=True))
        await runner.regeneration.run("one", a.id, request(runner, a))
        prompt = engine.contexts[-1].planned_prompt.text
        assert "ORIGINAL PRESET" in prompt and "ORIGINAL ARCHIVE" in prompt
        assert all(text not in prompt for text in ("FUTURE PRESET", "FUTURE RESOLVED ARCHIVE",
                                                  "FUTURE SCENE DESCRIPTION", "FUTURE UNANCHORED MEMORY"))
        assert calls == []
        await runner.turns.run_character_turn(runner.state.character("a"), runner.state.current_turn())
        assert calls == []
        assert "ORIGINAL ARCHIVE" in engine.contexts[-1].planned_prompt.text
        assert "FUTURE RESOLVED ARCHIVE" not in engine.contexts[-1].planned_prompt.text
        assert "FUTURE PRESET" in engine.contexts[-1].planned_prompt.text
    finally:
        store.close()


@pytest.mark.asyncio
async def test_frozen_history_compression_rejects_later_summary_with_old_sources():
    runner, *_ = setup()
    old = Message(id="old", session_id=runner.state.meta.id, seq=0, turn=1, actor="a",
                  content="original historical source text", scene_id="old-scene")
    runner.state.messages.append(old)
    runner.state.scenes.append(Scene(id="old-scene", title="old", turn_start=1, turn_end=1))
    runner.state.meta.memory_compress_horizon_turns = 1
    player = Message(id="current", session_id=runner.state.meta.id, seq=1, turn=4, actor="player", content="current", scene_id="scene")
    runner.state.messages.append(player)
    target = await runner.turns.run_character_turn(runner.state.character("a"), 4)
    future_summary = MemoryRecord(character_id="a", session_id=runner.state.meta.id, kind="scene", scene_id="old-scene",
                                  content="FUTURE SUMMARY", source_message_ids=[old.id], source_fingerprints={old.id: old.fingerprint},
                                  created_at=target.created_at + timedelta(seconds=1))
    runner.memory = SimpleNamespace(scene_summary=lambda *args: future_summary)
    snap, _ = generation_baseline(runner.state, target)
    compressed = runner.context_builder.compress_history(snap.character("a"), snap.visible_messages_for("a"), input_limit=1, state=snap)
    assert old.id in [m.id for m in compressed]
    assert "FUTURE SUMMARY" not in " ".join(m.content for m in compressed)


@pytest.mark.asyncio
async def test_index_failure_keeps_durable_story_commit(tmp_path, monkeypatch):
    from mrp.storage.paths import AppPaths
    from mrp.storage.session_repo import SessionRepo
    repo = SessionRepo(AppPaths(tmp_path / "data"))
    runner, _, sink, _ = setup()
    _, a, _, _ = await round_(runner)
    await repo.save_state(runner.state)
    before = runner.state.model_dump(mode="json")
    original_upsert = repo._upsert_row
    calls = []
    async def fail_once(summary):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("synthetic index write failed after body saved")
        await original_upsert(summary)
    monkeypatch.setattr(repo, "_upsert_row", fail_once)
    await runner.regeneration.run("one", a.id, request(runner, a, "write-failure"),
        persist=lambda: repo.save_state(runner.state), rollback_persist=lambda: repo.restore_state(runner.state))
    assert runner.state.meta.branch_revision == before["meta"]["branch_revision"] + 1
    saved = await repo.load_state_readonly(runner.state.meta.id)
    assert saved.model_dump(mode="json") == runner.state.model_dump(mode="json")
    assert any(event == "message.final" and payload.get("operation_id") == "write-failure" for event, payload in sink.events)


@pytest.mark.asyncio
async def test_same_speaker_same_turn_has_unique_inspection_and_reply_edge():
    runner, engine, _, _ = setup()
    player, first, *_ = await round_(runner)
    provenance = make_provenance(runner.state, "single", ["a"], reply_to_message_ids=[first.id],
                                 scheduling_sources=[source_ref(first)])
    second = await runner.turns.run_character_turn(runner.state.character("a"), player.turn, provenance=provenance)
    assert second.id != first.id and second.generation_id != first.generation_id
    assert runner.runtime.inspections.generation(first.id, first.generation_id)["ctx"].message_id == first.id
    assert runner.runtime.inspections.generation(second.id, second.generation_id)["ctx"].message_id == second.id
    assert second.generation_meta.provenance.reply_to_message_ids == [first.id]


def test_http_contract_persists_idempotency_and_cas(mrp_client):
    client = mrp_client
    imported = client.post("/api/v1/characters/import", files={"file": ("synthetic.json", b'{"name":"Synthetic"}', "application/json")})
    cid = imported.json()["id"]
    created = client.post("/api/v1/sessions", json={"title": "synthetic", "character_ids": [cid]}).json()
    sid = created["meta"]["id"]
    sent = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "synthetic input", "mentions": [cid]}).json()
    target = next(m for m in sent["messages"] if m["actor"] == cid)
    state = client.get(f"/api/v1/sessions/{sid}").json()
    req = {"operation_id": "http-operation", "expected_branch_revision": state["meta"]["branch_revision"],
           "expected_fingerprint": target["fingerprint"], "expected_player_identity_id": state["meta"]["player_identity_id"]}
    url = f"/api/v1/sessions/{sid}/messages/{target['id']}/regenerate-one"
    result = client.post(url, json=req)
    assert result.status_code == 200, result.text
    assert result.json()["branch_revision"] == req["expected_branch_revision"] + 1
    assert client.post(url, json=req).json() == result.json()
    assert client.post(url, json={**req, "operation_id": "stale-operation"}).status_code == 409
    message = next(m for m in result.json()["messages"] if m["id"] == target["id"])
    inspection = client.get(f"/api/v1/sessions/{sid}/generation-inspections/{target['id']}/{message['generation_id']}")
    assert inspection.status_code == 200 and inspection.json()["generation_id"] == message["generation_id"]


@pytest.mark.asyncio
async def test_child_boundary_and_historical_scope_reject_before_model():
    runner, engine, _, _ = setup()
    _, a, _, _ = await round_(runner)
    count = len(engine.contexts)
    async def child_protect(ids):
        assert a.id in ids
        raise RegenerationConflict("synthetic child anchor")
    with pytest.raises(RegenerationConflict):
        await runner.regeneration.run("one", a.id, request(runner, a), protect=child_protect)
    assert len(engine.contexts) == count
    await round_(runner)
    count = len(engine.contexts)
    with pytest.raises(RegenerationConflict):
        await runner.regeneration.run("one", a.id, request(runner, a))
    assert len(engine.contexts) == count


@pytest.mark.asyncio
async def test_pending_only_target_and_success_final_follows_persistence():
    runner, engine, sink, _ = setup()
    _, a, b, c = await round_(runner)
    peers = b.model_dump(mode="json"), c.model_dump(mode="json")
    async def observe(character, ctx):
        assert a.status == "pending"
        assert (b.model_dump(mode="json"), c.model_dump(mode="json")) == peers
    engine.on_generate = observe
    async def persisted():
        assert not any(e == "message.final" and p.get("operation_id") == "commit-order" for e, p in sink.events)
        runner.state.meta.branch_revision += 1
    await runner.regeneration.run("one", a.id, request(runner, a, "commit-order"), persist=persisted)
    final = [p for e, p in sink.events if e == "message.final" and p.get("operation_id") == "commit-order"]
    assert final and final[0]["branch_revision"] == runner.state.meta.branch_revision


def test_memory_compensation_journal_recovers_without_committed_json_marker(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite", tmp_path / "mirror", None)
    try:
        runner, *_ = setup(memory=store)
        record = MemoryRecord(character_id="a", session_id=runner.state.meta.id, kind="manual", content="synthetic manual",
                              source_message_ids=["source"])
        store.add(record)
        before = store.records_for("a")[0].model_dump(mode="json")
        store.prepare_generation_operation(runner.state.meta.id, "interrupted")
        store.invalidate_sources(runner.state.meta.id, {"source"})
        assert store.records_for("a")[0].source_changed
        store.recover_generation_operations(runner.state)
        assert store.records_for("a")[0].model_dump(mode="json") == before
    finally: store.close()
