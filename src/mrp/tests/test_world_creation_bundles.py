"""Synthetic authoring integration tests; never invokes an upstream model."""
from __future__ import annotations

import asyncio
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mrp.lorebook_generation.commits import fingerprint
from mrp.lorebook_generation.validation import simulation
from mrp.orchestrator.lorebook import LorebookEngine
from mrp.shared.models import Message
from mrp.tests.test_app_factory import _import_char


RAW = "Moon tide crystals store tidal energy only at full moon high tide. Their charge fades after dawn. Salt disrupts their charge."


def install_fake_workers(client):
    container = client.app.state.container
    calls = {"import": 0, "lore": 0}

    async def fake_import(prompt, job_id, attempt):
        calls["import"] += 1
        if '仅返回 {"core_brief":string}' in prompt:
            return json.dumps({"core_brief": "This world has unusual tides."})
        result = {"payload": {"title": "Tide World", "description": "A synthetic coastal setting.",
                              "core_brief": "This world has unusual tides."}, "evidence": RAW[:40]}
        if "addition_text" in prompt:
            result.update(addition_text="Star shells glow softly after the tide recedes.", added_facts=["Star shells glow."])
        return json.dumps(result)

    async def fake_lore(**kwargs):
        calls["lore"] += 1
        snapshot = json.loads(Path(kwargs["task_file"]).read_text(encoding="utf-8"))
        source = snapshot["sources"][0]
        quote = RAW[:60] if RAW[:60] in source["content"] else source["content"][:60]
        return json.dumps({"entries": [{
            "payload": {"keys": ["Moon tide crystal"], "content": RAW},
            "source_refs": [{"source_id": source["id"], "quote": quote}],
            "positive_examples": ["Please explain the moon tide crystal."],
            "negative_examples": ["We walk towards the mountains."],
            "rationale": "Preserve the crystal conditions.", "risk_notes": []}]})

    container.asset_imports._call = fake_import
    container.lorebook_generation.worker.run = fake_lore
    return container, calls


def ready_bundle(client, *, world_id=None, intent="organize", source=RAW, target_book_id=None):
    created = client.post("/api/v1/asset-import-jobs", json={"source": source, "target_kind": "world",
                         "world_id": world_id, "intent": intent, "instruction": "Add glowing shell details."})
    assert created.status_code == 200, created.text
    job = created.json()
    response = client.post(f"/api/v1/asset-import-jobs/{job['id']}/derive-world-runtime",
                           json={"expected_bundle_revision": 1, "target_lorebook_id": target_book_id})
    assert response.status_code == 200, response.text
    for _ in range(200):
        job = client.get(f"/api/v1/asset-import-jobs/{job['id']}").json()
        if job["bundle"]["status"] in {"review", "failed"}:
            break
        time.sleep(0.005)
    assert job["bundle"]["status"] == "review", job
    return job


def commit_request(job, *, shared=None, operation_id="adopt-one", accepted=None):
    bundle = job["bundle"]
    accepted = accepted or ["manuscript", "core", *[row["id"] for row in bundle["entry_proposals"]]]
    return {"operation_id": operation_id, "expected_bundle_revision": bundle["revision"],
            "review_digest": bundle["review_digest"], "expected_world_revision": bundle.get("base_world_revision"),
            "expected_lorebook_revision": bundle.get("target_lorebook_revision"),
            "accepted_proposal_ids": accepted, "shared_proposal_ids": shared or [], "runtime_mode": "compiled"}


def test_manuscript_save_is_exact_zero_ai_and_revisioned(mrp_client):
    container, calls = install_fake_workers(mrp_client)
    created = mrp_client.post("/api/v1/worlds", json={"title": "Raw World"}).json()
    assert created["runtime_policy"] == "raw"
    raw = "  First paragraph.\n\nLast paragraph.  \n"
    response = mrp_client.post(f"/api/v1/worlds/{created['id']}/manuscript", json={
        "expected_revision": created["revision"], "body": raw, "visibility": "public"})
    assert response.status_code == 200, response.text
    world = response.json()
    assert world["archive_records"][0]["body"] == raw
    assert world["manuscript_archive_id"] == world["archive_records"][0]["id"]
    assert calls == {"import": 0, "lore": 0}
    stale = mrp_client.post(f"/api/v1/worlds/{created['id']}/manuscript", json={
        "expected_revision": created["revision"], "body": "stale"})
    assert stale.status_code == 409


def test_pending_world_one_review_scope_provenance_and_idempotency(mrp_client):
    container, calls = install_fake_workers(mrp_client)
    job = ready_bundle(mrp_client)
    assert container.worlds == {} and container.lorebooks == {}
    bundle = job["bundle"]
    entry = bundle["entry_proposals"][0]
    assert entry["runtime_scope"] == "author"
    old_digest = bundle["review_digest"]
    edited = mrp_client.patch(f"/api/v1/asset-import-jobs/{job['id']}/runtime-draft", json={
        "expected_bundle_revision": bundle["revision"], "core_content": "Only the ocean tide is unusual."})
    assert edited.status_code == 200, edited.text
    job = edited.json()
    assert job["bundle"]["review_digest"] != old_digest
    request = commit_request(job, shared=["core"])
    response = mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/commit-bundle", json=request)
    assert response.status_code == 200, response.text
    saved = response.json()
    world, book = saved["world"], saved["book"]
    assert world["runtime_policy"] == "compiled"
    assert world["core_brief"] == "Only the ocean tide is unusual."
    assert world["archive_records"][0]["body"] == RAW
    assert world["archive_records"][0]["visibility"] == "private"
    assert book["entries"][0]["extensions"]["mrp.runtime_scope"] == "author"
    assert book["entries"][0]["extensions"]["mrp.archive_sources"][0]["source_id"] == world["manuscript_archive_id"]
    message = Message(session_id="test", seq=0, turn=1, actor="player", content="Moon tide crystal")
    assert LorebookEngine.scan(container.lorebooks[book["id"]], [message]) == []
    repeated = mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/commit-bundle", json=request)
    assert repeated.status_code == 200, repeated.text
    assert len(container.worlds) == len(container.lorebooks) == 1


def test_scope_rejection_and_public_source_can_be_demoted(mrp_client):
    container, calls = install_fake_workers(mrp_client)
    world = mrp_client.post("/api/v1/worlds", json={"title": "Existing World"}).json()
    world = mrp_client.post(f"/api/v1/worlds/{world['id']}/manuscript", json={
        "expected_revision": world["revision"], "body": RAW, "visibility": "public"}).json()
    other = mrp_client.post("/api/v1/worlds", json={"title": "Other World"}).json()
    other = mrp_client.post(f"/api/v1/worlds/{other['id']}/manuscript", json={
        "expected_revision": other["revision"], "body": "A private unrelated world."}).json()
    created = mrp_client.post("/api/v1/asset-import-jobs", json={"source": RAW, "target_kind": "world", "world_id": world["id"]}).json()
    denied = mrp_client.post(f"/api/v1/asset-import-jobs/{created['id']}/derive-world-runtime", json={
        "expected_bundle_revision": 1, "source_ids": [other["manuscript_archive_id"]]})
    assert denied.status_code == 400
    assert calls == {"import": 0, "lore": 0}
    job = ready_bundle(mrp_client, world_id=world["id"])
    assert job["bundle"]["core_proposal"]["runtime_scope"] == "shared"
    response = mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/commit-bundle", json=commit_request(job))
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["world"]["core_brief"] == ""
    assert saved["world"]["author_core_brief"]
    assert saved["world"]["archive_records"][0]["visibility"] == "private"
    assert saved["book"]["entries"][0]["extensions"]["mrp.runtime_scope"] == "author"


def test_extension_returns_new_prose_without_writing_assets(mrp_client):
    container, calls = install_fake_workers(mrp_client)
    job = ready_bundle(mrp_client, intent="extend")
    draft = next(row for row in job["drafts"] if row["kind"] == "world")
    assert draft["addition_text"].startswith("Star shells")
    assert draft["proposed_source"].startswith(RAW)
    assert job["bundle"]["manuscript_proposal"]["body"].endswith(draft["addition_text"])
    assert container.worlds == {} and container.lorebooks == {}


def test_batches_resume_preserves_more_than_twenty_candidates(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    fail_once = {"value": True}

    async def many_entries(**kwargs):
        index = int(Path(kwargs["task_file"]).stem.split("-")[-1])
        if index == 1 and fail_once["value"]:
            fail_once["value"] = False
            raise RuntimeError("Synthetic interruption")
        snapshot = json.loads(Path(kwargs["task_file"]).read_text(encoding="utf-8"))
        source = snapshot["sources"][0]
        return json.dumps({"entries": [{"payload": {"keys": [f"specific-{index}-{number}"],
             "content": f"Specific fact {index}-{number}: a synthetic detail preserved for this source passage."},
             "source_refs": [{"source_id": source["id"], "quote": source["content"][:60]}],
             "positive_examples": [f"Explain specific-{index}-{number}."], "negative_examples": ["Unrelated mountains."],
             "rationale": "Synthetic per-batch fixture", "risk_notes": []} for number in range(20)]})

    container.lorebook_generation.worker.run = many_entries
    source = ("Synthetic source paragraph contains stable names, conditions and limitations.\n" * 400)
    created = mrp_client.post("/api/v1/asset-import-jobs", json={"source": source, "target_kind": "world"}).json()
    mrp_client.post(f"/api/v1/asset-import-jobs/{created['id']}/derive-world-runtime", json={"expected_bundle_revision": 1})
    for _ in range(300):
        job = mrp_client.get(f"/api/v1/asset-import-jobs/{created['id']}").json()
        if job["bundle"]["status"] == "failed":
            break
        time.sleep(0.005)
    assert len(job["bundle"]["entry_proposals"]) == 20
    first_ids = [row["id"] for row in job["bundle"]["entry_proposals"]]
    resumed = mrp_client.post(f"/api/v1/asset-import-jobs/{created['id']}/resume-world-runtime")
    assert resumed.status_code == 200, resumed.text
    for _ in range(300):
        job = mrp_client.get(f"/api/v1/asset-import-jobs/{created['id']}").json()
        if job["bundle"]["status"] == "review":
            break
        time.sleep(0.005)
    assert job["bundle"]["status"] == "review", job
    assert len(job["bundle"]["entry_proposals"]) > 20
    assert [row["id"] for row in job["bundle"]["entry_proposals"][:20]] == first_ids
    assert job["bundle"]["completed_batches"] == job["bundle"]["batch_total"]


def test_concurrent_bundle_adoption_has_one_winner(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    job = ready_bundle(mrp_client)
    endpoint = f"/api/v1/asset-import-jobs/{job['id']}/commit-bundle"
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda index: mrp_client.post(endpoint,
            json=commit_request(job, operation_id=f"operation-{index}")), [1, 2]))
    assert sorted(response.status_code for response in responses)[0] == 200
    assert len([response for response in responses if response.status_code == 200]) == 1
    assert len(container.worlds) == len(container.lorebooks) == 1


def test_journal_recovery_after_book_saved_preserves_external_edits(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    job = ready_bundle(mrp_client)
    original = container.world_registry.save_sync
    fail_once = {"value": True}

    def interrupted_save(world):
        if fail_once["value"]:
            fail_once["value"] = False
            raise OSError("Synthetic process loss after book write")
        original(world)

    container.world_registry.save_sync = interrupted_save
    request = commit_request(job)
    with pytest.raises(OSError):
        mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/commit-bundle", json=request)
    assert len(container.lorebooks) == 1 and not container.worlds
    container.world_registry.save_sync = original
    # Simulate fresh registry state and service startup recovery, without touching a live server.
    container.lorebook_registry.load_all_sync()
    container.world_registry.load_all_sync()
    assert container.asset_imports.recover_commits() == []
    saved = container.asset_imports.get(job["id"])
    assert saved["bundle"]["status"] == "committed"
    assert len(container.worlds) == len(container.lorebooks) == 1
    assert fingerprint(next(iter(container.lorebooks.values())))


def ready_lore(client, world_id, *, target_book_id=None, source_ids=None, mode="initial"):
    response = client.post("/api/v1/lorebook-agent-jobs", json={"world_id": world_id,
        "target_lorebook_id": target_book_id, "source_ids": source_ids, "mode": mode})
    assert response.status_code == 200, response.text
    job = response.json()
    for _ in range(200):
        job = client.get(f"/api/v1/lorebook-agent-jobs/{job['id']}").json()
        if job["status"] in {"review", "failed"}:
            break
        time.sleep(0.005)
    assert job["status"] == "review", job
    return job


def commit_lore(client, job, operation_id):
    response = client.post(f"/api/v1/lorebook-agent-jobs/{job['id']}/commit-batch", json={
        "operation_id": operation_id, "expected_revision": job["revision"],
        "expected_lorebook_revision": job.get("target_revision"),
        "draft_ids": [row["id"] for row in job["drafts"]],
        "shared_draft_ids": [row["id"] for row in job["drafts"]]})
    assert response.status_code == 200, response.text
    return response.json()["book"]


def world_with_manuscript(client, *, visibility="public"):
    world = client.post("/api/v1/worlds", json={"title": "Incremental World"}).json()
    response = client.post(f"/api/v1/worlds/{world['id']}/manuscript", json={
        "expected_revision": world["revision"], "body": RAW, "visibility": visibility})
    assert response.status_code == 200, response.text
    return response.json()


def test_incremental_preserves_uid_and_manual_edits_and_reviews_deleted_source(mrp_client):
    container, calls = install_fake_workers(mrp_client)
    world = world_with_manuscript(mrp_client)
    job = ready_lore(mrp_client, world["id"])
    book = commit_lore(mrp_client, job, "initial")
    uid = book["entries"][0]["uid"]
    world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    # Changed source gives an update with the original uid rather than a new entry.
    world = mrp_client.post(f"/api/v1/worlds/{world['id']}/manuscript", json={
        "expected_revision": world["revision"], "body": RAW + "\nA changed source condition.", "visibility": "public"}).json()
    update = ready_lore(mrp_client, world["id"], target_book_id=book["id"], mode="incremental")
    assert update["drafts"][0]["action"] == "replace"
    assert update["drafts"][0]["target_uid"] == uid
    book = commit_lore(mrp_client, update, "replace")
    assert len(book["entries"]) == 1 and book["entries"][0]["uid"] == uid
    manual = "The author manually refined the crystal charge limitation."
    response = mrp_client.patch(f"/api/v1/lorebooks/{book['id']}/entries/{uid}", json={
        "expected_revision": book["revision"], "content": manual})
    assert response.status_code == 200, response.text
    world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    world = mrp_client.post(f"/api/v1/worlds/{world['id']}/manuscript", json={
        "expected_revision": world["revision"], "body": RAW + "\nAnother changed condition.", "visibility": "public"}).json()
    update = ready_lore(mrp_client, world["id"], target_book_id=book["id"], mode="incremental")
    row = update["drafts"][0]
    assert row["action"] == "keep" and row["conflict_reason"]
    assert row["before_payload"]["content"] == row["original_content"] == manual
    book = commit_lore(mrp_client, update, "keep-manual")
    assert book["entries"][0]["content"] == manual
    unchanged = ready_lore(mrp_client, world["id"], target_book_id=book["id"], mode="incremental")
    # The kept hand edit intentionally retains its previous provenance: the changed source is still pending.
    assert unchanged["drafts"][0]["action"] == "keep"
    world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    deleted = mrp_client.request("DELETE", f"/api/v1/worlds/{world['id']}/archive/{world['manuscript_archive_id']}",
                                json={"expected_revision": world["revision"]})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["manuscript_archive_id"] is None
    removal = ready_lore(mrp_client, world["id"], target_book_id=book["id"], source_ids=[], mode="incremental")
    assert removal["drafts"][0]["action"] == "keep"
    assert removal["drafts"][0]["proposed_action"] == "disable"
    assert removal["drafts"][0]["target_uid"] == uid
    candidate = removal["drafts"][0]
    resolved = mrp_client.patch(f"/api/v1/lorebook-agent-jobs/{removal['id']}/drafts/{candidate['id']}", json={
        "expected_revision": candidate["revision"], "payload": candidate["payload"],
        "action": "disable", "resolve_conflict": True})
    assert resolved.status_code == 200, resolved.text
    removal = mrp_client.get(f"/api/v1/lorebook-agent-jobs/{removal['id']}").json()
    book = commit_lore(mrp_client, removal, "disable-deleted")
    assert book["entries"][0]["uid"] == uid and book["entries"][0]["enabled"] is False


def test_source_audience_change_cannot_use_legacy_override(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    world = world_with_manuscript(mrp_client)
    job = ready_lore(mrp_client, world["id"])
    source_id = world["manuscript_archive_id"]
    patched = mrp_client.patch(f"/api/v1/worlds/{world['id']}/archive/{source_id}", json={
        "expected_revision": world["revision"], "record": {**world["archive_records"][0], "visibility": "private"}})
    assert patched.status_code == 200, patched.text
    row = job["drafts"][0]
    denied = mrp_client.post(f"/api/v1/lorebook-agent-jobs/{job['id']}/drafts/{row['id']}/commit", json={
        "expected_revision": row["revision"], "accept_source_changes": True})
    assert denied.status_code == 409
    assert not container.lorebooks


def test_whole_book_simulation_detects_budget_and_recursive_false_positive():
    candidate = {"payload": {"keys": ["moon crystal"], "content": "Crystal charge follows the moon tide."},
                 "positive_examples": ["moon crystal"], "negative_examples": ["walk in the mountains"]}
    assert simulation(candidate)["valid"]
    budget = simulation(candidate, {"token_budget": 20, "entries": [{"uid": 40, "constant": True,
        "content": "Constant setting text.", "order": 999}]})
    assert not budget["valid"]
    assert "预算" in budget["positive"][0]["reason"]
    recursive = simulation(candidate, {"recursive_scanning": True, "entries": [{"uid": 2,
        "keys": ["mountains"], "content": "A moon crystal rests inside the cave."}]})
    assert recursive["negative"][0]["triggered"] is True and not recursive["valid"]


def test_recovery_conflict_preserves_external_book_edit(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    job = ready_bundle(mrp_client)
    original = container.world_registry.save_sync
    def fail_world(world):
        raise OSError("Synthetic loss before world write")
    container.world_registry.save_sync = fail_world
    with pytest.raises(OSError):
        mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/commit-bundle", json=commit_request(job))
    container.world_registry.save_sync = original
    book = next(iter(container.lorebooks.values())).model_copy(deep=True)
    book.name = "An external edit after interruption"
    book.revision += 1
    container.lorebook_registry.save_sync(book)
    assert len(container.asset_imports.recover_commits()) == 1
    assert container.lorebooks[book.id].name == book.name
    assert not container.worlds
    journal = json.loads(next((container.asset_imports.root / job["id"]).glob("bundle-*.json")).read_text(encoding="utf-8"))
    assert journal["status"] == "conflict"


def test_frozen_private_source_and_operation_identity(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    world = world_with_manuscript(mrp_client)
    created = mrp_client.post("/api/v1/asset-import-jobs", json={"source": RAW,
        "target_kind": "world", "world_id": world["id"], "source_visibility": "private"}).json()
    mrp_client.post(f"/api/v1/asset-import-jobs/{created['id']}/derive-world-runtime", json={"expected_bundle_revision": 1})
    for _ in range(200):
        job = mrp_client.get(f"/api/v1/asset-import-jobs/{created['id']}").json()
        if job["bundle"]["status"] == "review":
            break
        time.sleep(0.005)
    assert job["bundle"]["manuscript_proposal"]["runtime_scope"] == "author"
    assert job["bundle"]["core_proposal"]["runtime_scope"] == "author"
    request = commit_request(job)
    response = mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/commit-bundle", json=request)
    assert response.status_code == 200, response.text
    changed = {**request, "shared_proposal_ids": ["core"]}
    duplicate = mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/commit-bundle", json=changed)
    assert duplicate.status_code == 409


def test_character_organization_preserves_seed_runtime_aliases_and_exact_manuscript(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    raw = "  A synthetic character wears a blue coat.\nHas a difficult relationship with the sea.  \n"
    async def fake_character(prompt, job_id, attempt):
        assert "reference_snapshot" not in prompt  # No ambient cross-library collection.
        return json.dumps({"payload": {"name": "Marina", "description": "Has a difficult relationship with the sea.",
            "appearance": "Wears a blue coat.", "system_prompt": "Unrequested overwrite", "extensions": {"raw": raw}}})
    container.asset_imports._call = fake_character
    response = mrp_client.post("/api/v1/asset-import-jobs", json={"source": raw,
        "target_kind": "character", "selected_fields": ["appearance"], "character_aliases": ["Sailor"],
        "character_runtime": {"model": "", "base_url": "", "max_tokens": 0},
        "character_seed": {"name": "Seed", "description": raw, "system_prompt": "Preserved author instruction",
                           "alternate_greetings": ["An existing opening"], "extensions": {"safe": "seed"}}})
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["selected_fields"] == ["description", "appearance"]
    mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/generate")
    for _ in range(200):
        job = mrp_client.get(f"/api/v1/asset-import-jobs/{job['id']}").json()
        if job["status"] == "needs_review":
            break
        time.sleep(0.005)
    row = job["drafts"][0]
    response = mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/drafts/{row['id']}/commit")
    assert response.status_code == 200, response.text
    character = container.characters[response.json()["saved_asset_id"]]
    assert character.authoring_source.text == raw
    assert character.card.description == "Has a difficult relationship with the sea."
    assert character.card.system_prompt == "Preserved author instruction"
    assert character.card.alternate_greetings == ["An existing opening"]
    assert character.card.extensions == {"safe": "seed"}
    assert character.aliases == ["Sailor"]
    assert character.llm.inherit_model and character.llm.inherit_base_url
    assert character.llm.sampling["max_tokens"] == 0


def test_initial_failed_world_draft_is_retryable_and_history_is_metadata_only(mrp_client):
    container, calls = install_fake_workers(mrp_client)
    healthy = container.asset_imports._call
    async def invalid_initial(prompt, job_id, attempt):
        return json.dumps({"payload": {"title": "Missing required world name?", "unexpected": True}})
    container.asset_imports._call = invalid_initial
    created = mrp_client.post("/api/v1/asset-import-jobs", json={"source": RAW,
        "target_kind": "world", "intent": "organize"}).json()
    mrp_client.post(f"/api/v1/asset-import-jobs/{created['id']}/derive-world-runtime", json={
        "expected_bundle_revision": 1, "source_ids": ["manuscript"]})
    for _ in range(200):
        job = mrp_client.get(f"/api/v1/asset-import-jobs/{created['id']}").json()
        if job["bundle"]["status"] == "failed":
            break
        time.sleep(0.005)
    assert job["drafts"][0]["status"] == "failed"
    failed_id = job["drafts"][0]["id"]
    exact_id = job["drafts"][1]["id"]
    container.asset_imports._call = healthy
    retry = mrp_client.post(f"/api/v1/asset-import-jobs/{created['id']}/derive-world-runtime", json={
        "expected_bundle_revision": job["bundle_revision"]})
    assert retry.status_code == 200, retry.text
    for _ in range(200):
        job = mrp_client.get(f"/api/v1/asset-import-jobs/{created['id']}").json()
        if job["bundle"]["status"] in {"review", "failed"}:
            break
        time.sleep(0.005)
    assert job["bundle"]["status"] == "review", job
    assert job["drafts"][0]["id"] == failed_id and job["drafts"][1]["id"] == exact_id
    summary = next(row for row in mrp_client.get("/api/v1/asset-import-jobs").json() if row["id"] == job["id"])
    assert {"world_id", "target_kind", "intent"}.issubset(summary)
    assert "source" not in summary and "drafts" not in summary and "bundle" not in summary


def test_sources_and_cas_base_are_frozen_before_initial_model_wait(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    world = world_with_manuscript(mrp_client)
    world = mrp_client.post(f"/api/v1/worlds/{world['id']}/archive", json={
        "expected_revision": world["revision"], "record": {"kind": "background", "title": "Author notes",
            "body": "Version A selected author facts.", "visibility": "private", "kind_data": {"section": "other"}}}).json()
    side = world["archive_records"][-1]
    started, released = threading.Event(), threading.Event()
    healthy = container.asset_imports._call
    async def waiting(prompt, job_id, attempt):
        if attempt == 1:
            started.set()
            while not released.is_set():
                await asyncio.sleep(0.005)
        if '仅返回 {"core_brief":string}' in prompt:
            assert "Version A selected author facts." in prompt and "Version B" not in prompt
        return await healthy(prompt, job_id, attempt)
    container.asset_imports._call = waiting
    created = mrp_client.post("/api/v1/asset-import-jobs", json={"source": RAW,
        "target_kind": "world", "world_id": world["id"]}).json()
    response = mrp_client.post(f"/api/v1/asset-import-jobs/{created['id']}/derive-world-runtime", json={
        "expected_bundle_revision": 1})
    assert response.status_code == 200, response.text
    assert started.wait(2)
    try:
        changed = mrp_client.patch(f"/api/v1/worlds/{world['id']}/archive/{side['id']}", json={
            "expected_revision": world["revision"], "record": {**side, "body": "Version B external facts."}})
        assert changed.status_code == 200, changed.text
    finally:
        released.set()
    for _ in range(200):
        job = mrp_client.get(f"/api/v1/asset-import-jobs/{created['id']}").json()
        if job["bundle"]["status"] in {"review", "failed"}:
            break
        time.sleep(0.005)
    assert job["bundle"]["status"] == "review", job
    frozen = next(row for row in job["bundle"]["source_snapshot"] if row["id"] == side["id"])
    assert "Version A" in frozen["content"] and job["bundle"]["base_world_revision"] == world["revision"]
    denied = mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/commit-bundle", json=commit_request(job))
    assert denied.status_code == 409
    assert "Version B" in container.worlds[world["id"]].archive_records[-1].body
    assert not container.lorebooks


def test_same_entity_multiple_facts_match_distinct_uids_and_keep_hand_edit(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    world = world_with_manuscript(mrp_client)
    facts = ["Moon tide crystals collect tidal energy only at a full moon high tide.",
             "Moon tide crystals lose stored charge when exposed to salt water."]
    world = mrp_client.post(f"/api/v1/worlds/{world['id']}/manuscript", json={
        "expected_revision": world["revision"], "body": "\n".join(facts), "visibility": "public"}).json()
    async def per_fact(**kwargs):
        snapshot = json.loads(Path(kwargs["task_file"]).read_text(encoding="utf-8"))
        source = snapshot["sources"][0]
        return json.dumps({"entries": [{"payload": {"keys": ["Moon tide crystal"], "content": fact},
            "source_refs": [{"source_id": source["id"], "quote": fact}],
            "positive_examples": ["Moon tide crystal"], "negative_examples": ["A mountain walk."]} for fact in facts]})
    container.lorebook_generation.worker.run = per_fact
    initial = ready_lore(mrp_client, world["id"])
    book = commit_lore(mrp_client, initial, "two-initial")
    assert [row["uid"] for row in book["entries"]] == [1, 2]
    hand = facts[0] + " The author explicitly refined its energy limit."
    mrp_client.patch(f"/api/v1/lorebooks/{book['id']}/entries/1", json={"expected_revision": book["revision"], "content": hand})
    facts = [facts[0].replace("only", "only briefly"), facts[1].replace("salt water", "concentrated salt water")]
    world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    world = mrp_client.post(f"/api/v1/worlds/{world['id']}/manuscript", json={
        "expected_revision": world["revision"], "body": "\n".join(facts), "visibility": "public"}).json()
    update = ready_lore(mrp_client, world["id"], target_book_id=book["id"], mode="incremental")
    assert [row.get("target_uid") for row in update["drafts"]] == [1, 2], update["drafts"]
    assert [row["action"] for row in update["drafts"]] == ["keep", "replace"]
    book = commit_lore(mrp_client, update, "two-update")
    assert len(book["entries"]) == 2
    assert book["entries"][0]["content"] == hand and book["entries"][1]["content"] == facts[1]


def test_ambiguous_incremental_target_requires_explicit_choice(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    world = world_with_manuscript(mrp_client)
    refs = [{"source_id": world["manuscript_archive_id"], "quote": RAW[:60]}]
    response = mrp_client.post("/api/v1/lorebooks", json={"book": {"id": "book-ambiguous", "name": "Old book",
        "entries": [{"uid": uid, "keys": ["Moon tide crystal"], "content": RAW, "order": 100 + uid,
                     "extensions": {"mrp.archive_sources": refs}} for uid in [1, 2]]}})
    assert response.status_code == 200, response.text
    linked = mrp_client.post(f"/api/v1/worlds/{world['id']}/lorebooks", json={
        "expected_revision": world["revision"], "book_id": "book-ambiguous"})
    assert linked.status_code == 200, linked.text
    job = ready_lore(mrp_client, world["id"], target_book_id="book-ambiguous", mode="incremental")
    row = job["drafts"][0]
    assert row["action"] == "keep" and row.get("target_uid") is None
    assert {option["uid"] for option in row["candidate_targets"]} == {1, 2}
    missing = mrp_client.patch(f"/api/v1/lorebook-agent-jobs/{job['id']}/drafts/{row['id']}", json={
        "expected_revision": row["revision"], "payload": row["payload"], "action": "replace", "resolve_conflict": True})
    assert missing.status_code == 400
    resolved = mrp_client.patch(f"/api/v1/lorebook-agent-jobs/{job['id']}/drafts/{row['id']}", json={
        "expected_revision": row["revision"], "payload": row["payload"], "action": "replace",
        "target_uid": 2, "resolve_conflict": True})
    assert resolved.status_code == 200, resolved.text
    job = mrp_client.get(f"/api/v1/lorebook-agent-jobs/{job['id']}").json()
    book = commit_lore(mrp_client, job, "explicit-target")
    assert len(book["entries"]) == 2
    assert book["entries"][0]["uid"] == 1 and "mrp.generated" not in book["entries"][0]["extensions"]
    assert book["entries"][1]["uid"] == 2 and book["entries"][1]["extensions"]["mrp.generated"]["job_id"] == job["id"]


def test_character_delete_waits_for_organized_save_without_resurrection(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    character_id = _import_char(mrp_client, "Before", "Original description")
    character = container.characters[character_id]
    job = container.asset_imports.create("Synthetic retained author prose.", target_kind="character", world_id=None,
        target_asset_id=character_id, target_revision=character.revision, selected_fields=["description"])
    row = {"id": "draft-synthetic", "kind": "character", "status": "needs_review", "revision": 1,
        "payload": {"name": "After", "description": "Organized synthetic description"}, "saved_asset_id": None,
        "target_asset_id": character_id, "target_revision": character.revision,
        "selected_fields": ["description"], "world_id": None}
    job.update(status="needs_review", drafts=[row])
    container.asset_imports._save(job)
    original_save = container.save_character
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def held_save(character):
            started.set()
            await release.wait()
            await original_save(character)
        container.save_character = held_save
        save_task = asyncio.create_task(container.asset_imports.commit(job["id"], row["id"]))
        await started.wait()
        from mrp.server.routers.characters import delete_character
        delete_task = asyncio.create_task(delete_character(character_id, container))
        await asyncio.sleep(0)
        assert not delete_task.done()
        release.set()
        await save_task
        result = await delete_task
        assert result["ok"] and character_id not in container.characters
    try:
        asyncio.run(scenario())
    finally:
        container.save_character = original_save


def test_organization_cannot_append_model_supplement_to_author_manuscript(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    async def unexpected_supplement(prompt, job_id, attempt):
        return json.dumps({"payload": {"title": "Organized", "core_brief": "Ocean tides matter."},
                           "addition_text": "An unauthorized newly invented empire.", "added_facts": ["Invented empire"]})
    container.asset_imports._call = unexpected_supplement
    job = ready_bundle(mrp_client)
    assert job["bundle"]["manuscript_proposal"]["body"] == RAW
    draft = next(row for row in job["drafts"] if row["kind"] == "world")
    assert draft["addition_text"] == "" and draft["added_facts"] == []


def test_deselected_main_manuscript_is_not_sent_to_initial_core_preparation(mrp_client):
    container, calls = install_fake_workers(mrp_client)
    world = world_with_manuscript(mrp_client, visibility="private")
    side_fact = "Glass birds remain inside the lighthouse and guide sailors only after sunset."
    world = mrp_client.post(f"/api/v1/worlds/{world['id']}/archive", json={
        "expected_revision": world["revision"], "record": {"kind": "background", "title": "Glass birds",
            "body": side_fact, "visibility": "public", "kind_data": {"section": "other"}}}).json()
    side = world["archive_records"][-1]
    healthy = container.asset_imports._call
    async def selected_only(prompt, job_id, attempt):
        assert RAW not in prompt
        assert side_fact in prompt
        return await healthy(prompt, job_id, attempt)
    container.asset_imports._call = selected_only
    async def bird_entry(**kwargs):
        snapshot = json.loads(Path(kwargs["task_file"]).read_text(encoding="utf-8"))
        assert len(snapshot["sources"]) == 1 and snapshot["sources"][0]["id"] == side["id"]
        assert RAW not in snapshot["sources"][0]["content"]
        return json.dumps({"entries": [{"payload": {"keys": ["Glass bird"], "content": side_fact},
            "source_refs": [{"source_id": side["id"], "quote": side_fact}],
            "positive_examples": ["Glass bird"], "negative_examples": ["A mountain walk."]}]})
    container.lorebook_generation.worker.run = bird_entry
    created = mrp_client.post("/api/v1/asset-import-jobs", json={"source": RAW,
        "target_kind": "world", "world_id": world["id"]}).json()
    response = mrp_client.post(f"/api/v1/asset-import-jobs/{created['id']}/derive-world-runtime", json={
        "expected_bundle_revision": 1, "source_ids": [side["id"]]})
    assert response.status_code == 200, response.text
    for _ in range(200):
        job = mrp_client.get(f"/api/v1/asset-import-jobs/{created['id']}").json()
        if job["bundle"]["status"] in {"review", "failed"}:
            break
        time.sleep(0.005)
    assert job["bundle"]["status"] == "review", job
    assert calls["import"] == 1
    assert job["bundle"]["core_proposal"]["runtime_scope"] == "shared"


def test_bundle_edit_keys_and_examples_runs_whole_target_book_rules(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    world = world_with_manuscript(mrp_client)
    book = mrp_client.post("/api/v1/lorebooks", json={"book": {"id": "book-rule-review", "name": "Rule review",
        "recursive_scanning": True, "entries": [{"uid": 7, "keys": ["mountains"],
            "content": "A glass bird lives in the lighthouse beside those mountains."}]}}).json()
    world = mrp_client.post(f"/api/v1/worlds/{world['id']}/lorebooks", json={
        "expected_revision": world["revision"], "book_id": book["id"]}).json()
    job = ready_bundle(mrp_client, world_id=world["id"], target_book_id=book["id"])
    row = job["bundle"]["entry_proposals"][0]
    edited_payload = {**row["payload"], "keys": ["glass bird"]}
    response = mrp_client.patch(f"/api/v1/asset-import-jobs/{job['id']}/runtime-draft", json={
        "expected_bundle_revision": job["bundle"]["revision"], "proposal_id": row["id"],
        "payload": edited_payload, "positive_examples": ["A glass bird"], "negative_examples": ["mountains"]})
    assert response.status_code == 200, response.text
    job = response.json()
    edited = job["bundle"]["entry_proposals"][0]
    assert edited["payload"]["keys"] == ["glass bird"]
    assert edited["source_refs"] == row["source_refs"]
    assert edited["action"] == row["action"]
    assert edited["simulation"]["negative"][0]["triggered"] and not edited["simulation"]["valid"]
    assert edited["validation_errors"]
    response = mrp_client.patch(f"/api/v1/asset-import-jobs/{job['id']}/runtime-draft", json={
        "expected_bundle_revision": job["bundle"]["revision"], "proposal_id": row["id"],
        "negative_examples": ["An empty sea."]})
    assert response.status_code == 200, response.text
    revised = response.json()["bundle"]["entry_proposals"][0]
    assert revised["positive_examples"] == ["A glass bird"]
    assert revised["negative_examples"] == ["An empty sea."] and revised["simulation"]["valid"]
    assert not revised["validation_errors"]


def test_disabled_candidate_materializes_without_recursive_delivery_and_reenables_strictly(mrp_client):
    container, _ = install_fake_workers(mrp_client)
    world = world_with_manuscript(mrp_client)
    book = mrp_client.post("/api/v1/lorebooks", json={"book": {"id": "book-disabled-review", "name": "Disabled review",
        "recursive_scanning": True, "entries": [{"uid": 7, "keys": ["mountains"],
            "content": "A moon tide crystal rests inside those mountains."}]}}).json()
    world = mrp_client.post(f"/api/v1/worlds/{world['id']}/lorebooks", json={
        "expected_revision": world["revision"], "book_id": book["id"]}).json()
    job = ready_bundle(mrp_client, world_id=world["id"], target_book_id=book["id"])
    row = job["bundle"]["entry_proposals"][0]
    response = mrp_client.patch(f"/api/v1/asset-import-jobs/{job['id']}/runtime-draft", json={
        "expected_bundle_revision": job["bundle"]["revision"], "proposal_id": row["id"],
        "payload": {**row["payload"], "enabled": False},
        "positive_examples": ["An incorrect trigger"], "negative_examples": ["mountains"]})
    assert response.status_code == 200, response.text
    job = response.json()
    disabled = job["bundle"]["entry_proposals"][0]
    assert disabled["simulation"]["valid"] and disabled["simulation"]["mode"] == "disabled"
    assert "停用" in disabled["simulation"]["note"]
    assert not any(sample["triggered"] for sample in [*disabled["simulation"]["positive"], *disabled["simulation"]["negative"]])
    selected = ["manuscript", "core", row["id"]]
    saved = mrp_client.post(f"/api/v1/asset-import-jobs/{job['id']}/commit-bundle", json=commit_request(
        job, accepted=selected, shared=selected, operation_id="disabled-adoption"))
    assert saved.status_code == 200, saved.text
    book = saved.json()["book"]
    adopted = next(entry for entry in book["entries"] if entry["uid"] != 7)
    assert adopted["enabled"] is False
    message = Message(session_id="disabled-test", seq=0, turn=1, actor="player", content="mountains")
    injections = LorebookEngine.scan(container.lorebooks[book["id"]], [message])
    assert f"{book['id']}:{adopted['uid']}" not in {entry.entry_id for entry in injections}
    # A new increment may explicitly reenable the same uid, but active examples must pass again.
    reopened = ready_bundle(mrp_client, world_id=world["id"], target_book_id=book["id"],
                            source=RAW + "\nAn unrelated updated source condition.")
    row = reopened["bundle"]["entry_proposals"][0]
    assert row["target_uid"] == adopted["uid"]
    response = mrp_client.patch(f"/api/v1/asset-import-jobs/{reopened['id']}/runtime-draft", json={
        "expected_bundle_revision": reopened["bundle"]["revision"], "proposal_id": row["id"],
        "payload": {**row["payload"], "enabled": True},
        "positive_examples": ["An incorrect trigger"], "negative_examples": ["An empty sea."]})
    assert response.status_code == 200, response.text
    reopened = response.json()
    active = reopened["bundle"]["entry_proposals"][0]
    assert active["simulation"]["mode"] == "active" and not active["simulation"]["valid"]
    response = mrp_client.patch(f"/api/v1/asset-import-jobs/{reopened['id']}/runtime-draft", json={
        "expected_bundle_revision": reopened["bundle"]["revision"], "proposal_id": row["id"],
        "positive_examples": ["Moon tide crystal"]})
    assert response.status_code == 200, response.text
    reopened = response.json()
    assert reopened["bundle"]["entry_proposals"][0]["simulation"]["valid"]
    selected = ["manuscript", "core", row["id"]]
    saved = mrp_client.post(f"/api/v1/asset-import-jobs/{reopened['id']}/commit-bundle", json=commit_request(
        reopened, accepted=selected, shared=selected, operation_id="reenabled-adoption"))
    assert saved.status_code == 200, saved.text
    assert next(entry for entry in saved.json()["book"]["entries"] if entry["uid"] == adopted["uid"])["enabled"]
