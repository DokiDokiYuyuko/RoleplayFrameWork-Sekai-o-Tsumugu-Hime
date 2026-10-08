"""World organizer acceptance checks using synthetic sources and injected models."""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path

import pytest


ENDPOINT = "/api/v1/world-organize-jobs"
SOURCE = (
    "The Glass Canal opens only at low tide. Its gates close after the third bell. "
    "Silver moths live beside the canal; they glow after sunset, need salt water, "
    "and lose their glow in cold weather."
)


def archive_payload(title="Glass Canal", *, kind="background", body=SOURCE, visibility="public"):
    return {
        "kind": kind, "title": title, "subtype": "", "aliases": [], "tags": [],
        "summary": "A complete synthetic setting fact.", "body": body,
        "visibility": visibility,
        "kind_data": {"section": "geography"} if kind == "background" else {
            "classification": "species", "appearance": "Silver wings",
            "habitat": "Glass Canal", "culture": "", "abilities": "Glow after sunset",
            "limitations": "Need salt water; cold weather removes the glow",
        },
    }


def archive_candidate(title="Glass Canal", *, kind="background", source_id="pasted-source", quote=SOURCE, body=SOURCE):
    payload = archive_payload(title, kind=kind, body=body)
    payload.pop("kind")
    return {
        "kind": kind, "payload": payload,
        "source_refs": [{"source_id": source_id, "quote": quote}],
    }


def make_world(client):
    response = client.post("/api/v1/worlds", json={
        "title": "Synthetic Canal World", "description": "A test world.",
        "core_brief": "Core must remain intact.", "runtime_policy": "raw",
        "manuscript_body": "Exact manuscript with spacing.  \nFinal line.",
        "manuscript_visibility": "private",
    })
    assert response.status_code == 200, response.text
    world = response.json()
    for payload in [
        archive_payload("Existing Road", body="The existing road has three toll gates."),
        archive_payload("Unselected Secret", body="A private tunnel crosses the river.", visibility="private"),
    ]:
        response = client.post(f"/api/v1/worlds/{world['id']}/archive", json={
            "expected_revision": world["revision"], "record": payload,
        })
        assert response.status_code == 200, response.text
        world = response.json()
    return world


def install_archive_worker(client, output=None):
    container = client.app.state.container
    calls = []

    async def call(prompt, job_id, attempt):
        calls.append({"prompt": prompt, "job_id": job_id, "attempt": attempt})
        value = output(prompt, job_id, attempt) if callable(output) else output
        return value if value is not None else json.dumps({"archives": [
            archive_candidate(),
            archive_candidate("Silver Moth", kind="biology"),
        ]})

    container.world_organize._call = call
    return container, calls


def start_job(client, world, **changes):
    response = client.post(ENDPOINT, json={
        "world_id": world["id"], "category": "archives", "source_text": SOURCE,
        "instruction": "Retain opening hours, conditions and limitations.",
        "reference_source_ids": [], **changes,
    })
    assert response.status_code == 200, response.text
    return response.json()


def wait_job(client, job_id, states=("review", "failed", "interrupted", "cancelled")):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f"{ENDPOINT}/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in states:
            return job
        time.sleep(0.005)
    pytest.fail(f"Synthetic organizer job did not settle: {job}")


def ready_job(client, world, **changes):
    job = wait_job(client, start_job(client, world, **changes)["id"])
    assert job["status"] == "review", job.get("errors", job)
    return job


def commit_body(job, *, operation_id="synthetic-adoption", draft_ids=None, approved=None):
    return {
        "operation_id": operation_id, "expected_revision": job["revision"],
        "draft_ids": draft_ids if draft_ids is not None else [row["id"] for row in job["drafts"]],
        "approved_replace_draft_ids": approved or [],
        "expected_lorebook_revision": job.get("target_revision"),
        "accept_source_changes": False,
    }


def protected_world(world):
    manuscript = next(row for row in world["archive_records"] if row["id"] == world["manuscript_archive_id"])
    return {key: world[key] for key in ("core_brief", "author_core_brief", "manuscript_archive_id", "runtime_policy")} | {
        "manuscript": copy.deepcopy(manuscript),
    }


def test_archive_generation_preserves_full_source_and_is_draft_only(mrp_client):
    world = make_world(mrp_client)
    container, calls = install_archive_worker(mrp_client)
    before = copy.deepcopy(world)
    job = ready_job(mrp_client, world)
    assert {row["kind"] for row in job["drafts"]} == {"background", "biology"}
    assert all(row["status"] == "review" and row["action"] == "add" for row in job["drafts"])
    assert all(row["payload"]["body"] == SOURCE for row in job["drafts"])
    assert all(row["source_refs"][0]["source_id"] == "pasted-source" for row in job["drafts"])
    assert job["source_text"] == SOURCE
    assert SOURCE in calls[0]["prompt"]
    assert "Core must remain intact." not in calls[0]["prompt"]
    assert "A private tunnel crosses the river." not in calls[0]["prompt"]
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == before
    assert container.lorebooks == {}
    original = mrp_client.get(f"{ENDPOINT}/{job['id']}/source")
    assert original.status_code == 200, original.text
    assert SOURCE in original.text


@pytest.mark.parametrize("bad_output", ["{broken", '{"archives": []}', '{"archives": [{"kind":"biology"'])
def test_malformed_empty_or_truncated_result_retries_once_and_preserves_input(mrp_client, bad_output):
    world = make_world(mrp_client)
    _, calls = install_archive_worker(mrp_client, bad_output)
    job = wait_job(mrp_client, start_job(mrp_client, world)["id"])
    assert job["status"] == "failed", job
    assert len(calls) == 2
    assert job["source_text"] == SOURCE
    assert job["instruction"] == "Retain opening hours, conditions and limitations."
    assert job["errors"]
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world


def test_single_bad_result_recovers_without_shortening_complete_facts(mrp_client):
    world = make_world(mrp_client)
    invocations = []

    def output(prompt, job_id, attempt):
        invocations.append(attempt)
        return "{}" if len(invocations) == 1 else json.dumps({"archives": [archive_candidate()]})

    _, calls = install_archive_worker(mrp_client, output)
    job = ready_job(mrp_client, world)
    assert len(calls) == 2 and job["drafts"][0]["payload"]["body"] == SOURCE


def test_only_explicit_reference_is_in_frozen_model_scope(mrp_client):
    world = make_world(mrp_client)
    selected = world["archive_records"][1]
    _, calls = install_archive_worker(mrp_client)
    job = ready_job(mrp_client, world, reference_source_ids=[selected["id"]])
    prompt = calls[0]["prompt"]
    assert selected["body"] in prompt
    assert world["archive_records"][2]["body"] not in prompt
    assert world["archive_records"][0]["body"] not in prompt
    assert job["reference_source_ids"] == [selected["id"]]
    other_world = make_world(mrp_client)
    denied = mrp_client.post(ENDPOINT, json={
        "world_id": world["id"], "category": "archives", "source_text": SOURCE,
        "reference_source_ids": [other_world["archive_records"][1]["id"]],
    })
    assert denied.status_code in {400, 422}, denied.text
    assert len(calls) == 1


def test_archive_commit_keeps_unselected_records_and_protected_world_fields(mrp_client):
    world = make_world(mrp_client)
    install_archive_worker(mrp_client)
    job = ready_job(mrp_client, world)
    body = commit_body(job)
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=body)
    assert response.status_code == 200, response.text
    returned_job = response.json()
    assert returned_job["id"] == job["id"]
    assert returned_job["source_text"] == SOURCE
    assert returned_job["status"] == "committed"
    assert all(row["status"] == "committed" for row in returned_job["drafts"])
    saved = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    assert protected_world(saved) == protected_world(world)
    assert saved["archive_records"][:len(world["archive_records"])] == world["archive_records"]
    assert len(saved["archive_records"]) == len(world["archive_records"]) + 2
    repeated = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=body)
    assert repeated.status_code == 200, repeated.text
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == saved


def test_public_source_can_be_marked_private_in_editor_and_adopted_privately(mrp_client):
    world = make_world(mrp_client)
    install_archive_worker(mrp_client, json.dumps({"archives": [archive_candidate()]}))
    job = ready_job(mrp_client, world)
    draft = job["drafts"][0]
    assert draft["payload"]["visibility"] == "public"
    response = mrp_client.patch(f"{ENDPOINT}/{job['id']}/drafts/{draft['id']}", json={
        "expected_revision": draft["revision"], "payload": {**draft["payload"], "visibility": "private"},
    })
    assert response.status_code == 200, response.text
    assert response.json()["payload"]["visibility"] == "private"
    job = mrp_client.get(f"{ENDPOINT}/{job['id']}").json()
    adopted = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job))
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["id"] == job["id"]
    saved = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    assert saved["archive_records"][-1]["visibility"] == "private"


def test_selected_archive_replacement_preserves_imported_metadata(mrp_client):
    world = make_world(mrp_client)
    imported = archive_payload("Imported Road", body="An imported road includes custom metadata.") | {
        "schema_version": 7, "legacy_flags": {"reviewed": True}, "external_source": "synthetic-import",
    }
    created = mrp_client.post(f"/api/v1/worlds/{world['id']}/archive", json={
        "expected_revision": world["revision"], "record": imported,
    })
    assert created.status_code == 200, created.text
    world = created.json()
    target = world["archive_records"][-1]
    install_archive_worker(mrp_client, json.dumps({"archives": [archive_candidate()]}))
    job = ready_job(mrp_client, world, target_archive_id=target["id"])
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job))
    assert response.status_code == 200, response.text
    saved = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()["archive_records"][-1]
    assert saved["id"] == target["id"] and saved["created_at"] == target["created_at"]
    assert saved["schema_version"] == 7
    assert saved["legacy_flags"] == {"reviewed": True} and saved["external_source"] == "synthetic-import"


def test_runtime_contract_extension_is_advertised_and_enforced_before_drafting(mrp_client, monkeypatch):
    from mrp.asset_import.service import ArchiveInput, TARGETS

    class ExpandedBiologyInput(ArchiveInput):
        seasonal_rule: str

    monkeypatch.setitem(TARGETS, "biology", ExpandedBiologyInput)
    world = make_world(mrp_client)
    invocations = []

    def output(prompt, job_id, attempt):
        invocations.append(prompt)
        candidate = archive_candidate("Silver Moth", kind="biology")
        if len(invocations) == 2:
            candidate["payload"]["seasonal_rule"] = "Cold weather removes the glow."
        return json.dumps({"archives": [candidate]})

    install_archive_worker(mrp_client, output)
    job = ready_job(mrp_client, world)
    assert len(invocations) == 2
    assert '"seasonal_rule"' in invocations[0]
    assert job["drafts"][0]["payload"]["seasonal_rule"] == "Cold weather removes the glow."
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world


@pytest.mark.parametrize("auxiliary_model,expected_override", [
    ("synthetic/organizer", None), ("synthetic/main", 65536),
])
@pytest.mark.parametrize("finish_reason", ["stop", "length", "max_tokens", "max-tokens", "connection_closed", "cancelled", "content_filter", "content-filter", "safety", "blocked", "error", "tool_calls", "function_call", "LENGTH"])
def test_archive_gateway_uses_frozen_auxiliary_model_settings_and_complete_response(mrp_client, monkeypatch, auxiliary_model, expected_override, finish_reason):
    from mrp.orchestrator.model_capacity import ModelCapacity
    from mrp.settings import GenerationSettings

    world = make_world(mrp_client)
    container = mrp_client.app.state.container
    container._fake_mode = False
    settings = container.settings
    settings.model = "synthetic/main"
    settings.auxiliary_model = auxiliary_model
    settings.model_provider = "main-supplier"
    settings.auxiliary_provider = "aux-supplier"
    settings.provider_allow_fallbacks = False
    settings.thinking = "off"
    settings.context_limit_override = 65536
    settings.generation = GenerationSettings(temperature=0.3, top_p=0.7, max_output_tokens=100)
    settings.api_key = "synthetic-test-key"
    capacity_calls, gateway_calls = [], []

    async def capacity(routed, model, **kwargs):
        capacity_calls.append((routed, model, kwargs))
        return ModelCapacity(65536, 60000, 5536, "synthetic-catalog")

    def chat(messages, config, on_delta, **kwargs):
        gateway_calls.append((messages, config, kwargs))
        response = json.dumps({"archives": [archive_candidate()]})
        on_delta(response)
        return response, {"input_tokens": 4, "output_tokens": 6, "finish_reason": finish_reason}

    monkeypatch.setattr("mrp.world_organize.service.resolve_model_capacity", capacity)
    monkeypatch.setattr("mrp.world_organize.service.chat_stream_with_usage", chat)
    job = wait_job(mrp_client, start_job(mrp_client, world)["id"])
    if finish_reason == "stop":
        assert job["status"] == "review", job.get("errors")
        assert len(gateway_calls) == 1 and len(job["drafts"]) == 1
    else:
        assert job["status"] == "failed", job
        assert len(gateway_calls) == 2
        assert job["drafts"] == [] and job["errors"]
        assert job["source_text"] == SOURCE
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world
    assert container.world_organize._streams == {}
    assert job["model"] == auxiliary_model and job["provider"] == "aux-supplier"
    routed, model, kwargs = capacity_calls[0]
    assert routed.model == model == auxiliary_model
    assert routed.model_provider == "aux-supplier" and routed.context_limit_override == expected_override
    assert kwargs["reply_max_tokens"] == 0
    messages, config, kwargs = gateway_calls[0]
    assert config.model == auxiliary_model and config.provider == "aux-supplier"
    assert config.sampling == {"temperature": 0.3, "top_p": 0.7}
    assert config.max_tokens == kwargs["max_tokens"] == 0
    assert kwargs["require_complete"] is True and kwargs["no_thinking"] is True
    assert kwargs["stream_control"] is not None
    assert not config.provider_allow_fallbacks
    assert SOURCE in messages[-1]["content"]
    assert "synthetic-test-key" not in json.dumps(job)


def test_private_cited_reference_inherits_visibility_through_draft_edit_and_commit(mrp_client):
    world = make_world(mrp_client)
    private = world["archive_records"][2]
    output = json.dumps({"archives": [archive_candidate(
        "Canal Tunnel", source_id=private["id"], quote=private["body"], body="The canal tunnel crosses the river.",
    )]})
    install_archive_worker(mrp_client, output)
    job = ready_job(mrp_client, world, reference_source_ids=[private["id"]])
    draft = job["drafts"][0]
    assert draft["payload"]["visibility"] == "private"
    edited = mrp_client.patch(f"{ENDPOINT}/{job['id']}/drafts/{draft['id']}", json={
        "expected_revision": draft["revision"], "payload": {**draft["payload"], "visibility": "public"},
    })
    assert edited.status_code in {200, 400}, edited.text
    if edited.status_code == 200:
        job = mrp_client.get(f"{ENDPOINT}/{job['id']}").json()
        assert job["drafts"][0]["payload"]["visibility"] == "private"
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job))
    assert response.status_code == 200, response.text
    saved = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    assert saved["archive_records"][-1]["visibility"] == "private"
    assert protected_world(saved) == protected_world(world)


def test_new_source_replaces_only_the_explicit_archive(mrp_client):
    world = make_world(mrp_client)
    target = world["archive_records"][1]
    new_source = "The new road has two toll gates. Its western gate closes during storms."
    install_archive_worker(mrp_client, json.dumps({"archives": [archive_candidate(
        "Rebuilt Road", quote=new_source, body=new_source,
    )]}))
    job = ready_job(mrp_client, world, source_text=new_source, target_archive_id=target["id"])
    assert len(job["drafts"]) == 1
    draft = job["drafts"][0]
    assert draft["action"] == "replace" and draft["target_id"] == target["id"]
    assert draft["before_payload"]["body"] == target["body"]
    assert draft["payload"]["body"] == new_source
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job))
    assert response.status_code == 200, response.text
    saved = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    assert len(saved["archive_records"]) == len(world["archive_records"])
    replacement = next(row for row in saved["archive_records"] if row["id"] == target["id"])
    assert replacement["body"] == new_source and replacement["revision"] == target["revision"] + 1
    assert [row for row in saved["archive_records"] if row["id"] != target["id"]] == [
        row for row in world["archive_records"] if row["id"] != target["id"]
    ]
    assert protected_world(saved) == protected_world(world)


def test_replacement_conflict_is_atomic_and_keeps_manual_edit(mrp_client):
    world = make_world(mrp_client)
    target = world["archive_records"][1]
    install_archive_worker(mrp_client, json.dumps({"archives": [archive_candidate()]}))
    job = ready_job(mrp_client, world, target_archive_id=target["id"])
    changed = mrp_client.patch(f"/api/v1/worlds/{world['id']}/archive/{target['id']}", json={
        "expected_revision": world["revision"],
        "record": archive_payload("Manual Road", body="A manual edit must not be replaced by a stale draft."),
    })
    assert changed.status_code == 200, changed.text
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job))
    assert response.status_code == 409, response.text
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == changed.json()
    current = mrp_client.get(f"{ENDPOINT}/{job['id']}").json()
    assert current["drafts"][0]["status"] == "review"


def test_full_long_source_and_more_than_twenty_archive_candidates_are_retained(mrp_client):
    world = make_world(mrp_client)
    source = "START: The canal opens at low tide.\n" + "Operational conditions and limitations.\n" * 900
    source += "TAIL: The last bridge closes only when the silver bell rings twice."
    output = json.dumps({"archives": [archive_candidate(
        f"Distinct Canal Topic {index}", quote=source, body=f"Topic {index}: " + source,
    ) for index in range(37)]})
    _, calls = install_archive_worker(mrp_client, output)
    job = ready_job(mrp_client, world, source_text=source)
    assert len(calls) == 1
    assert json.dumps(source, ensure_ascii=False)[1:-1] in calls[0]["prompt"]
    assert job["source_text"] == source
    assert len(job["drafts"]) == 37
    assert job["drafts"][-1]["payload"]["body"].endswith(source[-65:])
    assert all(row["payload"]["body"].endswith(source) for row in job["drafts"])


def test_background_generation_returns_before_model_completion_and_survives_view_reads(mrp_client):
    world = make_world(mrp_client)
    started, release = threading.Event(), threading.Event()
    container = mrp_client.app.state.container

    async def slow_model(prompt, job_id, attempt):
        import asyncio
        started.set()
        await asyncio.to_thread(release.wait, 4)
        return json.dumps({"archives": [archive_candidate()]})

    container.world_organize._call = slow_model
    try:
        created = start_job(mrp_client, world)
        assert created["status"] in {"queued", "running"}
        assert started.wait(2)
        assert mrp_client.get("/api/v1/worlds").status_code == 200
        listed = mrp_client.get(ENDPOINT, params={"world_id": world["id"]})
        assert listed.status_code == 200, listed.text
        assert created["id"] in listed.text
        still_running = mrp_client.get(f"{ENDPOINT}/{created['id']}").json()
        assert still_running["status"] in {"queued", "running"}
        assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world
    finally:
        release.set()
    job = wait_job(mrp_client, created["id"])
    assert job["status"] == "review" and len(job["drafts"]) == 1


def test_stale_draft_edit_and_changed_idempotency_request_preserve_saved_results(mrp_client):
    world = make_world(mrp_client)
    install_archive_worker(mrp_client)
    job = ready_job(mrp_client, world)
    draft = job["drafts"][0]
    body = {"expected_revision": draft["revision"], "payload": {**draft["payload"], "title": "Reviewed Canal"}}
    response = mrp_client.patch(f"{ENDPOINT}/{job['id']}/drafts/{draft['id']}", json=body)
    assert response.status_code == 200, response.text
    edited = mrp_client.get(f"{ENDPOINT}/{job['id']}").json()
    stale = mrp_client.patch(f"{ENDPOINT}/{job['id']}/drafts/{draft['id']}", json=body)
    assert stale.status_code == 409, stale.text
    assert mrp_client.get(f"{ENDPOINT}/{job['id']}").json()["drafts"] == edited["drafts"]
    request = commit_body(edited, draft_ids=[draft["id"]])
    saved_response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=request)
    assert saved_response.status_code == 200, saved_response.text
    saved = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    altered = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json={
        **request, "draft_ids": [row["id"] for row in edited["drafts"]],
    })
    assert altered.status_code == 409, altered.text
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == saved


def test_biology_multi_batch_details_keep_overflow_facts_and_remain_saveable(mrp_client, monkeypatch):
    world = make_world(mrp_client)
    service = mrp_client.app.state.container.world_organize
    parts = [
        "SPRING FACT: Amber wings glow after sunset and require salt water. " * 350,
        "WINTER FACT: Silver wings lose their glow in cold weather and need shelter. " * 350,
    ]
    assert all(15000 < len(part) < 30000 for part in parts)
    assert sum(map(len, parts)) > 30000
    fields = ("appearance", "habitat", "culture", "abilities", "limitations")

    async def plan(job, snapshot):
        source = snapshot["sources"][0]
        offset = 0
        batches = []
        for part in parts:
            batches.append({**snapshot, "sources": [{**source, "content": part, "excerpt": "",
                "char_count": len(part), "source_offset": offset}]})
            offset += len(part)
        return batches

    def output(prompt, job_id, attempt):
        part = parts[service._read(job_id)["batch_index"]]
        candidate = archive_candidate("Seasonal Silver Moth", kind="biology", quote=part[:100], body=part[:100])
        candidate["payload"]["kind_data"] = {"classification": "species", **{field: part for field in fields}}
        return json.dumps({"archives": [candidate]})

    monkeypatch.setattr(service, "_plan_archive_batches", plan)
    _, calls = install_archive_worker(mrp_client, output)
    job = ready_job(mrp_client, world, source_text="".join(parts))
    assert len(calls) == 2 and len(job["drafts"]) == 1
    draft = job["drafts"][0]
    assert draft["kind"] == "biology"
    assert all(len(draft["payload"]["kind_data"][field]) <= 30000 for field in fields)
    assert all(part in draft["payload"]["body"] for part in parts)
    response = mrp_client.post(f"{ENDPOINT}/{job['id']}/commit-batch", json=commit_body(job))
    assert response.status_code == 200, response.text
    saved_world = mrp_client.get(f"/api/v1/worlds/{world['id']}").json()
    assert len(saved_world["archive_records"]) == len(world["archive_records"]) + 1
    saved = saved_world["archive_records"][-1]
    assert saved["kind_data"] == draft["payload"]["kind_data"]
    assert all(part in saved["body"] for part in parts)
    assert protected_world(saved_world) == protected_world(world)
