"""Capacity, restart and interrupted-journal checks against temporary synthetic state."""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mrp.orchestrator.model_capacity import ModelCapacity
from mrp.server.app import create_app
from mrp.server.container import AppContainer, AppContainerConfig
from mrp.shared.prompt import estimate_tokens
from mrp.storage.atomic import read_json
from mrp.tests.test_world_organize import (
    ENDPOINT, SOURCE, archive_candidate, commit_body, install_archive_worker,
    make_world, protected_world, ready_job, start_job, wait_job,
)
from mrp.tests.test_world_organize_lorebook import install_lore_worker


@pytest.mark.parametrize("adoption", ["uncommitted", "adopted", "manually_edited"])
def test_real_capacity_planner_keeps_all_chunks_and_preserves_completed_draft_on_resume(mrp_client, monkeypatch, adoption):
    world = make_world(mrp_client)
    container = mrp_client.app.state.container
    container._fake_mode = False
    service = container.world_organize
    source_text = "\n\n".join(
        f"FACT {i:03}: Canal district {i} opens after bell {i + 1}; its toll is {i + 2} silver coins. "
        f"Gate {i} closes only during flood condition {i}; exemption applies to caravan {i}."
        for i in range(240)
    ) + "\n\nFINAL FACT: The tail observatory closes after the fourth bell."
    planned = []

    async def capacity(settings, model, **kwargs):
        limit = 5000
        planned.append({"model": model, "limit": limit})
        return ModelCapacity(limit + 1000, limit, 1000, "synthetic-model-catalog")

    monkeypatch.setattr("mrp.world_organize.service.resolve_model_capacity", capacity)
    failing = {"enabled": True}
    calls = []

    async def fake_call(prompt, job_id, attempt):
        job = service._read(job_id)
        index = job["batch_index"]
        batch = read_json(service._directory(job_id) / "batches.json")[index]
        content = batch["sources"][0]["content"]
        calls.append({"index": index, "attempt": attempt, "content": content})
        assert json.dumps(content, ensure_ascii=False)[1:-1] in prompt
        assert estimate_tokens(prompt) + 32 <= 5000
        if index == 1 and failing["enabled"]:
            return '{"archives": [{'
        return json.dumps({"archives": [archive_candidate("Canal Rules", quote=content, body=content)]})

    service._call = fake_call
    job = wait_job(mrp_client, start_job(mrp_client, world, source_text=source_text)["id"])
    assert job["status"] == "failed", job.get("errors")
    assert [row["index"] for row in calls] == [0, 1, 1]
    assert job["completed_batches"] == [0]
    assert len(job["drafts"]) == 1
    kept_id = job["drafts"][0]["id"]
    first_body = job["drafts"][0]["payload"]["body"]
    assert job["source_text"] == source_text
    adopted_draft = None
    adopted_world = world
    if adoption != "uncommitted":
        response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(
            job, operation_id="adopt-successful-first-batch", draft_ids=[kept_id],
        ))
        assert response.status_code == 200, response.text
        adopted_draft = copy.deepcopy(response.json()["drafts"][0])
        adopted_world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
        assert len(adopted_world["archive_records"]) == len(world["archive_records"]) + 1
        assert adopted_draft["status"] == "committed"
    failing["enabled"] = False
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/resume", json={})
    assert response.status_code == 200, response.text
    completed = wait_job(mrp_client, job["id"])
    assert completed["status"] == "review", completed.get("errors")
    assert completed["drafts"][0]["id"] == kept_id
    if adoption == "uncommitted":
        assert len(completed["drafts"]) == 1
        review_draft = completed["drafts"][0]
    else:
        assert completed["drafts"][0] == adopted_draft
        pending = [row for row in completed["drafts"] if row["status"] == "review"]
        assert len(pending) == 1
        review_draft = pending[0]
        assert review_draft["id"] != kept_id
        assert review_draft["action"] == "replace"
        assert review_draft["target_id"] == adopted_world["archive_records"][-1]["id"]
    assert review_draft["payload"]["body"].startswith(first_body)
    assert planned and len(planned) == 1
    assert [row["index"] for row in calls].count(0) == 1
    spans = read_json(service._directory(job["id"]) / "batches.json")
    assert len(spans) > 2
    assert "".join(batch["sources"][0]["content"] for batch in spans) == source_text
    assert len(completed["completed_batches"]) == completed["batch_total"] == len(spans)
    body = review_draft["payload"]["body"]
    assert all(f"FACT {i:03}:" in body for i in range(240))
    assert source_text[-60:] in body
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == adopted_world
    if adoption == "manually_edited":
        target = adopted_world["archive_records"][-1]
        changed = mrp_client.patch(f"/api/v1/worlds/{world['id']}/archive/{target['id']}", json={
            "expected_revision": adopted_world["revision"],
            "record": {**review_draft["payload"], "kind": review_draft["kind"],
                       "body": "A later manual review of the adopted canal facts must survive."},
        })
        assert changed.status_code == 200, changed.text
        denied = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(
            completed, operation_id="adopt-resumed-tail", draft_ids=[review_draft["id"]],
        ))
        assert denied.status_code == 409, denied.text
        assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == changed.json()
        assert mrp_client.get(f"{ENDPOINT}/{job['id']}").json()["drafts"] == completed["drafts"]
    elif adoption == "adopted":
        response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(
            completed, operation_id="adopt-resumed-tail", draft_ids=[review_draft["id"]],
        ))
        assert response.status_code == 200, response.text
        saved_world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
        assert len(saved_world["archive_records"]) == len(world["archive_records"]) + 1
        assert saved_world["archive_records"][-1]["body"] == body
        assert protected_world(saved_world) == protected_world(world)
        assert response.json()["drafts"][0] == adopted_draft


def test_fresh_container_loads_interrupted_input_and_resumes_saved_task(tmp_path):
    container = AppContainer(data_root=tmp_path / "data", config=AppContainerConfig(fake_mode=True))
    started, release = threading.Event(), threading.Event()

    async def slow_call(prompt, job_id, attempt):
        import asyncio
        started.set()
        await asyncio.to_thread(release.wait, 5)
        return json.dumps({"archives": [archive_candidate()]})

    container.world_organize._call = slow_call
    fresh = None
    try:
        with TestClient(create_app(container)) as initial:
            world = make_world(initial)
            created = start_job(initial, world)
            assert started.wait(2)
            initial.portal.call(container.world_organize.close)
            release.set()
        # A restart must stop the first server, not run two writers against one root.
        fresh = AppContainer(data_root=container.data_root, config=AppContainerConfig(fake_mode=True))
        with TestClient(create_app(fresh)) as restarted:
            install_archive_worker(restarted)
            persisted = restarted.get(f"{ENDPOINT}/{created['id']}").json()
            assert persisted["status"] == "interrupted"
            assert persisted["source_text"] == SOURCE
            assert persisted["instruction"] == created["instruction"]
            assert persisted["reference_source_ids"] == []
            response = restarted.post(f"{ENDPOINT}/{created['id']}/resume", json={})
            assert response.status_code == 200, response.text
            job = wait_job(restarted, created["id"])
            assert job["status"] == "review" and len(job["drafts"]) == 2
            assert restarted.get(f"/api/v1/worlds/{world['id']}").json() == world
    finally:
        release.set()
        container.memory_store.close()
        if fresh is not None:
            fresh.memory_store.close()


def test_archive_journal_restores_job_after_asset_saved_and_replays_once(mrp_client, monkeypatch):
    import mrp.lorebook_generation.commits as commits

    world = make_world(mrp_client)
    container, _ = install_archive_worker(mrp_client)
    job = ready_job(mrp_client, world)
    original_write = commits.write_json_atomic
    job_path = container.world_organize._path(job["id"])
    fail = {"enabled": True}

    def fail_job_after_asset_save(path, value, **kwargs):
        if Path(path) == job_path and fail["enabled"]:
            fail["enabled"] = False
            raise OSError("Synthetic lost process before job acknowledgement")
        return original_write(path, value, **kwargs)

    monkeypatch.setattr(commits, "write_json_atomic", fail_job_after_asset_save)
    request = commit_body(job, operation_id="recover-archive-ack")
    with pytest.raises(OSError, match="Synthetic lost process"):
        mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=request)
    saved_world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    assert len(saved_world["archive_records"]) == len(world["archive_records"]) + 2
    assert protected_world(saved_world) == protected_world(world)
    assert container.world_organize._read(job["id"])["status"] == "review"
    monkeypatch.setattr(commits, "write_json_atomic", original_write)
    assert container.world_organize.recover_commits() == []
    assert container.world_organize.get(job["id"])["status"] == "committed"
    replay = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=request)
    assert replay.status_code == 200, replay.text
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == saved_world


def test_interrupted_journal_refuses_to_overwrite_a_subsequent_world_edit(mrp_client, monkeypatch):
    world = make_world(mrp_client)
    container, _ = install_lore_worker(mrp_client)
    job = ready_job(mrp_client, world, category="lorebook")
    original_save = container.world_registry.save_sync

    def interrupted_save(model):
        raise OSError("Synthetic loss before world association")

    monkeypatch.setattr(container.world_registry, "save_sync", interrupted_save)
    request = commit_body(job, operation_id="recover-with-conflict")
    with pytest.raises(OSError):
        mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=request)
    monkeypatch.setattr(container.world_registry, "save_sync", original_save)
    manual = mrp_client.patch(f"/api/v1/worlds/{world['id']}", json={
        "expected_revision": world["revision"], "description": "An independent manual edit after the interruption.",
    })
    assert manual.status_code == 200, manual.text
    failures = container.world_organize.recover_commits()
    assert len(failures) == 1
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == manual.json()
    replay = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=request)
    assert replay.status_code == 409, replay.text
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == manual.json()
    assert len(container.lorebooks) == 1


@pytest.mark.parametrize("input_limit", [16000, 65536])
def test_lorebook_uses_model_capacity_and_saved_plan_on_resume(mrp_client, monkeypatch, input_limit):
    from mrp.tests.test_world_organize_lorebook import lore_candidate

    world = make_world(mrp_client)
    container = mrp_client.app.state.container
    container._fake_mode = False
    source_text = "\n\n".join(
        f"FACT {i:03}: Canal district {i} opens after bell {i + 1}; its toll is {i + 2} silver coins. "
        f"Gate {i} closes only during flood condition {i}; exemption applies to caravan {i}."
        for i in range(240)
    ) + "\n\nTAIL FACT: The last observatory closes after the fourth silver bell."
    planned = []

    async def capacity(settings, model, **kwargs):
        planned.append((model, kwargs))
        return ModelCapacity(input_limit + 2000, input_limit, 2000, "synthetic-model-catalog")

    monkeypatch.setattr("mrp.world_organize.service.resolve_model_capacity", capacity)
    failing = {"enabled": True}

    def output(snapshot, index, count):
        if index == 1 and failing["enabled"]:
            return "{truncated"
        source = snapshot["sources"][0]
        return json.dumps({"entries": [lore_candidate(
            source, f"Canal Capacity Batch {index}", body=source["content"][-300:],
        )]})

    _, calls = install_lore_worker(mrp_client, output, read_with_tools=True)
    job = wait_job(mrp_client, start_job(mrp_client, world, category="lorebook", source_text=source_text)["id"])
    child_service = container.lorebook_generation
    plan_path = child_service.repo.directory(job["child_job_id"]) / "organize-batches.json"
    saved_plan = read_json(plan_path)
    assert job["input_limit"] == input_limit
    if input_limit == 65536:
        assert len(saved_plan) == 1
        assert job["status"] == "review", job.get("errors")
        assert calls[0]["snapshot"]["sources"][0]["content"] == source_text
    else:
        assert len(saved_plan) > 1
        assert job["status"] == "failed", job.get("errors")
        kept_drafts = copy.deepcopy(job["drafts"])
        assert kept_drafts and [row["batch_index"] for row in calls] == [0, 1, 1]
        failing["enabled"] = False
        resumed = mrp_client.post(f"{ENDPOINT}/{job['id']}/resume", json={})
        assert resumed.status_code == 200, resumed.text
        job = wait_job(mrp_client, job["id"])
        assert job["status"] == "review", job.get("errors")
        assert job["drafts"][:len(kept_drafts)] == kept_drafts
        assert [row["batch_index"] for row in calls].count(0) == 1
        assert read_json(plan_path) == saved_plan
    assert len(planned) == 1
    end = 0
    for batch in saved_plan:
        source = batch["sources"][0]
        offset = source.get("source_offset", 0)
        assert offset <= end
        assert source_text[offset:offset + len(source["content"])] == source["content"]
        end = max(end, offset + len(source["content"]))
    assert end == len(source_text)
    child = child_service.get(job["child_job_id"])
    assert len(child["completed_batches"]) == child["batch_total"] == len(saved_plan)
    assert any(source_text[-60:] in row["payload"]["content"] for row in job["drafts"])
    assert all(row["reading_verified"] for rows in job["reading_coverage"].values() for row in rows)
    assert job["source_text"] == source_text and container.lorebooks == {}
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world
