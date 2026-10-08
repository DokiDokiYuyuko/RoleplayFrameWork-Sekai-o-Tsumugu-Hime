"""Synthetic command retry and failure tests; no real models or private data."""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from mrp.server.app import create_app
from mrp.shared.models import Message, MessageVariant, SessionMeta, SessionState
from mrp.tests.conftest import make_test_container


@asynccontextmanager
async def client_for(container):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(container),
        raise_app_exceptions=False), base_url="http://127.0.0.1",
        headers={"Origin": "http://127.0.0.1"}) as client:
        yield client


async def seed(container, *, legacy=False):
    container.sessions.sqlite_new_stories = not legacy
    sid = "sess-command-synthetic"
    state = SessionState(schema_version=3, meta=SessionMeta(id=sid, story_id=sid),
        messages=[Message(id="msg-command-first", session_id=sid, seq=0, turn=1,
            actor="player", content="first", input_group_id="input-command"),
            Message(id="msg-command-second", session_id=sid, seq=1, turn=1,
            actor="player", content="second", kind="inner", input_group_id="input-command")])
    await container.sessions.save_state(state, 0)
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_concurrent_duplicate_edit_and_failed_commit_retry(tmp_path, monkeypatch, legacy):
    c = make_test_container(tmp_path)
    try:
        state = await seed(c, legacy=legacy)
        url = f"/api/v1/sessions/{state.meta.id}/messages/{state.messages[0].id}"
        body = {"content": "edited", "expected_branch_revision": state.meta.branch_revision}
        headers = {"X-Operation-ID": "command-concurrent"}
        async with client_for(c) as client:
            saved = c.sessions.commit_state
            async def fail(*args, **kwargs):
                raise OSError("synthetic failure before durable commit")
            monkeypatch.setattr(c.sessions, "commit_state", fail)
            rejected = await client.patch(url, json=body, headers=headers)
            assert rejected.status_code == 503
            assert await c.sessions.lookup_command(state.meta.id, "command-concurrent") is None
            assert (await c.sessions.load_state(state.meta.id)).messages[0].content == "first"
            monkeypatch.setattr(c.sessions, "commit_state", saved)
            first, duplicate = await asyncio.gather(*(client.patch(url, json=body, headers=headers) for _ in range(2)))
            assert first.status_code == duplicate.status_code == 200
            assert first.json() == duplicate.json()
            assert first.headers["X-Command-ID"] == "command-concurrent"
            assert first.headers["X-Branch-Revision"] == duplicate.headers["X-Branch-Revision"] == str(state.meta.branch_revision + 1)
            assert (await c.sessions.load_state(state.meta.id)).meta.branch_revision == state.meta.branch_revision + 1
            conflict = await client.patch(url, json={**body, "content": "other"}, headers=headers)
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["code"] == "operation_conflict"
    finally:
        await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_noop_settings_receipt_survives_restart_and_old_cas(tmp_path, legacy):
    c = make_test_container(tmp_path)
    state = await seed(c, legacy=legacy)
    url = f"/api/v1/sessions/{state.meta.id}"
    body = {"title": state.meta.title, "expected_branch_revision": state.meta.branch_revision}
    headers = {"X-Operation-ID": "command-noop"}
    async with client_for(c) as client:
        first = await client.patch(url, json=body, headers=headers)
        assert first.status_code == 200
        assert first.json()["branch_revision"] == state.meta.branch_revision + 1
    await c.aclose()
    c = make_test_container(tmp_path)
    try:
        async with client_for(c) as client:
            replay = await client.patch(url, json=body, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == first.json()
        assert replay.headers["X-Command-ID"] == "command-noop"
        assert replay.headers["X-Branch-Revision"] == first.headers["X-Branch-Revision"]
        assert (await c.sessions.load_state(state.meta.id)).meta.branch_revision == state.meta.branch_revision + 1
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_input_group_replay_and_legacy_no_id(tmp_path):
    c = make_test_container(tmp_path)
    try:
        state = await seed(c)
        url = f"/api/v1/sessions/{state.meta.id}/messages/{state.messages[0].id}/input-group"
        payload = {"expected_branch_revision": state.meta.branch_revision,
            "parts": [{"message_id": msg.id, "expected_fingerprint": msg.fingerprint,
                       "content": msg.content + " edited"} for msg in state.messages]}
        headers = {"X-Operation-ID": "command-input-group"}
        async with client_for(c) as client:
            first = await client.patch(url, json=payload, headers=headers)
            assert first.status_code == 200, first.text
            repeated = await client.patch(url, json=payload, headers=headers)
            assert repeated.json() == first.json()
            assert first.json()["branch_revision"] == state.meta.branch_revision + 1
            old = await client.patch(f"/api/v1/sessions/{state.meta.id}", json={"title": "old client"})
            assert old.status_code == 200
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_candidate_switch_replay_does_not_generate_or_recommit(tmp_path):
    from mrp.tests.test_ordinary_turn_runs import rig, req
    c, runner, engine, *_ = await rig(tmp_path)
    try:
        await c.turn_runs.send(runner, req(mentions=["a"]))
        message = next(msg for msg in runner.state.messages if msg.actor == "a")
        message.variants.append(MessageVariant(content="synthetic alternative"))
        await c.persist_session(runner)
        revision = runner.state.meta.branch_revision
        calls = len(engine.contexts)
        url = f"/api/v1/sessions/{runner.state.meta.id}/messages/{message.id}"
        payload = {"active_variant": len(message.variants) - 1,
            "expected_branch_revision": revision, "expected_fingerprint": message.fingerprint}
        async with client_for(c) as client:
            headers = {"X-Operation-ID": "command-switch"}
            first = await client.patch(url, json=payload, headers=headers)
            assert first.status_code == 200, first.text
            repeated = await client.patch(url, json=payload, headers=headers)
            assert repeated.status_code == 200
            assert first.json() == repeated.json()
            assert first.json()["content"] == "synthetic alternative"
        assert len(engine.contexts) == calls
        assert runner.state.meta.branch_revision == revision + 1
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_command_and_generation_id_conflicts_in_both_directions(tmp_path):
    from mrp.tests.test_ordinary_turn_runs import rig, req
    c, runner, engine, *_ = await rig(tmp_path)
    try:
        await c.turn_runs.send(runner, req(mentions=["a"], operation_id="generation-owned"))
        url = f"/api/v1/sessions/{runner.state.meta.id}"
        async with client_for(c) as client:
            rejected = await client.patch(url, json={"title": "collision"},
                headers={"X-Operation-ID": "generation-owned"})
            assert rejected.status_code == 409
            assert (await client.patch(url, json={"title": "command"},
                headers={"X-Operation-ID": "command-owned"})).status_code == 200
            calls = len(engine.contexts)
            send = await client.post(url + "/messages", json={"content": "must not generate",
                "operation_id": "command-owned", "mentions": ["a"]})
            assert send.status_code == 409
            assert len(engine.contexts) == calls
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_outbox_delivery_failure_is_success_and_replay_does_not_emit_again(tmp_path, monkeypatch):
    c = make_test_container(tmp_path)
    try:
        state = await seed(c)
        calls = []
        async def failed(**kwargs):
            calls.append(True)
            raise ConnectionError("synthetic notification failure after SQL commit")
        monkeypatch.setattr(c, "dispatch_story_outbox", failed)
        url = f"/api/v1/sessions/{state.meta.id}/messages/{state.messages[0].id}"
        body = {"content": "durable", "expected_branch_revision": state.meta.branch_revision}
        async with client_for(c) as client:
            headers = {"X-Operation-ID": "command-notification-failure"}
            first = await client.patch(url, json=body, headers=headers)
            assert first.status_code == 200, first.text
            repeated = await client.patch(url, json=body, headers=headers)
            assert repeated.json() == first.json()
        assert len(calls) == 1
        assert len(c.sessions.story_db.pending_events()) == 1
        durable = await c.sessions.load_state(state.meta.id)
        assert durable.messages[0].content == "durable"
        assert durable.meta.branch_revision == state.meta.branch_revision + 1
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_repository_duplicate_guard_does_not_install_stale_loser(tmp_path, monkeypatch):
    first = make_test_container(tmp_path)
    state = await seed(first)
    second = make_test_container(tmp_path)
    try:
        stale = await second.load_session(state.meta.id)
        url = f"/api/v1/sessions/{state.meta.id}"
        body = {"title": "winner", "expected_branch_revision": state.meta.branch_revision}
        headers = {"X-Operation-ID": "command-repository-race"}
        async with client_for(first) as client:
            winner = await client.patch(url, json=body, headers=headers)
            assert winner.status_code == 200
        async def lookup_missed(*args):
            return None
        monkeypatch.setattr(second.sessions, "lookup_command", lookup_missed)
        async with client_for(second) as client:
            replay = await client.patch(url, json=body, headers=headers)
            assert replay.status_code == 200, replay.text
            assert replay.json() == winner.json()
            assert replay.headers["X-Branch-Revision"] == winner.headers["X-Branch-Revision"]
        assert stale.public_head.meta.title == "winner"
        assert stale.public_head.meta.branch_revision == state.meta.branch_revision + 1
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.asyncio
async def test_old_message_replay_carries_original_revision_after_new_edit(tmp_path):
    c = make_test_container(tmp_path)
    try:
        state = await seed(c)
        url = f"/api/v1/sessions/{state.meta.id}/messages/{state.messages[0].id}"
        original_body = {"content": "first edit", "expected_branch_revision": state.meta.branch_revision}
        async with client_for(c) as client:
            first = await client.patch(url, json=original_body, headers={"X-Operation-ID": "command-old"})
            assert first.status_code == 200
            later = await client.patch(url, json={"content": "later edit",
                "expected_branch_revision": int(first.headers["X-Branch-Revision"])},
                headers={"X-Operation-ID": "command-later"})
            assert later.status_code == 200
            replay = await client.patch(url, json=original_body, headers={"X-Operation-ID": "command-old"})
        assert replay.json() == first.json()
        assert replay.headers["X-Command-ID"] == first.headers["X-Command-ID"]
        assert replay.headers["X-Branch-Revision"] == first.headers["X-Branch-Revision"]
        assert int(replay.headers["X-Branch-Revision"]) < int(later.headers["X-Branch-Revision"])
        assert (await c.sessions.load_state(state.meta.id)).messages[0].content == "later edit"
    finally:
        await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("active_field", ["active_turn_run_id", "active_conversation_run_id", "turn_checkpoint_active"])
async def test_receipt_read_precedes_actual_run_ownership_and_new_commands_reject(tmp_path, active_field):
    c = make_test_container(tmp_path)
    runner = None
    try:
        state = await seed(c)
        runner = await c.load_session(state.meta.id)
        url = f"/api/v1/sessions/{state.meta.id}"
        body = {"title": "committed", "expected_branch_revision": state.meta.branch_revision}
        async with client_for(c) as client:
            first = await client.patch(url, json=body, headers={"X-Operation-ID": "command-before-run"})
            assert first.status_code == 200
            setattr(runner.runtime, active_field, True if active_field == "turn_checkpoint_active" else "synthetic-active")
            assert runner.busy()
            replay = await client.patch(url, json=body, headers={"X-Operation-ID": "command-before-run"})
            assert replay.status_code == 200
            assert replay.json() == first.json()
            assert replay.headers["X-Branch-Revision"] == first.headers["X-Branch-Revision"]
            blocked = await client.patch(url, json={"title": "new mutation"}, headers={"X-Operation-ID": "command-during-run"})
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["detail"]["code"] == "branch_busy"
    finally:
        if runner is not None:
            setattr(runner.runtime, active_field, False if active_field == "turn_checkpoint_active" else None)
        await c.aclose()
