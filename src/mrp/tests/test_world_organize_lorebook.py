"""Synthetic worldbook organizer checks through its real validation and commit path."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from mrp.lorebook_generation.tools import call_tool
from mrp.tests.test_world_organize import (
    ENDPOINT, SOURCE, commit_body, make_world, protected_world, ready_job, start_job, wait_job,
)


def lore_candidate(source, key="Glass Canal", *, body=SOURCE):
    return {
        "payload": {"keys": [key], "content": body, "extensions": {}},
        "source_refs": [{"source_id": source["id"], "quote": source["content"][:100]}],
        "positive_examples": [f"Explain the {key}."],
        "negative_examples": ["We will walk towards the distant mountain tomorrow."],
        "rationale": "Retain the supplied conditions and limitations.", "risk_notes": [],
    }


def install_lore_worker(client, output=None, *, read_with_tools=False):
    container = client.app.state.container
    calls = []

    async def worker(**kwargs):
        task_file = Path(kwargs["task_file"])
        snapshot = json.loads(task_file.read_text(encoding="utf-8"))
        index = int(task_file.stem.rsplit("-", 1)[-1])
        calls.append({**kwargs, "snapshot": snapshot, "batch_index": index})
        if read_with_tools:
            call_tool(task_file, "list_sources", {"page": 0, "limit": 80})
            for source in snapshot["sources"]:
                offset = 0
                while True:
                    read = call_tool(task_file, "read_source", {"source_id": source["id"], "offset": offset, "max_chars": 3000})
                    if read["next_offset"] is None:
                        break
                    offset = read["next_offset"]
        value = output(snapshot, index, len(calls)) if callable(output) else output
        return value if value is not None else json.dumps({"entries": [lore_candidate(snapshot["sources"][0])]})

    container.lorebook_generation.worker.run = worker
    return container, calls


def linked_book(client, world):
    entries = [
        {"uid": 1, "keys": ["Silver Moth"], "content": "Silver moths previously gathered at the eastern canal bank.",
         "extensions": {"custom": "retain-one", "mrp.archive_sources": [{"source_id": "unselected-archive", "quote": "Old unrelated source."}]}},
        {"uid": 2, "keys": ["Old Bridge"], "content": "The old bridge has a toll of three silver coins.",
         "extensions": {"custom": "retain-two"}},
        {"uid": 3, "keys": ["Northern Railway"], "content": "The northern railway operates twice a week.",
         "enabled": True, "extensions": {"custom": "unrelated", "mrp.archive_sources": [{"source_id": world["archive_records"][2]["id"]}]}},
    ]
    response = client.post("/api/v1/lorebooks", json={"book": {"name": "Synthetic Existing Book", "entries": entries}})
    assert response.status_code == 200, response.text
    book = response.json()
    linked = client.post(f"/api/v1/worlds/{world['id']}/lorebooks", json={
        "expected_revision": world["revision"], "book_id": book["id"],
    })
    assert linked.status_code == 200, linked.text
    return linked.json(), book


def test_lorebook_job_reads_only_paste_and_checked_references_and_leaves_assets_untouched(mrp_client):
    world = make_world(mrp_client)
    container, calls = install_lore_worker(mrp_client, read_with_tools=True)
    job = ready_job(mrp_client, world, category="lorebook", new_lorebook_name="Synthetic New Book")
    assert len(job["drafts"]) == 1
    assert job["drafts"][0]["kind"] == "lorebook" and job["drafts"][0]["action"] == "add"
    assert job["drafts"][0]["simulation"]["valid"]
    snapshot = calls[0]["snapshot"]
    assert [row["id"] for row in snapshot["sources"]] == ["pasted-source"]
    assert snapshot["sources"][0]["content"] == SOURCE
    assert snapshot.get("related_lorebooks") == []
    assert not snapshot.get("core_brief")
    assert container.lorebooks == {}
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world
    coverage = [row for batch in job["reading_coverage"].values() for row in batch]
    assert coverage and all(row["reading_verified"] for row in coverage)


def test_partial_pasted_source_adds_to_existing_book_without_disabling_unrelated_entries(mrp_client):
    world, book = linked_book(mrp_client, make_world(mrp_client))
    install_lore_worker(mrp_client)
    job = ready_job(mrp_client, world, category="lorebook", target_lorebook_id=book["id"])
    assert all(row["action"] == "add" for row in job["drafts"])
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job))
    assert response.status_code == 200, response.text
    saved = mrp_client.get(f"/api/v1/lorebooks/{book['id']}").json()
    assert saved["entries"][:len(book["entries"])] == book["entries"]
    assert len(saved["entries"]) == len(book["entries"]) + 1
    assert all(row["enabled"] for row in saved["entries"])
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world


def test_lorebook_replacement_needs_each_item_confirmation_before_any_batch_write(mrp_client):
    world, book = linked_book(mrp_client, make_world(mrp_client))

    def output(snapshot, index, count):
        source = snapshot["sources"][0]
        return json.dumps({"entries": [
            lore_candidate(source, "Glass Canal"), lore_candidate(source, "Third Bell"),
        ]})

    install_lore_worker(mrp_client, output)
    job = ready_job(mrp_client, world, category="lorebook", target_lorebook_id=book["id"])
    for uid, draft in enumerate(job["drafts"], start=1):
        changed = mrp_client.patch(f"{ENDPOINT}/{job['id']}/drafts/{draft['id']}", json={
            "expected_revision": draft["revision"], "payload": draft["payload"],
            "action": "replace", "target_uid": uid,
        })
        assert changed.status_code == 200, changed.text
    job = mrp_client.get(f"{ENDPOINT}/{job['id']}").json()
    ids = [row["id"] for row in job["drafts"]]
    denied = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job, approved=[ids[0]]))
    assert denied.status_code == 400, denied.text
    assert mrp_client.get(f"/api/v1/lorebooks/{book['id']}").json() == book
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world
    approved = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job, approved=ids))
    assert approved.status_code == 200, approved.text
    saved = mrp_client.get(f"/api/v1/lorebooks/{book['id']}").json()
    assert len(saved["entries"]) == len(book["entries"])
    assert saved["entries"][2] == book["entries"][2]
    assert saved["entries"][0]["content"] == saved["entries"][1]["content"] == SOURCE


def test_existing_book_revision_conflict_preserves_all_pending_additions(mrp_client):
    world, book = linked_book(mrp_client, make_world(mrp_client))
    install_lore_worker(mrp_client)
    job = ready_job(mrp_client, world, category="lorebook", target_lorebook_id=book["id"])
    edited = mrp_client.patch(f"/api/v1/lorebooks/{book['id']}/entries/3", json={
        "expected_revision": book["revision"], "content": "Manually reviewed railway schedule must survive.",
    })
    assert edited.status_code == 200, edited.text
    current_book = mrp_client.get(f"/api/v1/lorebooks/{book['id']}").json()
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job))
    assert response.status_code == 409, response.text
    assert mrp_client.get(f"/api/v1/lorebooks/{book['id']}").json() == current_book
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world
    assert all(row["status"] == "review" for row in mrp_client.get(f"{ENDPOINT}/{job['id']}").json()["drafts"])


def test_private_pasted_source_cannot_publish_lorebook_entries_by_forged_extension(mrp_client):
    world = make_world(mrp_client)

    def output(snapshot, index, count):
        candidate = lore_candidate(snapshot["sources"][0])
        candidate["payload"]["extensions"]["mrp.runtime_scope"] = "shared"
        return json.dumps({"entries": [candidate]})

    container, _ = install_lore_worker(mrp_client, output)
    job = ready_job(mrp_client, world, category="lorebook", source_visibility="private")
    assert job["drafts"][0]["runtime_scope"] == "author"
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job))
    assert response.status_code == 200, response.text
    book = next(iter(container.lorebooks.values())).model_dump(mode="json")
    assert book["entries"][0]["extensions"]["mrp.runtime_scope"] == "author"
    saved_world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    assert protected_world(saved_world) == protected_world(world)
    assert saved_world["archive_records"] == world["archive_records"]


def test_lorebook_multiple_batches_resume_preserves_ids_and_reaches_source_tail(mrp_client):
    world = make_world(mrp_client)
    source_text = "Opening fact about the canal.\n" + "A distinct synthetic mechanism has an explicit cost and exception.\n" * 550
    source_text += "FINAL FACT: The tail observatory is closed at the fourth bell."
    fail = {"enabled": True}

    def output(snapshot, index, count):
        if index == 1 and fail["enabled"]:
            return "{truncated"
        source = snapshot["sources"][0]
        return json.dumps({"entries": [lore_candidate(
            source, f"Canal Topic {index}-{number}",
            body=f"Topic {index}-{number}: " + source["content"][-300:],
        ) for number in range(20)]})

    container, calls = install_lore_worker(mrp_client, output, read_with_tools=True)
    job = wait_job(mrp_client, start_job(mrp_client, world, category="lorebook", source_text=source_text)["id"])
    assert job["status"] == "failed", job.get("errors")
    assert len(job["drafts"]) == 20
    initial_ids = [row["id"] for row in job["drafts"]]
    assert [row["batch_index"] for row in calls] == [0, 1, 1]
    fail["enabled"] = False
    resumed = mrp_client.post(f"{ENDPOINT}/{job['id']}/resume", json={})
    assert resumed.status_code == 200, resumed.text
    job = wait_job(mrp_client, job["id"])
    assert job["status"] == "review", job.get("errors")
    assert len(job["drafts"]) > 20
    assert [row["id"] for row in job["drafts"][:20]] == initial_ids
    assert [row["batch_index"] for row in calls].count(0) == 1
    assert any(source_text[-60:] in row["payload"]["content"] for row in job["drafts"])
    assert job["source_text"] == source_text
    child = container.lorebook_generation.get(job["child_job_id"])
    assert len(child["completed_batches"]) == child["batch_total"]
    assert container.lorebooks == {}
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world


def test_new_book_commit_journal_resumes_after_book_saved_without_duplicate_entries(mrp_client, monkeypatch):
    world = make_world(mrp_client)
    container, _ = install_lore_worker(mrp_client)
    job = ready_job(mrp_client, world, category="lorebook")
    original_save = container.world_registry.save_sync
    interrupted = {"once": True}

    def fail_world_after_book_saved(model):
        if interrupted["once"]:
            interrupted["once"] = False
            raise OSError("Synthetic interruption between book and world persistence")
        return original_save(model)

    monkeypatch.setattr(container.world_registry, "save_sync", fail_world_after_book_saved)
    request = commit_body(job, operation_id="recover-new-book")
    with pytest.raises(OSError, match="Synthetic interruption"):
        mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=request)
    assert len(container.lorebooks) == 1
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world
    monkeypatch.setattr(container.world_registry, "save_sync", original_save)
    container.lorebook_registry.load_all_sync()
    container.world_registry.load_all_sync()
    assert container.world_organize.recover_commits() == []
    saved_world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    assert len(saved_world["lorebook_ids"]) == 1
    assert protected_world(saved_world) == protected_world(world)
    saved_book = container.lorebooks[saved_world["lorebook_ids"][0]].model_dump(mode="json")
    assert len(saved_book["entries"]) == 1
    repeated = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=request)
    assert repeated.status_code == 200, repeated.text
    assert len(container.lorebooks) == 1
    assert container.lorebooks[saved_book["id"]].model_dump(mode="json") == saved_book


def test_lorebook_batch_rejects_quote_from_unread_tail_and_recovers(mrp_client):
    world = make_world(mrp_client)
    source_text = "HEAD FACT: The canal opens at low tide.\n" + "A synthetic district rule includes a cost and exception.\n" * 550
    tail = "TAIL FACT: The last observatory closes after the fourth silver bell."
    source_text += tail
    failing = {"enabled": True}

    def output(snapshot, index, count):
        source = snapshot["sources"][0]
        candidate = lore_candidate(source, f"Canal Batch {index}", body=source["content"][-300:])
        if index == 0 and failing["enabled"]:
            assert tail not in source["content"]
            candidate["source_refs"][0]["quote"] = tail
        return json.dumps({"entries": [candidate]})

    container, calls = install_lore_worker(mrp_client, output, read_with_tools=True)
    job = wait_job(mrp_client, start_job(mrp_client, world, category="lorebook", source_text=source_text)["id"])
    assert job["status"] == "failed", job
    assert [row["batch_index"] for row in calls] == [0, 0]
    assert job["drafts"] == [] and job["errors"]
    assert job["source_text"] == source_text
    assert container.lorebooks == {}
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world
    failing["enabled"] = False
    resumed = mrp_client.post(f"{ENDPOINT}/{job['id']}/resume", json={})
    assert resumed.status_code == 200, resumed.text
    recovered = wait_job(mrp_client, job["id"])
    assert recovered["status"] == "review", recovered.get("errors")
    assert len(recovered["drafts"]) > 1
    assert any(tail in row["payload"]["content"] for row in recovered["drafts"])
    assert recovered["source_text"] == source_text
    assert container.lorebooks == {}
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world
