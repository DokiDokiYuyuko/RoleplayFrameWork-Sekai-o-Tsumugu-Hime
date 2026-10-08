"""Portable, branch-local control transfer with synthetic cards and no remote LLM."""
import asyncio
import io
import json
import zipfile

import pytest
from PIL import Image

from mrp.shared.models import EngineReply, GroupActor, MemoryRecord, Message, TokenUsage
from mrp.shared.player_identity import current_identity, memory_participants
from mrp.orchestrator.inspections import build_inspection
from mrp.orchestrator.player_control import PlayerSwitchConflict
from mrp.orchestrator.scene_frame import build_shared_scene_frame
from mrp.tests.test_player_switch import setup, switch


class CaptureEngine:
    def __init__(self):
        self.calls = []

    async def generate(self, character, ctx, *, on_delta=None):
        self.calls.append(ctx)
        text = f"{character.card.name}的第{ctx.turn}轮测试回复"
        if on_delta:
            on_delta(text)
        return EngineReply(content=text, usage=TokenUsage())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["serial", "parallel"])
async def test_actual_requests_identity_owner_and_scheduling(mrp_container, mode):
    r = await setup(mrp_container)
    r.state.meta.hygiene_enabled = False
    r.state.meta.proactive_turn_limit = 0
    r.state.meta.director_mode = "rules"
    r.state.meta.short_input_padding = False
    first_identity = current_identity(r.state)
    secret = (await r.player_say("向导自己的内心证据", channel="inner"))[0]
    old_public = (await r.player_say("守卫，请查看旧地图", mentions=["guard"]))[0]
    await mrp_container.persist_session(r)
    await switch(mrp_container, r, "guard")
    engine = CaptureEngine()
    r.engines = engine
    result = await r.player_say_with_reply_mode("向导和学者，请谈眼前的门", mentions=["guide", "scholar"], reply_mode=mode)
    assert [m.actor for m in result] == ["player", "guide", "scholar"]
    assert len(engine.calls) == 2
    calls = {ctx.character_id: ctx for ctx in engine.calls}
    guide, scholar = calls["guide"], calls["scholar"]
    for ctx in engine.calls:
        assert ctx.player_identity_source["person_id"] == "guard"
        assert ctx.player_persona == current_identity(r.state).persona
        assert ctx.planned_prompt.text.count("[玩家角色设定]") == 1
        assert "[玩家身份对应关系]" in ctx.planned_prompt.text
        assert "只有人物 ID 相同时" in ctx.planned_prompt.text
        assert f"玩家·向导〔guide〕" in ctx.planned_prompt.text
        assert "控制角色已切换" not in ctx.planned_prompt.text
        assert ctx.actor_labels[f"message:{old_public.id}"] == "玩家·向导〔guide〕"
    assert secret in guide.visible_messages
    assert secret not in scholar.visible_messages
    assert "向导自己的内心证据" not in scholar.planned_prompt.text
    preceding = result[1]
    assert (preceding in scholar.visible_messages) == (mode == "serial")
    assert result[0].player_identity_id != first_identity.id
    assert all(ctx.character_id != "guard" for ctx in engine.calls)
    record = r.runtime.inspections[("scholar", result[0].turn)]
    assert build_inspection(record, "scholar", result[0].turn)["player_identity_source"]["person_id"] == "guard"


@pytest.mark.asyncio
async def test_continuation_context_keeps_current_identity_and_historic_labels(mrp_container):
    r = await setup(mrp_container)
    sent = await r.player_say("守卫，请继续查看桥头", mentions=["guard"])
    reply = sent[-1]
    continued = await r.continue_message(reply.id)
    assert continued is not None
    ctx = r.runtime.inspections[("guard", reply.turn)]["ctx"]
    assert ctx.player_identity_source["person_id"] == "guide"
    assert ctx.actor_labels[f"message:{sent[0].id}"] == "玩家·向导〔guide〕"
    assert "[玩家身份对应关系]" in ctx.planned_prompt.text


@pytest.mark.asyncio
async def test_group_request_uses_current_identity_without_old_inner(mrp_container):
    r = await setup(mrp_container)
    scene = r.active_scene()
    group = GroupActor(id="crowd", label="旅店居民", scene_id=scene.id, joined_seq=0)
    r.state.groups.append(group)
    scene.group_ids.append(group.id)
    secret = (await r.player_say("向导的私密记号", channel="inner"))[0]
    await switch(mrp_container, r, "guard")
    await r.player_say_with_reply_mode("居民们，请说明路况", mentions=[group.id], reply_mode="parallel")
    inspection = r.inspection(group.id, r.state.current_turn())
    assert inspection["player_identity_source"]["person_id"] == "guard"
    assert "向导的私密记号" not in inspection["prompt"]
    assert secret.id not in inspection["visible_message_ids"]


@pytest.mark.asyncio
async def test_shared_stage_cannot_restore_away_knowledge(mrp_container):
    r = await setup(mrp_container)
    await switch(mrp_container, r, "guard", "leave")
    hidden = r._append_message(Message(
        session_id=r.state.meta.id, seq=r.state.next_seq(), turn=r.state.current_turn()+1,
        actor="player", content="离场期间才说出的密语", visible_to="all"))
    await switch(mrp_container, r, "guide", key="return")
    assert hidden not in r.state.visible_messages_for("guide")
    frame = build_shared_scene_frame(r.state, hidden.turn, viewer_ids=["guide", "scholar"])
    assert "离场期间才说出的密语" not in frame


@pytest.mark.asyncio
async def test_switch_invalidates_late_candidate_and_keeps_real_memory_ids(mrp_container):
    from mrp.tests.test_assist_anchor import CaptureAssist
    import threading
    r = await setup(mrp_container)
    old_message = (await r.player_say("守卫，我把钥匙给你", mentions=["guard"]))[0]
    legacy = MemoryRecord(character_id="guard", session_id=r.state.meta.id,
        participant_ids=["guard", "player"], content="钥匙约定", source_message_ids=[old_message.id])
    assert memory_participants(r.state, legacy) == {"guide", "guard"}
    started, release = threading.Event(), threading.Event()
    r.assist_generator = CaptureAssist(entered=started, release=release)
    task = asyncio.create_task(r.assist_builder.generate_candidates())
    assert await asyncio.to_thread(started.wait, 5)
    await switch(mrp_container, r, "guard")
    release.set()
    batch = await task
    assert batch["reason"] == "stale" and batch["options"] == []
    assert memory_participants(r.state, legacy) == {"guide", "guard"}
    assert (await r.assist_builder.generate_candidates())["reason"] == "control_boundary"


def _card(client, name):
    response = client.post("/api/v1/characters/import", files={"file": (
        "card.json", json.dumps({"name": name, "description": name+"的独立设定", "first_mes": ""}, ensure_ascii=False).encode(), "application/json")})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _story(client):
    a, b = _card(client, "向导甲"), _card(client, "守卫乙")
    buffer = io.BytesIO()
    Image.new("RGBA", (24, 24), "blue").save(buffer, format="PNG")
    png = buffer.getvalue()
    assert client.post(f"/api/v1/characters/{a}/avatar", files={"file": ("avatar.png", png, "image/png")}).status_code == 200
    response = client.post("/api/v1/sessions", json={"title": "身份隔离验收", "character_ids": [b], "player_character_id": a})
    assert response.status_code == 200, response.text
    return a, b, response.json(), png


def _switch_body(state, target, key, disposition="npc"):
    return {"target_character_id": target, "previous_disposition": disposition,
        "expected_branch_revision": state["meta"]["branch_revision"],
        "expected_player_identity_id": state["meta"]["player_identity_id"], "idempotency_key": key}


def test_media_library_deletion_branches_and_portable_export(mrp_client):
    a, b, state, png = _story(mrp_client)
    root = state["meta"]["id"]
    first_id = state["meta"]["player_identity_id"]
    first_avatar = f"/api/v1/sessions/{root}/player/avatar/{first_id}"
    assert mrp_client.get(first_avatar).content == png
    sent = mrp_client.post(f"/api/v1/sessions/{root}/messages", json={"content": "保存向导的旧发言", "mentions": [b]}).json()
    source = sent["messages"][0]
    before = mrp_client.get(f"/api/v1/sessions/{root}").json()
    fork = mrp_client.post(f"/api/v1/branches/{root}/fork", json={"message_id": before["messages"][-1]["id"],
        "title": "切换之前", "expected_revision": before["meta"]["branch_revision"], "idempotency_key": "before"})
    assert fork.status_code == 200, fork.text
    child = fork.json()["branch_id"]
    changed = mrp_client.post(f"/api/v1/sessions/{root}/player/switch", json=_switch_body(before, b, "switch-one"))
    assert changed.status_code == 200, changed.text
    current = changed.json()
    assert current["meta"]["player_character_id"] == b
    assert mrp_client.get(f"/api/v1/sessions/{child}").json()["meta"]["player_identity_id"] == first_id
    assert len(mrp_client.get(f"/api/v1/stories/{root}/worldline").json()["branches"]) == 2
    repeated = mrp_client.post(f"/api/v1/sessions/{root}/player/switch", json=_switch_body(before, b, "switch-one"))
    assert repeated.status_code == 200 and len(repeated.json()["player_identities"]) == 2
    obsolete = mrp_client.post(f"/api/v1/sessions/{root}/messages", json={"content": "过期草稿", "expected_player_identity_id": first_id})
    assert obsolete.status_code == 409
    container = mrp_client.app.state.container
    memory = MemoryRecord(character_id=a, session_id=root, content="向导自己的经历", source_message_ids=[source["id"]])
    container.memory_store.add(memory)
    assert mrp_client.delete(f"/api/v1/characters/{a}").status_code == 200
    assert any(item.id == memory.id for item in container.memory_store.records_for(a, session_id=root))
    assert mrp_client.get(first_avatar).content == png
    current = mrp_client.get(f"/api/v1/sessions/{root}").json()
    back = mrp_client.post(f"/api/v1/sessions/{root}/player/switch", json=_switch_body(current, a, "switch-back"))
    assert back.status_code == 200, back.text
    assert back.json()["player_identities"][-1]["character"]["card"]["name"] == "向导甲"
    archive = mrp_client.get(f"/api/v1/stories/{root}/export")
    assert archive.status_code == 200, archive.text
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        manifest = json.loads(zipped.read("manifest.json"))
        assert manifest["media"]
        assert zipped.read("media/"+manifest["media"][0]) == png
        payload = json.loads(zipped.read("branches/"+root+".json"))
        assert payload["player_identities"][0]["character"]["card"]["avatar_path"] is None
    imported = mrp_client.post("/api/v1/stories/import", files={"file": ("story.zip", archive.content, "application/zip")})
    assert imported.status_code == 200, imported.text
    restored_id = imported.json()["story_id"]
    restored = mrp_client.get(f"/api/v1/sessions/{restored_id}").json()
    assert restored["meta"]["player_identity_id"] == back.json()["meta"]["player_identity_id"]
    assert mrp_client.get(f"/api/v1/sessions/{restored_id}/player/avatar/{first_id}").content == png


def test_disk_write_then_failure_restores_authoritative_identity(mrp_client, monkeypatch):
    a, b, state, _ = _story(mrp_client)
    sid = state["meta"]["id"]
    container = mrp_client.app.state.container
    original_state = container.sessions.load_state_readonly_sync(sid).model_dump(mode="json")
    actual = container.persist_session
    async def save_then_fail(r):
        await actual(r)
        raise OSError("模拟索引完成后的失败")
    monkeypatch.setattr(container, "persist_session", save_then_fail)
    response = mrp_client.post(f"/api/v1/sessions/{sid}/player/switch", json=_switch_body(state, b, "failed"))
    assert response.status_code == 503 and "恢复原控制角色" in response.json()["detail"]
    assert container.sessions.load_state_readonly_sync(sid).model_dump(mode="json") == original_state
    restored = mrp_client.get(f"/api/v1/sessions/{sid}").json()
    assert restored["meta"]["player_character_id"] == a
    assert restored["meta"]["player_identity_id"] == state["meta"]["player_identity_id"]
    assert len(restored["player_identities"]) == 1


@pytest.mark.asyncio
async def test_competing_switches_only_one_commits(mrp_container):
    from mrp.orchestrator.player_control import switch_player
    r = await setup(mrp_container)
    revision, stage = r.state.meta.branch_revision, r.state.meta.player_identity_id
    async def attempt(target):
        return await switch_player(r, target_id=target, disposition="npc", expected_revision=revision,
            expected_identity=stage, idempotency_key=target, resolve_character=mrp_container.characters.get,
            data_root=mrp_container.data_root, persist=mrp_container.persist_session)
    results = await asyncio.gather(attempt("guard"), attempt("scholar"), return_exceptions=True)
    assert sum(result is True for result in results) == 1
    assert sum(isinstance(result, PlayerSwitchConflict) for result in results) == 1
    assert len(r.state.player_identities) == 2


@pytest.mark.asyncio
async def test_background_memory_cannot_commit_after_control_changed(mrp_container):
    import threading
    mrp_container.settings.memory_consolidation_enabled = True
    r = await setup(mrp_container)
    await r.player_say("守卫，请说明情况", mentions=["guard"])
    started, release = threading.Event(), threading.Event()
    class PendingConsolidator:
        usage = TokenUsage()
        def consolidate_records(self, state, owner, *args, **kwargs):
            started.set()
            assert release.wait(5)
            return [MemoryRecord(character_id=owner, session_id=state.meta.id, content="过期整理结果")]
    r.episodic_consolidator = PendingConsolidator()
    task = asyncio.create_task(r.memory_pipeline._consolidate_character("guard", r.state.current_turn(),
        min_interval=0, retries=1, reason="acceptance"))
    assert await asyncio.to_thread(started.wait, 5)
    await switch(mrp_container, r, "guard")
    release.set()
    assert await task == []
    assert not mrp_container.memory_store.records_for("guard", session_id=r.state.meta.id)
    assert mrp_container.memory_store.window_rows("guard", r.state.meta.id)[0]["status"] == "dirty"


@pytest.mark.asyncio
async def test_missing_avatar_does_not_bind_later_library_image(mrp_container):
    r = await setup(mrp_container)
    first = current_identity(r.state)
    assert first.avatar_ref is None and first.media_captured
    folder = mrp_container.paths.characters_dir / "guide"
    folder.mkdir(exist_ok=True)
    Image.new("RGBA", (24,24), "red").save(folder / "avatar.png")
    await switch(mrp_container, r, "guard")
    await switch(mrp_container, r, "guide", key="return")
    assert current_identity(r.state).avatar_ref is None
    assert first.avatar_ref is None
