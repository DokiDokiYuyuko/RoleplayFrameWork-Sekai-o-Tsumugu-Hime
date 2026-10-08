import json

import pytest

from mrp.contracts.story import project_message
from mrp.server.routers.story_views import _window
from mrp.shared.models import (
    SessionMeta, SessionState, Character, CharacterCard, Message, GenerationMeta,
    GenerationProvenance, MessageVariant, TurnRun,
)


def state(count=230):
    result = SessionState(schema_version=3, meta=SessionMeta(id="read-contract"),
        characters=[Character(id="npc", card=CharacterCard(name="Synthetic", description="Visible profile",
            first_mes="Synthetic greeting", system_prompt="PRIVATE MATERIAL"))])
    result.messages = [Message(id=f"m{i}", session_id=result.meta.id, seq=i, turn=i // 2 + 1,
        actor="player", content=f"synthetic {i}") for i in range(count)]
    return result


def test_projection_excludes_recursive_generation_materials():
    source = state(1).messages[0]
    source.generation_meta = GenerationMeta(provenance=GenerationProvenance(baseline_state={"secret": "x"}),
        memory_recall=[{"private": "x"}])
    source.variants = [MessageVariant(content="candidate", generation_meta=source.generation_meta)]
    projected = project_message(source)
    assert "baseline_state" not in json.dumps(projected)
    assert "memory_recall" not in json.dumps(projected)
    assert source.generation_meta.provenance.baseline_state == {"secret": "x"}


def test_pages_keep_submission_group_and_no_gaps():
    messages = [{"id": str(i), "seq": i, "input_group_id": "group" if 98 <= i <= 103 else None} for i in range(203)]
    tail, cursor = _window(messages, limit=100)
    assert cursor == 98 and len(tail) == 105
    older, cursor = _window(messages, limit=100, before_seq=cursor)
    assert cursor is None
    assert [item["seq"] for item in older + tail] == list(range(203))


@pytest.mark.asyncio
async def test_read_view_does_not_build_runner_or_write(mrp_container, monkeypatch):
    from mrp.server.routers.story_views import story_view, story_setup
    from mrp.contracts.story import StoryView
    value = state()
    mrp_container.sessions.sqlite_new_stories = False
    await mrp_container.sessions.save_state(value)
    path = mrp_container.sessions.path_for(value.meta.id)
    original = path.read_bytes()
    async def forbidden(*args, **kwargs):
        raise AssertionError("read path must not construct a runner")
    monkeypatch.setattr(mrp_container, "load_session", forbidden)
    result = StoryView.model_validate(await story_view(value.meta.id, limit=100, before_seq=None, around="m20", container=mrp_container))
    assert len(result.messages) == 100
    assert result.turn == 115 and result.session.turn == 115 and result.latest_seq == 229
    assert result.messages[-1].turn < result.turn
    assert "PRIVATE MATERIAL" not in result.model_dump_json()
    assert result.characters[0].card.description == "Visible profile"
    assert result.characters[0].card.first_mes == "Synthetic greeting"
    assert "state_revisions" not in result.model_dump_json()
    assert path.read_bytes() == original
    setup = await story_setup(value.meta.id, container=mrp_container)
    assert setup["meta"]["branch_revision"] == value.meta.branch_revision
    assert "world_archive_records" not in setup["meta"]


@pytest.mark.asyncio
async def test_view_keeps_durable_head_and_pending_prefix(mrp_container):
    from mrp.server.routers.story_views import story_view
    from mrp.contracts.story import StoryView
    value = state(2)
    await mrp_container.sessions.save_state(value)
    runner = await mrp_container.load_session(value.meta.id)
    runner.state.meta.title = "UNCOMMITTED"
    pending = Message(id="live", session_id=value.meta.id, seq=2, turn=2, actor="npc", content="prefix", status="pending")
    runner.runtime.pending_messages[pending.id] = pending.model_dump(mode="json")
    result = StoryView.model_validate(await story_view(value.meta.id, limit=100, before_seq=None, around=None, container=mrp_container))
    assert result.session.title != "UNCOMMITTED"
    assert result.messages[-1].content == "prefix"
    assert result.messages[-1].status == "pending"


def test_http_validation_and_legacy_shape(mrp_client):
    import asyncio
    value = state(5)
    asyncio.run(mrp_client.app.state.container.sessions.save_state(value))
    response = mrp_client.get(f"/api/v1/sessions/{value.meta.id}/view")
    assert response.status_code == 200, response.text
    assert response.json()["session"]["id"] == value.meta.id
    assert mrp_client.get(f"/api/v1/sessions/{value.meta.id}/view?limit=101").status_code == 422
    assert mrp_client.get(f"/api/v1/sessions/{value.meta.id}/view?around=missing").status_code == 404
    assert mrp_client.get(f"/api/v1/sessions/{value.meta.id}/view?around=m1&before_seq=2").status_code == 422
    legacy = mrp_client.get(f"/api/v1/sessions/{value.meta.id}").json()
    assert "meta" in legacy and "state_revisions" in legacy


@pytest.mark.asyncio
async def test_sqlite_view_and_setup_never_assemble_whole_history(mrp_container, monkeypatch):
    from mrp.server.routers.story_views import story_view, story_setup
    from mrp.contracts.story import StoryView, SessionSetup
    from mrp.shared.models import Lorebook, LorebookEntry
    value = state(1000)
    value.lorebooks = [Lorebook(id="embedded", name="Synthetic book", entries=[LorebookEntry(uid=1, content="DO NOT TRANSFER")])]
    value.meta.lorebook_ids = ["embedded"]
    await mrp_container.sessions.save_state(value)
    async def forbidden(*args, **kwargs):
        raise AssertionError("whole-state reads are forbidden for sqlite view/setup")
    monkeypatch.setattr(mrp_container.sessions, "load_state_readonly", forbidden)
    result = StoryView.model_validate(await story_view(value.meta.id, limit=100, before_seq=None, around="m20", container=mrp_container))
    assert result.turn == 500 and result.latest_seq == 999
    assert len(result.messages) <= 100
    assert result.session.turn == result.turn
    setup = SessionSetup.model_validate(await story_setup(value.meta.id, container=mrp_container))
    assert setup.lorebooks[0].entry_count == 1 and setup.lorebooks[0].entries == []
    assert "DO NOT TRANSFER" not in setup.model_dump_json()
