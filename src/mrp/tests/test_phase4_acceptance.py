"""Independent lifecycle acceptance through actual HTTP and restarted containers."""
import asyncio

import pytest

from mrp.tests.conftest import make_test_container
from mrp.tests.test_phase2_acceptance import client_for, seed


async def bounded(awaitable):
    return await asyncio.wait_for(awaitable, 10)


@pytest.mark.asyncio
async def test_whole_story_delete_response_loss_restore_and_stale_trash_generation(tmp_path):
    container = make_test_container(tmp_path)
    sid, _, _ = await seed(container)
    state = await container.sessions.load_state(sid)
    state.meta.story_id = sid
    await container.sessions.save_state(state)
    child = state.model_copy(deep=True)
    child.meta.id = "sess-lifecycle-child"
    child.meta.parent_branch_id = sid
    child.meta.branch_revision = 0
    for message in child.messages:
        message.session_id = child.meta.id
    await container.sessions.save_state(child)
    original = {row.id for row in await container.sessions.list_summaries()}
    try:
        async with client_for(container) as client:
            stories = await bounded(client.get("/api/v1/stories"))
            assert stories.status_code == 200, stories.text
            membership = stories.json()[0]["membership_revision"]
        url = f"/api/v1/stories/{sid}?membership_revision={membership}"
        headers = {"X-Operation-ID": "acceptance-lifecycle-delete"}
        async with client_for(container, lose_response=True) as client:
            lost = await bounded(client.delete(url, headers=headers))
            assert lost.status_code >= 500, lost.text
        assert await container.sessions.list_summaries() == []
    finally:
        await container.aclose()
    container = make_test_container(tmp_path)
    try:
        async with client_for(container) as client:
            replay = await bounded(client.delete(url, headers=headers))
            assert replay.status_code == 200, replay.text
            assert set(replay.json()["branch_ids"]) == original
            generation = replay.json()["generation_id"]
            for identity in original:
                hidden = await bounded(client.get(f"/api/v1/sessions/{identity}/view"))
                assert hidden.status_code == 404, hidden.text
            restored = await bounded(client.post(f"/api/v1/stories/trash/{sid}/restore?generation_id={generation}",
                headers={"X-Operation-ID": "acceptance-lifecycle-restore"}))
            assert restored.status_code == 200, restored.text
            assert {row["new_id"] for row in restored.json()["branches"]} == original
            assert {row.id for row in await container.sessions.list_summaries()} == original
            second = await bounded(client.delete(f"/api/v1/stories/{sid}",
                headers={"X-Operation-ID": "acceptance-lifecycle-delete-again"}))
            assert second.status_code == 200, second.text
            assert second.json()["generation_id"] != generation
            stale = await bounded(client.delete(f"/api/v1/stories/trash/{sid}?generation_id={generation}",
                headers={"X-Operation-ID": "acceptance-lifecycle-stale-purge"}))
            assert stale.status_code == 409, stale.text
            trash = await bounded(client.get("/api/v1/stories/trash"))
            assert trash.status_code == 200, trash.text
            assert trash.json()[0]["generation_id"] == second.json()["generation_id"]
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_memory_http_receipt_and_persisted_event_share_committed_record(tmp_path):
    import json
    from mrp.tests.test_memory_commands import memory_rig, remember_body
    container, runner = await memory_rig(tmp_path)
    try:
        async with client_for(container) as client:
            result = await bounded(client.post(f"/api/v1/sessions/{runner.state.meta.id}/memory/records",
                json=remember_body(runner), headers={"X-Operation-ID": "acceptance-memory-projection"}))
            assert result.status_code == 200, result.text
            from mrp.contracts.commands import MemoryRecordView
            record = MemoryRecordView.model_validate(result.json()["records"][0]).model_dump(mode="json")
            assert record["commit_seq"] > 0
            saved = container.memory_store.record_by_id(record["id"]).model_dump(mode="json")
            assert record == saved
            receipt = await container.sessions.lookup_command(runner.state.meta.id, "acceptance-memory-projection")
            assert receipt["result"]["records"] == [saved]
            with container.sessions.story_db.connect() as db:
                rows = db.execute("SELECT revision,event FROM outbox WHERE branch_id=?", (runner.state.meta.id,)).fetchall()
            events = [(revision,json.loads(raw)) for revision,raw in rows]
            memory = [(revision,event) for revision,event in events if event["event"] == "memory.consolidated"]
            assert len(memory) == 1
            assert memory[0][0] == int(result.headers["X-Branch-Revision"])
            assert memory[0][1]["data"]["records"] == [saved]
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_long_conversation_audit_stays_in_legacy_export_not_reading_or_events(tmp_path):
    from mrp.shared.models import ConversationRun, ConversationStep
    from mrp.contracts.story import project_event
    container = make_test_container(tmp_path)
    sid, mid, _ = await seed(container)
    state = await container.sessions.load_state(sid)
    run = ConversationRun(session_id=sid, operation_id="acceptance-long-conversation", scene_id="scene-synthetic",
        status="completed", seed_message_ids=[mid], last_committed_message_id=mid,
        steps=[ConversationStep(index=index, generation_id=f"gen-{index}", speaker_id="synthetic",
            status="committed", message_id=mid) for index in range(1, 1001)],
        scheduling_trace=[{"synthetic": "trace " * 30} for _ in range(1000)])
    state.conversation_runs = [run]
    state.messages[0].operation_id = run.operation_id
    await container.sessions.save_state(state)
    try:
        async with client_for(container) as client:
            cold = await bounded(client.get(f"/api/v1/sessions/{sid}/view?limit=20"))
            assert cold.status_code == 200, cold.text
            projected = cold.json()["conversation_runs"][0]
            assert not {"steps", "scheduling_trace"} & projected.keys()
            event = project_event("conversation.run.updated", {"run": run.model_dump(mode="json")})
            assert event["run"] == projected
            legacy = await bounded(client.get(f"/api/v1/sessions/{sid}"))
            assert legacy.status_code == 200, legacy.text
            assert len(legacy.json()["conversation_runs"][0]["steps"]) == 1000
            hot = await bounded(client.get(f"/api/v1/sessions/{sid}/view?limit=20"))
            assert hot.status_code == 200, hot.text
            assert hot.json()["conversation_runs"][0] == projected
    finally:
        await container.aclose()


@pytest.mark.asyncio
async def test_committed_delete_hides_all_cached_branches_before_runtime_cleanup(tmp_path, monkeypatch):
    container = make_test_container(tmp_path)
    sid, _, _ = await seed(container)
    state = await container.sessions.load_state(sid)
    state.meta.story_id = sid
    await container.sessions.save_state(state)
    child = state.model_copy(deep=True)
    child.meta.id = "sess-hidden-cleanup-child"
    child.meta.parent_branch_id = sid
    child.meta.branch_revision = 0
    for message in child.messages:
        message.session_id = child.meta.id
    await container.sessions.save_state(child)
    runners = [await container.load_session(identity) for identity in (sid, child.meta.id)]
    entered, release = asyncio.Event(), asyncio.Event()
    async def pause_cleanup():
        entered.set()
        await release.wait()
    for runner in runners:
        monkeypatch.setattr(runner, "aclose", pause_cleanup)
    try:
        async with client_for(container) as client:
            deletion = asyncio.create_task(client.delete(f"/api/v1/stories/{sid}",
                headers={"X-Operation-ID": "acceptance-paused-cleanup"}))
            try:
                await asyncio.wait_for(entered.wait(), 10)
                assert container.runners, "Pause must leave another cached branch to exercise the read boundary"
                identity = next(iter(container.runners))
                for suffix in ("/view", "/setup"):
                    hidden = await bounded(client.get(f"/api/v1/sessions/{identity}{suffix}"))
                    assert hidden.status_code == 404, hidden.text
            finally:
                release.set()
                result = await bounded(deletion)
                assert result.status_code == 200, result.text
    finally:
        release.set()
        await container.aclose()
