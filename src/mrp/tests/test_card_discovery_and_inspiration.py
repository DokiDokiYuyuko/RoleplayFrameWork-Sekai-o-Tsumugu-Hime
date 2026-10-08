from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, PngImagePlugin

from mrp.card_discovery.schemas import CardDetail, CardHit, SearchInput, SearchPage, SourceCapabilities
from mrp.card_discovery.service import CardDiscoveryService
from mrp.card_discovery.sources import botbooru, chub
from mrp.card_inspiration.agent_tools import TOOL_DEFINITIONS, _dispatch, call_tool
from mrp.card_inspiration.schemas import BriefPatchInput, CreateJobInput, ReferenceInput
from mrp.card_inspiration.service import CardInspirationService
from mrp.shared.models import Character, CharacterCard


def _hit(source_id: str = "botbooru", card_id: str = "42", title: str = "Source") -> CardHit:
    return CardHit(
        source_id=source_id, card_id=card_id, title=title, creator="Author",
        source_url=f"https://{source_id}.example/cards/{card_id}", content_rating="sfw",
    )


def _png_card() -> bytes:
    payload = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
        "name": "Botbooru card", "description": "mapped from PNG", "personality": "curious",
    }}
    info = PngImagePlugin.PngInfo()
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    info.add_text("chara", encoded)
    image = Image.new("RGB", (2, 2))
    output = io.BytesIO()
    image.save(output, "PNG", pnginfo=info)
    return output.getvalue()


def test_connector_content_rating_and_field_mapping(monkeypatch: pytest.MonkeyPatch):
    post = {
        "id": 42, "character_name": "Botbooru card", "uploader_name": "Uploader",
        "description_excerpt": "A preview", "created_at": "2026-09-01T00:00:00Z",
        "tags": [{"name": "sfw"}, {"name": "Author", "category": "Writer"}],
    }
    assert botbooru._post_hit(post).content_rating == "sfw"
    assert botbooru._post_hit({**post, "tags": [{"name": "fantasy"}]}).content_rating == "unknown"

    node = {
        "fullPath": "author/chub-card", "name": "Chub card", "nsfw_image": False,
        "topics": ["sfw", "fantasy"], "description": "Fallback description",
        "definition": {
            "name": "Chub card", "description": "Mapped description", "personality": "Thoughtful",
            "scenario": "A station", "first_message": "Welcome.", "example_dialogs": ["<START>\nHello."],
        },
    }
    assert chub._hit(node).content_rating == "sfw"
    assert chub._hit({**node, "nsfw_image": True}).content_rating == "sensitive"
    assert chub._hit({**node, "nsfw_image": None}).content_rating == "unknown"
    assert chub._hit({**node, "topics": ["gore"]}).content_rating == "sensitive"

    async def chub_json(*args, **kwargs):
        return {"node": node}

    async def botbooru_json(*args, **kwargs):
        return post

    async def png_bytes(*args, **kwargs):
        return _png_card(), "image/png"

    monkeypatch.setattr(chub, "fetch_json", chub_json)
    monkeypatch.setattr(botbooru, "fetch_json", botbooru_json)
    monkeypatch.setattr(botbooru, "fetch_bytes", png_bytes)

    async def check_mappings():
        chub_hit, chub_card = await chub.ChubSource().full_card("author/chub-card")
        assert chub_hit.creator == "author"
        assert chub_card["description"] == "Mapped description"
        assert chub_card["first_mes"] == "Welcome."
        booru_hit, booru_card = await botbooru.BotbooruSource().full_card("42")
        assert booru_hit.creator == "Author"
        assert booru_card["name"] == "Botbooru card"
        assert booru_card["description"] == "mapped from PNG"

    asyncio.run(check_mappings())


@pytest.mark.asyncio
async def test_one_source_timeout_does_not_fail_other_source():
    service = CardDiscoveryService()

    async def timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    async def success(query: str, cursor: str | None, limit: int):
        return SearchPage(source_id="chub", status="ok", results=[_hit("chub", "author/ok", "A matching card")])

    service.sources["botbooru"].search = timeout
    service.sources["chub"].search = success
    response = await service.search(SearchInput(query="matching card"))
    by_id = {page.source_id: page for page in response.sources}
    assert by_id["botbooru"].status == "error"
    assert by_id["chub"].status == "ok"
    assert by_id["chub"].results[0].title == "A matching card"
    assert by_id["botbooru"].error == "站点搜索超时（18 秒）"


@pytest.mark.asyncio
async def test_third_connector_registers_without_public_search_changes():
    class ThirdSource:
        source_id = "third-party"

        def capabilities(self):
            return SourceCapabilities(source_id=self.source_id, label="Third", homepage="https://example.com",
                                      paging="page", full_card=True)

        async def search(self, query, cursor, limit):
            return SearchPage(source_id=self.source_id, status="ok", results=[_hit(self.source_id, "one", "Third result")])

        async def full_card(self, card_id):
            return _hit(self.source_id, card_id), {"name": "Third result"}

    service = CardDiscoveryService(sources=(ThirdSource(),))
    assert service.list_sources()[0]["source_id"] == "third-party"
    result = await service.search(SearchInput(query="third result", source_ids=["third-party"]))
    assert result.sources[0].results[0].title == "Third result"


class _DiscoveryStub:
    def __init__(self):
        card = CharacterCard(name="Frozen source", description="Original description", creator="Author")
        self.detail = CardDetail(
            hit=_hit(title="Frozen source"), card=card.model_dump(mode="json"),
            fetched_at="2026-09-30T00:00:00Z", content_sha256="a" * 64,
        )

    async def full_card(self, source_id: str, card_id: str) -> CardDetail:
        return self.detail


class _ContainerStub:
    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.characters: dict[str, Character] = {}
        self.save_count = 0
        self.settings = SimpleNamespace(
            gateway="https://example.invalid", model="test-model", model_provider="",
            provider_allow_fallbacks=True, api_key="test-key",
            generation=SimpleNamespace(request_parameters=lambda: {}),
        )

    async def save_character(self, character: Character) -> None:
        self.save_count += 1
        self.characters[character.id] = character


async def _new_job(service: CardInspirationService) -> dict:
    return await service.create(CreateJobInput(
        search_query="quiet astronomer sfw", requirement="Create a new, original character",
        references=[ReferenceInput(source_id="botbooru", card_id="42")],
    ))


def _add_draft(service: CardInspirationService, job_id: str, *, status: str = "review", revision: int = 1) -> dict:
    job = service.repo.get(job_id)
    draft = {
        "id": "draft-test", "revision": revision, "status": status,
        "payload": CharacterCard(name="New character", description="An original idea").model_dump(mode="json"),
        "aliases": ["New alias"], "committed_character_id": None,
    }
    job["drafts"] = [draft]
    service.repo.save(job)
    return draft


@pytest.mark.asyncio
async def test_selected_reference_is_frozen_as_task_snapshot(tmp_path: Path):
    discovery = _DiscoveryStub()
    service = CardInspirationService(_ContainerStub(tmp_path), discovery)  # type: ignore[arg-type]
    job = await _new_job(service)
    assert job["search_query"] == "quiet astronomer sfw"
    discovery.detail.card["description"] = "Changed after selection"
    snapshot = service.source_snapshot(job["id"])
    assert snapshot["references"][0]["card"]["description"] == "Original description"
    assert service.get(job["id"])["references"][0]["content_sha256"] == "a" * 64


def test_agent_tools_are_read_only_and_limited_to_frozen_task(tmp_path: Path):
    task_file = tmp_path / "snapshot.json"
    task_file.write_text(json.dumps({"references": [{
        "id": "ref-1", "source_id": "botbooru", "title": "A selected card", "creator": "Writer",
        "source_url": "https://botbooru.com/character/42", "content_sha256": "b" * 64,
        "card": {"name": "A selected card", "description": "A curious botanist in a quiet greenhouse."},
    }]}, ensure_ascii=False), encoding="utf-8")
    assert {row["name"] for row in TOOL_DEFINITIONS} == {"list_references", "read_reference", "search_references"}
    listed = call_tool(task_file, "list_references", {})
    assert listed["references"][0]["id"] == "ref-1"
    read = call_tool(task_file, "read_reference", {"reference_id": "ref-1", "field": "description", "max_chars": 12})
    assert read["text"] == "A curious bo"
    assert call_tool(task_file, "search_references", {"query": "greenhouse"})["results"]
    with pytest.raises(ValueError, match="不属于当前任务快照"):
        call_tool(task_file, "read_reference", {"reference_id": "other-task", "field": "description"})
    rejected = _dispatch(str(task_file), {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                           "params": {"name": "save_character", "arguments": {}}})
    assert rejected["result"]["isError"] is True
    assert json.loads((tmp_path / "telemetry.json").read_text(encoding="utf-8"))["tool_calls"] == 5


@pytest.mark.asyncio
async def test_draft_revision_conflict_and_commit_are_idempotent(tmp_path: Path):
    container = _ContainerStub(tmp_path)
    service = CardInspirationService(container, _DiscoveryStub())  # type: ignore[arg-type]
    job = await _new_job(service)
    draft = _add_draft(service, job["id"])
    payload = CharacterCard(name="Revised name", description="Edited").model_dump(mode="json")
    updated = service.edit_draft(job["id"], draft["id"], payload, ["Alias"], expected_revision=1)
    assert updated["revision"] == 2
    with pytest.raises(RuntimeError, match="草稿已在另一处更新"):
        service.edit_draft(job["id"], draft["id"], payload, [], expected_revision=1)

    result = await service.commit_draft(job["id"], draft["id"], payload, ["Alias"], expected_revision=2)
    stable_id = result["character"]["id"]
    again = await service.commit_draft(job["id"], draft["id"], payload, ["Alias"], expected_revision=2)
    assert again["character"]["id"] == stable_id
    assert again["draft"]["status"] == "committed"
    assert container.save_count == 1


@pytest.mark.asyncio
async def test_draft_keeps_restorable_field_history_and_flags_reference_overlap(tmp_path: Path):
    service = CardInspirationService(_ContainerStub(tmp_path), _DiscoveryStub())  # type: ignore[arg-type]
    job = await _new_job(service)
    draft = _add_draft(service, job["id"])
    copied = "A distinctively worded paragraph about the observatory and its keeper " * 3
    snapshot = service.source_snapshot(job["id"])
    snapshot["references"][0]["card"]["description"] = copied
    service.repo.save_snapshot(job["id"], snapshot)

    changed_payload = {**draft["payload"], "description": copied}
    updated = service.edit_draft(
        job["id"], draft["id"], changed_payload, draft["aliases"], expected_revision=1, source="agent",
    )
    history = next(item for item in updated["field_history"] if item["field"] == "description")
    assert history["previous_value"] == "An original idea"
    assert history["source"] == "agent"
    assert any("Frozen source" in issue for issue in updated["quality_review"]["issues"]), updated["quality_review"]

    restored_payload = {**updated["payload"], "description": history["previous_value"]}
    restored = service.edit_draft(
        job["id"], draft["id"], restored_payload, draft["aliases"], expected_revision=2, source="user",
    )
    assert restored["payload"]["description"] == "An original idea"
    assert restored["field_history"][-1]["source"] == "user"
    assert restored["field_history"][-1]["previous_value"] == copied


@pytest.mark.asyncio
async def test_brief_revision_and_initial_task_do_not_create_formal_asset(tmp_path: Path):
    container = _ContainerStub(tmp_path)
    service = CardInspirationService(container, _DiscoveryStub())  # type: ignore[arg-type]
    job = await _new_job(service)
    assert container.characters == {}
    updated = service.update_brief(job["id"], BriefPatchInput(
        expected_revision=1, requirement="Refined concept", detail="Keep it concise", borrow="Use contrast", avoid="No copying",
    ))
    assert updated["brief_revision"] == 2
    with pytest.raises(RuntimeError, match="简报已在另一处更新"):
        service.update_brief(job["id"], BriefPatchInput(expected_revision=1, requirement="Stale"))
    assert container.characters == {}


@pytest.mark.asyncio
async def test_agent_turn_reads_only_snapshot_and_returns_unapplied_suggestions(tmp_path: Path):
    from mrp.card_inspiration.agent_tools import call_tool

    container = _ContainerStub(tmp_path)
    service = CardInspirationService(container, _DiscoveryStub())  # type: ignore[arg-type]
    service.agent_enabled = True
    job = await _new_job(service)

    async def fake_run(**kwargs):
        snapshot = service.repo.snapshot(job["id"])
        assert snapshot["brief"]["requirement"] == "Create a new, original character"
        assert call_tool(kwargs["task_file"], "list_references", {})["references"][0]["id"] == "ref-1"
        call_tool(kwargs["task_file"], "search_references", {"query": "Original"})
        call_tool(kwargs["task_file"], "read_reference", {"reference_id": "ref-1", "field": "description"})
        return json.dumps({
            "reply": "The selected card contrasts a quiet setting with an enthusiastic character.",
            "brief_suggestion": {"borrow": "Use contrast", "avoid": None},
            "field_suggestions": [{"field": "personality", "value": "Warm and curious.", "rationale": "Keep the new character distinct."}],
        }, ensure_ascii=False)

    service.agent_worker.run = fake_run  # type: ignore[method-assign]
    queued = service.start_agent_turn(job["id"], "Analyze the abstract creative pattern.")
    assert queued["status"] == "agent_running"
    await service.tasks[job["id"]]
    completed = service.get(job["id"])
    assert completed["status"] == "ready", completed["errors"]
    assert completed["agent_messages"][-1]["field_suggestions"][0]["field"] == "personality"
    assert completed["agent_suggestion"]["borrow"] == "Use contrast"
    assert container.characters == {}


@pytest.mark.asyncio
async def test_commit_recovers_persisted_intent_after_interruption(tmp_path: Path):
    container = _ContainerStub(tmp_path)
    service = CardInspirationService(container, _DiscoveryStub())  # type: ignore[arg-type]
    job = await _new_job(service)
    draft = _add_draft(service, job["id"], status="committing", revision=2)
    draft["commit_character_id"] = "char-stable"
    doc = service.repo.get(job["id"])
    doc["drafts"] = [draft]
    service.repo.save(doc)

    resumed = await service.commit_draft(job["id"], draft["id"], draft["payload"], draft["aliases"], expected_revision=1)
    assert resumed["character"]["id"] == "char-stable"
    assert resumed["draft"]["status"] == "committed"
    assert container.characters["char-stable"].card.name == "New character"
    assert container.save_count == 1
