"""Synthetic scene-image commits: presentation only, replay and failure isolation."""
import json
from pathlib import Path
import pytest
from mrp.tests.test_command_replay import client_for
from mrp.tests.test_ordinary_turn_runs import rig
from mrp.shared.models import Scene
from mrp.shared.scene_images import BUILTIN_SCENE_IMAGE_IDS


def test_public_catalog_and_legacy_scene_defaults():
    catalog = json.loads((Path(__file__).parents[2] / "web/src/appearance/moonweaveCatalog.json").read_text(encoding="utf-8"))
    assert BUILTIN_SCENE_IMAGE_IDS == {image["id"] for image in catalog["scenes"]}
    assert Scene(title="Synthetic legacy").builtin_image_id is None


@pytest.mark.asyncio
async def test_scene_image_persists_replays_and_preserves_story(tmp_path):
    c, runner, engine, *_ = await rig(tmp_path)
    scene = runner.active_scene()
    before = runner.state.model_dump(mode="json")
    try:
        url = f"/api/v1/sessions/{runner.state.meta.id}/scenes/{scene.id}/image"
        revision = runner.state.meta.branch_revision
        body = {"builtin_image_id": "coast-ferry-night", "expected_branch_revision": revision}
        async with client_for(c) as client:
            headers = {"X-Operation-ID": "synthetic-scene-image"}
            first = await client.patch(url, json=body, headers=headers)
            assert first.status_code == 200, first.text
            repeat = await client.patch(url, json=body, headers=headers)
            assert repeat.json() == first.json()
            assert repeat.headers["X-Branch-Revision"] == first.headers["X-Branch-Revision"]
            assert runner.state.meta.branch_revision == revision + 1
            assert (await c.sessions.load_state(runner.state.meta.id)).scenes[0].builtin_image_id == "coast-ferry-night"
            assert not engine.contexts
            assert runner.state.active_scene_id == before["active_scene_id"]
            assert runner.state.messages == []
            assert scene.title == runner.active_scene().title
            conflict = await client.patch(url, json={**body, "builtin_image_id": "rain-alley"}, headers={"X-Operation-ID": "synthetic-stale-scene"})
            assert conflict.status_code == 409
            invalid = await client.patch(url, json={"builtin_image_id": "invented-scene", "expected_branch_revision": revision + 1}, headers={"X-Operation-ID": "synthetic-invalid-image"})
            assert invalid.status_code == 422
            assert runner.active_scene().builtin_image_id == "coast-ferry-night"
            removed = await client.patch(url, json={"builtin_image_id": None, "expected_branch_revision": revision + 1}, headers={"X-Operation-ID": "synthetic-remove-image"})
            assert removed.status_code == 200, removed.text
            assert runner.active_scene().builtin_image_id is None
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_failed_commit_keeps_previous_image_and_retry_identity(tmp_path, monkeypatch):
    c, runner, *_ = await rig(tmp_path)
    try:
        scene = runner.active_scene()
        url = f"/api/v1/sessions/{runner.state.meta.id}/scenes/{scene.id}/image"
        revision = runner.state.meta.branch_revision
        commit = c.sessions.commit_state
        async def failed(*args, **kwargs):
            raise OSError("Synthetic image commit failed")
        body = {"builtin_image_id": "rain-alley", "expected_branch_revision": revision}
        headers = {"X-Operation-ID": "synthetic-retry-image"}
        async with client_for(c) as client:
            monkeypatch.setattr(c.sessions, "commit_state", failed)
            failure = await client.patch(url, json=body, headers=headers)
            assert failure.status_code >= 500
            assert runner.active_scene().builtin_image_id is None
            assert runner.state.meta.branch_revision == revision
            monkeypatch.setattr(c.sessions, "commit_state", commit)
            success = await client.patch(url, json=body, headers=headers)
            assert success.status_code == 200, success.text
            assert runner.active_scene().builtin_image_id == "rain-alley"
            runner.state.active_scene_id = None
            rejected = await client.patch(url, json={"builtin_image_id": None, "expected_branch_revision": runner.state.meta.branch_revision}, headers={"X-Operation-ID": "synthetic-switched-scene"})
            assert rejected.status_code == 409
            assert runner.state.scenes[0].builtin_image_id == "rain-alley"
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_image_is_not_scene_prompt_or_memory_generation_identity(tmp_path, monkeypatch):
    from mrp.orchestrator.scene_frame import build_shared_scene_frame
    c, runner, *_ = await rig(tmp_path)
    calls = []
    async def captured(_runner, payload, *, operation_id):
        calls.append((payload, operation_id))
        return {"records": []}
    try:
        monkeypatch.setattr(c.memory_commands, "consolidate", captured)
        scene = runner.active_scene()
        scene.turn_end = 0
        frame = build_shared_scene_frame(runner.state, 0)
        await c.memory_commands.summarize_scene(runner, scene)
        before = list(calls)
        calls.clear()
        scene.builtin_image_id = "coast-ferry-night"
        assert build_shared_scene_frame(runner.state, 0) == frame
        await c.memory_commands.summarize_scene(runner, scene)
        assert calls == before
        assert calls
        assert all("builtin_image_id" not in payload["scene"] for payload, _ in calls)
    finally:
        await c.aclose()
