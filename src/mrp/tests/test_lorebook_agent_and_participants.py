from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from mrp.lorebook_generation.tools import _dispatch, call_tool
from mrp.lorebook_generation.validation import validate_candidate
from mrp.shared.models import Message
from mrp.tests.test_app_factory import _import_char, _new_session


def test_add_library_character_to_story_is_snapshot_and_enters_without_old_history(mrp_client: TestClient):
    container = mrp_client.app.state.container
    original_id = _import_char(mrp_client, "Original")
    added_id = _import_char(mrp_client, "Newcomer", "I arrived later.")
    session_id = _new_session(mrp_client, original_id)
    before = mrp_client.get(f"/api/v1/sessions/{session_id}").json()
    old_messages = list(before["messages"])
    library_before = mrp_client.get(f"/api/v1/characters/{added_id}").json()

    response = mrp_client.post(
        f"/api/v1/sessions/{session_id}/participants",
        json={"character_id": added_id, "entry_brief": "I just entered through the north gate."},
    )
    assert response.status_code == 200, response.text
    state = response.json()["state"]
    assert response.json()["added"] is True
    assert added_id in state["meta"]["character_ids"]
    assert len([row for row in state["characters"] if row["id"] == added_id]) == 1
    assert state["character_entry_briefs"][added_id] == "I just entered through the north gate."
    assert state["character_joined_at_seq"][added_id] == max(row["seq"] for row in old_messages) + 1
    scene = next(row for row in state["scenes"] if row["id"] == state["active_scene_id"])
    assert added_id in scene["member_ids"]
    assert state["messages"] == old_messages

    runner = container.runners[session_id]
    actor = runner.state.character(added_id)
    assert actor is not None
    assert runner.state.visible_messages_for(added_id) == []
    import asyncio
    injections = asyncio.run(runner.context_builder.build_injections(actor, turn=2))
    assert any(row.entry_id == f"character-entry:{added_id}" and "north gate" in row.content for row in injections)

    library_after = mrp_client.get(f"/api/v1/characters/{added_id}").json()
    assert library_after == library_before
    duplicate = mrp_client.post(
        f"/api/v1/sessions/{session_id}/participants",
        json={"character_id": added_id},
    )
    assert duplicate.status_code == 200 and duplicate.json()["added"] is False
    assert len([row for row in duplicate.json()["state"]["characters"] if row["id"] == added_id]) == 1

    runner.state.messages.append(Message(
        session_id=session_id, seq=state["character_joined_at_seq"][added_id],
        turn=2, actor="player", content="Welcome.",
    ))
    visible = runner.state.visible_messages_for(added_id)
    assert [row.content for row in visible] == ["Welcome."]


def test_lorebook_agent_mcp_tools_are_task_scoped_and_validate_triggers(tmp_path: Path):
    source_text = (
        "Name: moon tide crystal\\nAliases: tide crystal\\n"
        "Summary: Moon tide crystals appear only during a full-moon high tide.\\n"
        "Body: They absorb moonlight from the sea, cool to a pale blue, and store a brief charge of tidal magic."
    )
    snapshot = {
        "world_id": "world-demo", "world_title": "Tide World", "world_revision": 3,
        "sources": [{
            "id": "archive-crystal", "kind": "biology", "title": "Moon Tide Crystal",
            "revision": 2, "content": source_text, "char_count": len(source_text), "excerpt": source_text[:80],
        }],
        "target_lorebook": None,
    }
    task_file = tmp_path / "snapshot.json"
    task_file.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    listed = call_tool(task_file, "list_sources", {"page": 0, "limit": 20})
    assert listed["sources"][0]["id"] == "archive-crystal"
    assert call_tool(task_file, "read_source", {"source_id": "archive-crystal", "max_chars": 40})["next_offset"] == 40
    assert call_tool(task_file, "search_sources", {"query": "full-moon"})["results"]
    try:
        call_tool(task_file, "read_source", {"source_id": "outside-task"})
    except ValueError:
        pass
    else:
        raise AssertionError("MCP accepted a source outside this task snapshot")

    candidate = {
        "payload": {
            "keys": ["moon tide crystal", "tide crystal"],
            "content": "Moon tide crystals appear only during a full-moon high tide. After cooling, they turn pale blue and store brief tidal magic.",
        },
        "source_refs": [{"source_id": "archive-crystal", "quote": "Moon tide crystals appear only during a full-moon high tide."}],
        "positive_examples": ["Where can I find a moon tide crystal after the full-moon high tide?"],
        "negative_examples": ["I want to take a walk outside the city gate tomorrow."],
        "rationale": "Keep the concrete fact behind its named trigger.", "risk_notes": [],
    }
    validated = validate_candidate(candidate, snapshot)
    assert validated["simulation"]["positive"][0]["triggered"] is True
    assert validated["simulation"]["negative"][0]["triggered"] is False

    initialized = _dispatch(str(task_file), {
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"},
    })
    assert initialized["result"]["capabilities"]["tools"] == {}
    tools = _dispatch(str(task_file), {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {row["name"] for row in tools["result"]["tools"]} >= {"read_source", "simulate_trigger", "check_overlap"}
    called = _dispatch(str(task_file), {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "simulate_trigger", "arguments": candidate},
    })
    assert called["result"]["isError"] is False
    assert json.loads(called["result"]["content"][0]["text"])["valid"] is True
    assert json.loads((tmp_path / "telemetry.json").read_text(encoding="utf-8"))["tool_calls"] == 5


def test_task_bound_mcp_server_stdio_process(tmp_path: Path):
    task_file = tmp_path / "snapshot.json"
    task_file.write_text(json.dumps({
        "sources": [{"id": "only-source", "title": "Only Source", "content": "A frozen source for this task."}],
        "target_lorebook": None,
    }), encoding="utf-8")
    server = Path(__file__).parents[1] / "lorebook_generation" / "mcp_server.py"
    env = os.environ.copy()
    env["MRP_LOREBOOK_TASK_FILE"] = str(task_file)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "read_source", "arguments": {"source_id": "only-source"},
        }},
    ]
    process = subprocess.run(
        [sys.executable, str(server)],
        input="".join(json.dumps(row) + "\n" for row in messages),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        cwd=Path(__file__).resolve().parents[3],
        env=env,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert [row["id"] for row in responses] == [1, 2, 3]
    assert responses[1]["result"]["tools"]
    assert "frozen source" in json.loads(responses[2]["result"]["content"][0]["text"])["text"]


def test_worldbook_agent_review_commit_links_book_to_world_without_live_model(mrp_client: TestClient):
    container = mrp_client.app.state.container
    world_response = mrp_client.post("/api/v1/worlds", json={"title": "Tide World", "core_brief": "Moon tides affect coastal magic."})
    assert world_response.status_code == 200, world_response.text
    world = world_response.json()
    archive_response = mrp_client.post(f"/api/v1/worlds/{world['id']}/archive", json={
        "expected_revision": world["revision"],
        "record": {
            "kind": "background", "title": "Moon Tide Crystal", "aliases": ["Tide Crystal"],
            "tags": ["Moon Tide", "Magic Material"], "summary": "A pale-blue crystal found at a full-moon high tide.",
            "body": "Moon tide crystals absorb moonlight from the sea, cool to a pale blue, and store a brief charge of tidal magic.",
            "visibility": "public", "kind_data": {"section": "other"},
        },
    })
    assert archive_response.status_code == 200, archive_response.text
    world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    source = world["archive_records"][0]
    quote = "Moon tide crystals absorb moonlight"

    async def fake_agent_run(**kwargs):
        frozen = json.loads(Path(kwargs["task_file"]).read_text(encoding="utf-8"))
        assert frozen["world_revision"] == world["revision"]
        assert any(row["id"] == source["id"] for row in frozen["sources"])
        core_source = next(row for row in frozen["sources"] if row["kind"] == "world_core")
        assert core_source["content"] == "Moon tides affect coastal magic."
        return json.dumps({"entries": [
            {
                "payload": {
                    "keys": ["Moon Tide Crystal", "Tide Crystal"],
                    "content": "Moon tide crystals absorb moonlight from the sea, cool to a pale blue, and store a brief charge of tidal magic.",
                },
                "source_refs": [{"source_id": source["id"], "quote": quote}],
                "positive_examples": ["What can a moon tide crystal store?"],
                "negative_examples": ["Let's go for a walk outside the city tomorrow."],
                "rationale": "Keep this concrete fact behind a clear setting trigger.", "risk_notes": [],
            },
            {
                "payload": {
                    "keys": ["Moon tides", "coastal magic"],
                    "content": "Moon tides affect coastal magic.",
                },
                "source_refs": [{"source_id": core_source["id"], "quote": core_source["content"]}],
                "positive_examples": ["Moon tides affect coastal magic near the shore."],
                "negative_examples": ["A sunny afternoon in the mountains."],
                "rationale": "Preserve the selected world overview as a source-backed entry.", "risk_notes": [],
            },
        ]}, ensure_ascii=False)

    container.lorebook_generation.worker.run = fake_agent_run
    job_response = mrp_client.post("/api/v1/lorebook-agent-jobs", json={
        "world_id": world["id"], "source_ids": [source["id"]], "include_core_brief": True,
        "new_lorebook_name": "Tide World Lorebook", "goal": "Organize the crystal details",
    })
    assert job_response.status_code == 200, job_response.text
    job_id = job_response.json()["id"]
    for _ in range(30):
        current = mrp_client.get(f"/api/v1/lorebook-agent-jobs/{job_id}").json()
        if current["status"] in {"review", "failed"}:
            break
        time.sleep(0.01)
    assert current["status"] == "review", json.dumps(current, ensure_ascii=True)
    assert current["drafts"] and current["drafts"][0]["simulation"]["valid"]
    draft = current["drafts"][0]
    committed = mrp_client.post(
        f"/api/v1/lorebook-agent-jobs/{job_id}/drafts/{draft['id']}/commit",
        json={"expected_revision": draft["revision"]},
    )
    assert committed.status_code == 200, committed.text
    book = committed.json()["book"]
    assert book["entries"][0]["keys"] == ["Moon Tide Crystal", "Tide Crystal"]
    updated_world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    assert book["id"] in updated_world["lorebook_ids"]
    again = mrp_client.post(
        f"/api/v1/lorebook-agent-jobs/{job_id}/drafts/{draft['id']}/commit",
        json={"expected_revision": draft["revision"]},
    )
    assert again.status_code == 200
    assert len(container.lorebooks[book["id"]].entries) == 1

    skill = Path(__file__).parents[1] / "lorebook_generation" / "skills" / "worldbook" / "SKILL.md"
    assert skill.is_file() and "name:" in skill.read_text(encoding="utf-8")
