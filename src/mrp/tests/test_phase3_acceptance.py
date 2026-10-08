"""Independent transport and visibility acceptance for entity publication."""
import asyncio
import json

import pytest

from mrp.tests.conftest import make_test_container
from mrp.tests.test_phase2_acceptance import client_for


async def seed_character(container):
    async with client_for(container) as client:
        result = await client.post("/api/v1/characters/import", files={"file": (
            "synthetic.json", json.dumps({"name": "Synthetic guide", "first_mes": "Welcome."}).encode(), "application/json")})
        assert result.status_code == 200, result.text
        return result.json()["id"]


@pytest.mark.asyncio
async def test_create_response_loss_replays_after_restart_without_rerunning_creation(tmp_path, monkeypatch):
    container = make_test_container(tmp_path)
    payload = {"title": "Synthetic creation", "character_ids": [await seed_character(container)]}
    headers = {"X-Operation-ID": "acceptance-create-lost-response"}
    try:
        async with client_for(container, lose_response=True) as client:
            response = await client.post("/api/v1/sessions", json=payload, headers=headers)
            assert response.status_code >= 500, response.text
        branches = await container.sessions.list_summaries()
        assert len(branches) == 1
        identity = branches[0].id
    finally:
        await container.aclose()

    restarted = make_test_container(tmp_path)
    async def forbidden(*args, **kwargs):
        raise AssertionError("A replay must never invoke the creation use case again")
    monkeypatch.setattr(restarted, "create_session", forbidden)
    try:
        async with client_for(restarted) as client:
            replay = await client.post("/api/v1/sessions", json=payload, headers=headers)
            assert replay.status_code == 200, replay.text
            assert replay.json()["meta"]["id"] == identity
            assert replay.headers["X-Command-ID"] == headers["X-Operation-ID"]
            assert int(replay.headers["X-Branch-Revision"]) == replay.json()["meta"]["branch_revision"]
            repeat = await client.post("/api/v1/sessions", json=payload, headers=headers)
            assert repeat.json() == replay.json()
            changed = await client.post("/api/v1/sessions", json={**payload, "character_ids": ["missing-source"]}, headers=headers)
            assert changed.status_code == 409, changed.text
        assert len(await restarted.sessions.list_summaries()) == 1
    finally:
        await restarted.aclose()


@pytest.mark.asyncio
async def test_new_story_is_invisible_after_staging_until_publication(tmp_path, monkeypatch):
    container = make_test_container(tmp_path)
    character_id = await seed_character(container)
    persist = container.persist_session
    staged, release = asyncio.Event(), asyncio.Event()
    identities = []
    async def pause_after_persist(runner):
        await persist(runner)
        identities.append(runner.state.meta.id)
        staged.set()
        await release.wait()
    monkeypatch.setattr(container, "persist_session", pause_after_persist)
    try:
        async with client_for(container) as client:
            operation = asyncio.create_task(client.post("/api/v1/sessions",
                json={"title": "Hidden synthetic story", "character_ids": [character_id]},
                headers={"X-Operation-ID": "acceptance-hidden-create"}))
            try:
                await asyncio.wait_for(staged.wait(), 5)
                assert identities
                assert await container.sessions.list_summaries() == []
                assert await container.sessions.load_state_readonly(identities[0]) is None
                assert identities[0] not in container.runners
                hidden = await client.get(f"/api/v1/sessions/{identities[0]}/view")
                assert hidden.status_code == 404, hidden.text
            finally:
                release.set()
            response = await asyncio.wait_for(operation, 5)
            assert response.status_code == 200, response.text
            assert [row.id for row in await container.sessions.list_summaries()] == [identities[0]]
    finally:
        release.set()
        await container.aclose()


@pytest.mark.asyncio
async def test_fork_response_loss_replays_after_restart_without_reading_parent(tmp_path, monkeypatch):
    container = make_test_container(tmp_path)
    try:
        async with client_for(container) as client:
            parent = await client.post("/api/v1/sessions", json={
                "title": "Synthetic parent", "character_ids": [await seed_character(container)]})
            assert parent.status_code == 200, parent.text
            state = parent.json()
        path = f"/api/v1/branches/{state['meta']['id']}/fork"
        payload = {"message_id": state["messages"][-1]["id"], "title": "Synthetic child",
                   "expected_revision": state["meta"]["branch_revision"],
                   "idempotency_key": "acceptance-fork-lost-response"}
        async with client_for(container, lose_response=True) as client:
            lost = await client.post(path, json=payload)
            assert lost.status_code >= 500, lost.text
        branches = await container.sessions.list_summaries()
        assert len(branches) == 2
        child = next(row.id for row in branches if row.id != state["meta"]["id"])
    finally:
        await container.aclose()
    restarted = make_test_container(tmp_path)
    async def forbidden(*args, **kwargs):
        raise AssertionError("Fork replay must precede source loading and reconstruction")
    monkeypatch.setattr(restarted.worldlines, "fork_message", forbidden)
    try:
        async with client_for(restarted) as client:
            replay = await client.post(path, json=payload)
            assert replay.status_code == 200, replay.text
            assert replay.json()["branch_id"] == child
            assert replay.headers["X-Command-ID"] == payload["idempotency_key"]
            mismatch = await client.post(path, json={**payload, "message_id": "missing-source"})
            assert mismatch.status_code == 409, mismatch.text
        assert len(await restarted.sessions.list_summaries()) == 2
    finally:
        await restarted.aclose()
