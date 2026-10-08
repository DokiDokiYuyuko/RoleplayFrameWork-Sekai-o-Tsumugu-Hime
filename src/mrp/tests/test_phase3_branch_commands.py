"""Single-branch synthetic acceptance: models and side effects execute once."""
import pytest

from mrp.tests.test_command_replay import client_for
from mrp.tests.test_ordinary_turn_runs import rig, req
from mrp.shared.models import Character, CharacterCard


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["swipe", "continue", "regenerate"])
async def test_legacy_generation_retry_uses_receipt_and_keeps_model_count(tmp_path, action):
    c, runner, engine, *_ = await rig(tmp_path)
    try:
        await c.turn_runs.send(runner, req(mentions=["a"]))
        target = next(message for message in runner.state.messages if message.actor == ("player" if action == "regenerate" else "a"))
        url = f"/api/v1/sessions/{runner.state.meta.id}/messages/{target.id}/{action}"
        headers = {"X-Operation-ID": "synthetic-legacy-" + action}
        async with client_for(c) as client:
            first = await client.post(url, headers=headers)
            assert first.status_code == 200, first.text
            calls, revision = len(engine.contexts), runner.state.meta.branch_revision
            repeated = await client.post(url, headers=headers)
            assert repeated.status_code == 200, repeated.text
            assert repeated.json() == first.json()
            assert repeated.headers["X-Branch-Revision"] == first.headers["X-Branch-Revision"]
        assert len(engine.contexts) == calls
        assert runner.state.meta.branch_revision == revision
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_group_generation_and_swipe_commit_once(tmp_path):
    c, runner, _, _, responder = await rig(tmp_path)
    try:
        root = f"/api/v1/sessions/{runner.state.meta.id}/groups/group"
        async with client_for(c) as client:
            headers = {"X-Operation-ID": "synthetic-group-reply"}
            first = await client.post(root + "/reply", json={"idempotency_key": "group-original-key"}, headers=headers)
            assert first.status_code == 200, first.text
            message_id = first.json()["id"]
            assert first.json()["status"] == "final"
            calls = len(responder.contexts)
            repeated = await client.post(root + "/reply", json={"idempotency_key": "group-original-key"}, headers=headers)
            assert repeated.json() == first.json()
            assert len(responder.contexts) == calls
            swipe = root + f"/messages/{message_id}/swipe"
            headers = {"X-Operation-ID": "synthetic-group-swipe"}
            first = await client.post(swipe, headers=headers)
            assert first.status_code == 200, first.text
            calls, revision = len(responder.contexts), runner.state.meta.branch_revision
            repeated = await client.post(swipe, headers=headers)
            assert repeated.json() == first.json()
            assert len(responder.contexts) == calls
            assert runner.state.meta.branch_revision == revision
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_participant_and_pin_replay_retains_ids_without_recursive_receipts(tmp_path):
    c, runner, *_ = await rig(tmp_path)
    try:
        await c.save_character(Character(id="d", card=CharacterCard(name="Synthetic participant")))
        root = f"/api/v1/sessions/{runner.state.meta.id}"
        async with client_for(c) as client:
            headers = {"X-Operation-ID": "synthetic-participant"}
            first = await client.post(root + "/participants", json={"character_id": "d"}, headers=headers)
            assert first.status_code == 200, first.text
            repeated = await client.post(root + "/participants", json={"character_id": "d"}, headers=headers)
            assert repeated.json() == first.json()
            assert first.json()["added"] is True
            assert "generation_operations" not in first.json()["state"]
            headers = {"X-Operation-ID": "synthetic-pin-create"}
            first = await client.post(root + "/pinned-facts", json={"content": "Synthetic known fact"}, headers=headers)
            assert first.status_code == 200, first.text
            repeated = await client.post(root + "/pinned-facts", json={"content": "Synthetic known fact"}, headers=headers)
            assert repeated.json() == first.json()
            fact_id = first.json()["id"]
            headers = {"X-Operation-ID": "synthetic-pin-delete"}
            first = await client.delete(root + "/pinned-facts/" + fact_id, headers=headers)
            repeated = await client.delete(root + "/pinned-facts/" + fact_id, headers=headers)
            assert first.status_code == repeated.status_code == 200
            assert not runner.state.pinned_facts
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_director_terminal_receipt_failure_retry_does_not_regenerate(tmp_path, monkeypatch):
    c, runner, engine, *_ = await rig(tmp_path)
    class Judge:
        def decide(self, data):
            return {"action": "pick_speaker", "chosen": ["a"], "rationale": "synthetic"}
    try:
        runner.director_judge = Judge()
        runner.state.meta.director_mode = "confirm"
        result = await c.turn_runs.send(runner, req(mentions=[]))
        assert result["turn_run"]["status"] == "awaiting_director"
        commit = c.sessions.commit_state
        async def fail_terminal(*args, **kwargs):
            if kwargs.get("command_receipt") is not None:
                raise OSError("synthetic terminal receipt failure")
            return await commit(*args, **kwargs)
        monkeypatch.setattr(c.sessions, "commit_state", fail_terminal)
        url = f"/api/v1/sessions/{runner.state.meta.id}/director/pending/confirm"
        headers = {"X-Operation-ID": "synthetic-director-confirm"}
        async with client_for(c) as client:
            failed = await client.post(url, headers=headers)
            assert failed.status_code >= 500
            calls = len(engine.contexts)
            assert calls == 1
            assert runner.pending_director is None
            assert await c.sessions.lookup_command(runner.state.meta.id, headers["X-Operation-ID"]) is None
            monkeypatch.setattr(c.sessions, "commit_state", commit)
            repeated = await client.post(url, headers=headers)
            assert repeated.status_code == 200, repeated.text
            assert repeated.json()["turn_run"]["status"] == "completed"
            saved = repeated.json()
            replay = await client.post(url, headers=headers)
            assert replay.json() == saved
            changed = await client.post(url.replace("confirm", "reject"), headers=headers)
            assert changed.status_code == 409
            assert len(engine.contexts) == calls
    finally:
        await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed"])
async def test_director_pending_receipt_restart_reads_saved_turn_without_models(tmp_path, monkeypatch, status):
    from mrp.tests.conftest import make_test_container
    c, runner, engine, *_ = await rig(tmp_path)
    class Judge:
        def decide(self, data):
            return {"action": "pick_speaker", "chosen": ["a"], "rationale": "synthetic"}
    runner.director_judge = Judge()
    runner.state.meta.director_mode = "confirm"
    await c.turn_runs.send(runner, req(mentions=[]))
    pending = runner.pending_director.model_copy(deep=True)
    if status == "failed":
        engine.fail_actor = "a"
    commit = c.sessions.commit_state
    async def failed_receipt(*args, **kwargs):
        if kwargs.get("command_receipt"):
            raise OSError("synthetic terminal receipt lost before commit")
        return await commit(*args, **kwargs)
    monkeypatch.setattr(c.sessions, "commit_state", failed_receipt)
    url = f"/api/v1/sessions/{runner.state.meta.id}/director/pending/confirm"
    headers = {"X-Operation-ID": "synthetic-restarted-director"}
    async with client_for(c) as client:
        response = await client.post(url, headers=headers)
        assert response.status_code >= 500
    assert runner.state.turn_runs[-1].status == status
    calls = len(engine.contexts)
    if status == "failed":
        # Even a recovery snapshot retaining the original proposal cannot retry
        # an already-claimed failed run through the confirmation endpoint.
        runner.pending_director = pending
        await c.persist_session(runner)
    sid = runner.state.meta.id
    await c.aclose()
    c = make_test_container(tmp_path)
    try:
        restored = await c.load_session(sid)
        restored.engines = engine
        engine.fail_actor = None
        async with client_for(c) as client:
            response = await client.post(url, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["turn_run"]["status"] == status
            repeated = await client.post(url, headers=headers)
            assert repeated.json() == response.json()
        assert len(engine.contexts) == calls
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_node_regeneration_body_operation_id_replays_before_old_cas(tmp_path):
    c, runner, engine, *_ = await rig(tmp_path)
    try:
        await c.turn_runs.send(runner, req(mentions=["a"]))
        target = next(message for message in runner.state.messages if message.actor == "a")
        root = f"/api/v1/sessions/{runner.state.meta.id}/messages/{target.id}/regenerate-one"
        body = {"operation_id": "synthetic-node-regeneration",
            "expected_branch_revision": runner.state.meta.branch_revision,
            "expected_fingerprint": target.fingerprint,
            "expected_player_identity_id": runner.state.meta.player_identity_id}
        async with client_for(c) as client:
            first = await client.post(root, json=body, headers={"X-Operation-ID": body["operation_id"]})
            assert first.status_code == 200, first.text
            calls, revision = len(engine.contexts), runner.state.meta.branch_revision
            replay = await client.post(root, json=body, headers={"X-Operation-ID": body["operation_id"]})
            assert replay.status_code == 200, replay.text
            assert replay.json() == first.json()
            assert replay.headers["X-Branch-Revision"] == first.headers["X-Branch-Revision"]
            conflicting = await client.post(root, json={**body, "expected_fingerprint": "changed"})
            assert conflicting.status_code == 409
        assert len(engine.contexts) == calls
        assert runner.state.meta.branch_revision == revision
    finally:
        await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("action", ["swipe", "continue", "regenerate", "group-reply", "group-swipe", "node"])
async def test_generation_commit_failure_blocks_repeat_after_restart(tmp_path, monkeypatch, legacy, action):
    from mrp.tests.conftest import make_test_container
    from mrp.tests.test_targeted_regeneration import setup
    from mrp.shared.player_identity import ensure_player_identity
    c = make_test_container(tmp_path)
    c.sessions.sqlite_new_stories = not legacy
    runner, engine, _, responder = setup(group=True)
    ensure_player_identity(runner.state)
    c.turn_runs.attach(runner)
    c.conversation_runs.attach(runner)
    await c._register_runner(runner)
    await c.persist_session(runner)
    root = f"/api/v1/sessions/{runner.state.meta.id}"
    body = None
    if action.startswith("group"):
        if action == "group-reply":
            url, body = root + "/groups/group/reply", {"idempotency_key": "synthetic-group-attempt"}
        else:
            async with c.branch_commit.edit(runner):
                result = await c.story_generation.group_reply(runner, runner.state.groups[0], "synthetic-group-base")
            url = root + "/groups/group/messages/" + result["id"] + "/swipe"
    else:
        await c.turn_runs.send(runner, req(mentions=["a"]))
        target = next(message for message in runner.state.messages if message.actor == ("player" if action == "regenerate" else "a"))
        url = root + "/messages/" + target.id + "/" + ("regenerate-one" if action == "node" else action)
        if action == "node":
            body = {"operation_id": "synthetic-reserved-model", "expected_branch_revision": runner.state.meta.branch_revision,
                "expected_fingerprint": target.fingerprint,
                "expected_player_identity_id": runner.state.meta.player_identity_id}
    commit = c.sessions.commit_state
    async def fail_final(*args, **kwargs):
        if kwargs.get("command_receipt") is not None:
            raise OSError("synthetic story commit failure after the model returned")
        return await commit(*args, **kwargs)
    monkeypatch.setattr(c.sessions, "commit_state", fail_final)
    headers = {"X-Operation-ID": "synthetic-reserved-model"}
    before = len(engine.contexts) + len(responder.contexts)
    async with client_for(c) as client:
        first = await client.post(url, json=body, headers=headers)
        assert first.status_code >= 500, first.text
    calls = len(engine.contexts) + len(responder.contexts)
    assert calls > before
    assert await c.sessions.lookup_command(runner.state.meta.id, headers["X-Operation-ID"]) is None
    sid = runner.state.meta.id
    await c.aclose()
    c = make_test_container(tmp_path)
    try:
        restored = await c.load_session(sid)
        restored.engines, restored.group_responder = engine, responder
        async with client_for(c) as client:
            repeat = await client.post(url, json=body, headers=headers)
            assert repeat.status_code == 409, repeat.text
            assert repeat.json()["detail"]["code"] == "operation_incomplete"
            # Another action cannot acquire the abandoned model identity.
            settings = await client.patch(root, json={"title": "synthetic changed title"}, headers=headers)
            assert settings.status_code == 409, settings.text
            assert settings.json()["detail"]["code"] == "operation_conflict"
            target_id = restored.state.messages[0].id if restored.state.messages else "synthetic-missing-message"
            deletion = await client.delete(root + "/messages/" + target_id, headers=headers)
            assert deletion.status_code == 409, deletion.text
            assert deletion.json()["detail"]["code"] == "operation_conflict"
            sending = await client.post(root + "/messages", json={**req(mentions=["a"]).model_dump(mode="json"),
                "operation_id": headers["X-Operation-ID"]})
            assert sending.status_code == 409, sending.text
            assert sending.json()["detail"]["code"] == "operation_conflict"
            director = await client.post(root + "/director/pending/confirm", headers=headers)
            assert director.status_code == 409, director.text
            assert director.json()["detail"]["code"] == "operation_conflict"
            assert await c.sessions.lookup_command(sid, headers["X-Operation-ID"]) is None
        assert len(engine.contexts) + len(responder.contexts) == calls
    finally:
        await c.aclose()
