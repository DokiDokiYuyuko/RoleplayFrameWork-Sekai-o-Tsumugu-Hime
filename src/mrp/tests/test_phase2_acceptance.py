"""Independent HTTP acceptance: lost responses must not become duplicate writes.

All states and model dependencies are synthetic. These tests deliberately use a
new application container after the response is lost, rather than an in-memory
cache of the first request.
"""
from contextlib import asynccontextmanager

import httpx
import pytest

from mrp.server.app import create_app
from mrp.shared.models import Message, SessionMeta, SessionState
from mrp.tests.conftest import make_test_container


@asynccontextmanager
async def client_for(container, *, lose_response=False):
    application = create_app(container)

    async def transport_boundary(scope, receive, send):
        async def deliver(message):
            if lose_response and message["type"] == "http.response.start" and message["status"] == 200:
                raise ConnectionError("synthetic dropped successful response at transport boundary")
            await send(message)
        await application(scope, receive, deliver)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=transport_boundary, raise_app_exceptions=not lose_response),
        base_url="http://127.0.0.1", headers={"Origin": "http://127.0.0.1"},
    ) as client:
        yield client


async def seed(container):
    state = SessionState(
        schema_version=3,
        meta=SessionMeta(id="sess-command-acceptance", title="Synthetic branch", character_ids=[]),
        messages=[Message(id="msg-command-acceptance", session_id="sess-command-acceptance",
                          seq=0, turn=1, actor="player", content="before", status="final")],
    )
    await container.sessions.save_state(state)
    return state.meta.id, state.messages[0].id, state.meta.branch_revision


@pytest.mark.asyncio
async def test_durable_edit_replays_after_lost_response_and_container_restart(tmp_path):
    container = make_test_container(tmp_path)
    sid, mid, revision = await seed(container)
    headers = {"X-Operation-ID": "acceptance-lost-response"}
    payload = {"content": "durable edit",
               "expected_branch_revision": revision}
    url = f"/api/v1/sessions/{sid}/messages/{mid}"

    try:
        async with client_for(container, lose_response=True) as client:
            first = await client.patch(url, json=payload, headers=headers)
            assert first.status_code >= 500
        saved = await container.sessions.load_state(sid)
        assert saved.messages[0].content == "durable edit"
        committed_revision = saved.meta.branch_revision
        assert committed_revision == revision + 1
        with container.sessions.story_db.connect() as db:
            recorded_events = db.execute("SELECT id,branch_id,revision,event FROM outbox ORDER BY id").fetchall()
    finally:
        await container.aclose()

    restarted = make_test_container(tmp_path)
    try:
        async with client_for(restarted) as client:
            replay = await client.patch(url, json=payload, headers=headers)
            assert replay.status_code == 200, replay.text
            assert replay.json()["content"] == "durable edit"
            assert replay.headers["X-Command-ID"] == headers["X-Operation-ID"]
            assert int(replay.headers["X-Branch-Revision"]) == committed_revision
            repeated = await client.patch(url, json=payload, headers=headers)
            assert repeated.json() == replay.json()
            conflicting = await client.patch(url, json={**payload, "content": "different request"}, headers=headers)
            assert conflicting.status_code == 409
            assert conflicting.json()["detail"]["code"] == "operation_conflict"
        assert (await restarted.sessions.load_state(sid)).meta.branch_revision == committed_revision
        with restarted.sessions.story_db.connect() as db:
            assert db.execute("SELECT id,branch_id,revision,event FROM outbox ORDER BY id").fetchall() == recorded_events
    finally:
        await restarted.aclose()


@pytest.mark.asyncio
async def test_settings_receipt_replays_original_result_after_later_edit(tmp_path):
    container = make_test_container(tmp_path)
    try:
        sid, _, revision = await seed(container)
        url = f"/api/v1/sessions/{sid}"
        headers = {"X-Operation-ID": "acceptance-settings-first"}
        first_request = {"title": "first title",
                         "expected_branch_revision": revision}
        async with client_for(container) as client:
            first = await client.patch(url, json=first_request, headers=headers)
            assert first.status_code == 200, first.text
            saved = await container.sessions.load_state(sid)
            later = await client.patch(url, headers={"X-Operation-ID": "acceptance-settings-later"},
                json={"title": "later title", "expected_branch_revision": saved.meta.branch_revision})
            assert later.status_code == 200, later.text
            replay = await client.patch(url, json=first_request, headers=headers)
            assert replay.status_code == 200, replay.text
            assert replay.json() == first.json()
            assert replay.headers["X-Command-ID"] == headers["X-Operation-ID"]
            assert replay.headers["X-Branch-Revision"] == first.headers["X-Branch-Revision"]
            assert int(replay.headers["X-Branch-Revision"]) == revision + 1
        saved = await container.sessions.load_state(sid)
        assert saved.meta.title == "later title"
        assert saved.meta.branch_revision == revision + 2
    finally:
        await container.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("sqlite", [True, False])
async def test_deleted_target_replays_while_busy_and_rejects_new_target(tmp_path, monkeypatch, sqlite):
    container = make_test_container(tmp_path)
    container.sessions.sqlite_new_stories = sqlite
    try:
        sid, mid, revision = await seed(container)
        url = f"/api/v1/sessions/{sid}/messages/{mid}?expected_branch_revision={revision}"
        headers = {"X-Operation-ID": "acceptance-delete"}
        async with client_for(container) as client:
            first = await client.delete(url, headers=headers)
            assert first.status_code == 200, first.text
            runner = await container.load_session(sid)
            monkeypatch.setattr(runner.runtime, "active_turn_run_id", "synthetic-active-run")
            assert runner.busy()
            repeated = await client.delete(url, headers=headers)
            assert repeated.status_code == 200, repeated.text
            assert repeated.json() == first.json()
            assert repeated.headers["X-Branch-Revision"] == first.headers["X-Branch-Revision"]
            different = await client.delete(
                f"/api/v1/sessions/{sid}/messages/another-target?expected_branch_revision={revision}", headers=headers)
            assert different.status_code == 409, different.text
            new_while_busy = await client.delete(url, headers={"X-Operation-ID": "acceptance-new-while-busy"})
            assert new_while_busy.status_code == 409, new_while_busy.text
            assert new_while_busy.json()["detail"]["code"] == "branch_busy"
        saved = await container.sessions.load_state(sid)
        assert saved.messages == []
        assert saved.meta.branch_revision == revision + 1
    finally:
        await container.aclose()
