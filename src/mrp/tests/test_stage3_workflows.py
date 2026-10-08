"""Long-story closure with synthetic data only; no provider calls or live data roots."""
from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from datetime import timedelta

import pytest

from mrp.importers.st_chat import build_import_state, import_st_chat, parse_st_chat
from mrp.orchestrator.story_review import compare_branches, review_branch
from mrp.orchestrator.worldline_state import BranchPointUnavailable, state_at
from mrp.shared.models import Character, CharacterCard, MemoryRecord, Message, SessionMeta, SessionState, StoryEvent, utcnow
from mrp.storage.personal_library import backup_library, restore_library, verify_library


def _jsonl(*rows):
    return "\n".join(json.dumps(x, ensure_ascii=False) for x in rows).encode("utf-8")


def _sample():
    return _jsonl({"user_name": "访客", "character_name": "灯塔守卫", "chat_metadata": {"summary": "不可靠摘要"}},
                  {"name": "灯塔守卫", "is_user": False, "mes": "欢迎", "swipes": ["另一候选", "欢迎"], "swipe_id": 1},
                  {"name": "访客", "is_user": True, "mes": "借钥匙"},
                  {"name": "灯塔守卫", "is_user": False, "mes": "钥匙给你", "extra": {"memory": "不要迁入"}})


def _character(client):
    response = client.post("/api/v1/characters/import", files={"file": ("synthetic.json", json.dumps({"name": "守卫", "description": "旧设定", "first_mes": "欢迎"}).encode(), "application/json")})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_st_preview_import_identity_variants_and_idempotency(mrp_client):
    cid = _character(mrp_client)
    raw = _sample()
    preview = mrp_client.post("/api/v1/chat-import/st/preview", files={"file": ("chat.jsonl", raw, "application/jsonl")})
    assert preview.status_code == 200, preview.text
    info = preview.json()
    assert info["message_count"] == 3 and info["variant_count"] == 2 and info["degraded_fields"]
    payload = {"jsonl": raw.decode(), "source_sha256": info["source_sha256"], "character_id": cid,
               "user_name": "映射玩家", "title": "旧旅程", "acknowledge_degraded": True}
    denied = mrp_client.post("/api/v1/chat-import/st", json={**payload, "acknowledge_degraded": False})
    assert denied.status_code == 422
    result = mrp_client.post("/api/v1/chat-import/st", json=payload)
    assert result.status_code == 200, result.text
    sid = result.json()["branch_id"]
    repeat = mrp_client.post("/api/v1/chat-import/st", json=payload)
    assert repeat.status_code == 200 and repeat.json()["repeated"] and repeat.json()["branch_id"] == sid
    state = mrp_client.get(f"/api/v1/sessions/{sid}").json()
    assert len(state["messages"]) == 3 and not state["usage_records"]
    assert state["messages"][0]["actor"] == cid
    assert state["messages"][0]["active_variant"] == 1
    assert len(state["messages"][0]["variants"]) == 2
    assert all(not m["post_state_revision_id"] and not m["generation_meta"] for m in state["messages"])
    assert state["player_identities"][0]["name"] == "映射玩家"
    assert state["messages"][1]["person_id"] == state["player_identities"][0]["person_id"]
    assert not mrp_client.app.state.container.memory_store.records_for(cid, session_id=sid)
    stale = mrp_client.post("/api/v1/chat-import/st", json={**payload, "jsonl": raw.decode() + "\n"})
    assert stale.status_code == 422


def test_st_single_person_only_and_no_historic_checkpoint():
    raw = _sample()
    character = Character(card=CharacterCard(name="守卫"))
    state, _ = build_import_state(raw, character, user_name="玩家", title="旅程")
    with pytest.raises(BranchPointUnavailable):
        state_at(state, state.messages[0].id)
    group = raw + b'\n' + json.dumps({"name": "another", "is_user": False, "mes": "group"}).encode()
    with pytest.raises(ValueError, match="只支持"):
        parse_st_chat(group)
    system = _jsonl({"user_name": "u", "character_name": "c"},
                   {"name": "c", "is_user": False, "mes": "text"},
                   {"name": "system", "is_user": False, "is_system": True, "mes": "script result"})
    state, preview = build_import_state(system, character, user_name="玩家", title="旅程")
    assert state.messages[-1].kind == "ooc" and not state.messages[-1].can_see(character.id)
    assert len(preview["warnings"]) >= 3


def test_st_repeated_import_never_overwrites_an_existing_unreadable_story(mrp_client):
    mrp_client.app.state.container.sessions.sqlite_new_stories = False
    cid = _character(mrp_client)
    raw = _sample()
    payload = {"jsonl": raw.decode(), "source_sha256": parse_st_chat(raw)[2]["source_sha256"],
        "character_id": cid, "user_name": "映射玩家", "acknowledge_degraded": True}
    first = mrp_client.post("/api/v1/chat-import/st", json=payload)
    assert first.status_code == 200
    sid = first.json()["branch_id"]
    path = mrp_client.app.state.container.sessions.path_for(sid)
    with mrp_client.app.state.container.sessions.story_db.transaction() as connection:
        connection.execute('UPDATE branches SET active=0 WHERE id=?', (sid,))
    original = b'{"synthetic continued story":"must remain",invalid JSON'
    path.write_bytes(original)
    repeat = mrp_client.post("/api/v1/chat-import/st", json=payload)
    assert repeat.status_code == 409, repeat.text
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_st_concurrent_containers_publish_once_without_story_replacement(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from mrp.storage.paths import AppPaths
    from mrp.storage.session_repo import SessionRepo
    paths = AppPaths(tmp_path / "synthetic-concurrent").ensure()
    character = Character(id="char-guard", card=CharacterCard(name="守卫"))
    noop = SimpleNamespace(schedule=lambda *args, **kwargs: None)
    containers = [SimpleNamespace(characters={character.id: character}, sessions=SessionRepo(paths),
        lorebooks={}, story_search=noop, story_backups=noop) for _ in range(2)]
    barrier, entered = asyncio.Event(), []
    original_create = SessionRepo.create_state_exclusive
    async def create_at_once(self, state, memory_watermark=None):
        entered.append(state.meta.id)
        if len(entered) == 2:
            barrier.set()
        await barrier.wait()
        return await original_create(self, state, memory_watermark)
    monkeypatch.setattr(SessionRepo, "create_state_exclusive", create_at_once)
    raw = _sample()
    async def migrate(container, title):
        return await import_st_chat(container, raw, source_sha256=parse_st_chat(raw)[2]["source_sha256"],
            character_id=character.id, user_name="玩家", title=title, acknowledge_degraded=True)
    results = await asyncio.gather(migrate(containers[0], "first"), migrate(containers[1], "second"))
    assert sorted(result["repeated"] for result in results) == [False, True]
    assert results[0]["branch_id"] == results[1]["branch_id"]
    stored = await containers[0].sessions.load_state_readonly(results[0]["branch_id"])
    assert stored is not None and len(stored.messages) == 3 and stored.meta.branch_revision == 1
    before = containers[0].sessions.path_for(stored.meta.id).read_bytes()
    await migrate(containers[1], "attempted replacement")
    assert containers[0].sessions.path_for(stored.meta.id).read_bytes() == before
    assert not list(paths.sessions_dir.glob(".mrp-create-*.tmp"))


def test_review_and_compare_never_mix_private_or_future_evidence(mrp_container):
    cid = "char-guard"
    now = utcnow()
    common = Message(id="msg-common", session_id="sess-a", seq=1, turn=1, actor=cid,
                     content="共同经历", created_at=now)
    private = Message(id="msg-private", session_id="sess-a", seq=2, turn=2, actor="player", content="秘密",
                      visible_to=["player"], known_to=["person-u"], created_at=now + timedelta(seconds=1))
    left = SessionState(schema_version=3, meta=SessionMeta(id="sess-a", story_id="sess-story"),
                        characters=[Character(id=cid, card=CharacterCard(name="守卫"))], messages=[common, private])
    right = left.model_copy(deep=True)
    right.meta.id = "sess-b"
    right.messages = [common.model_copy(deep=True), Message(id="msg-future", session_id="sess-b", seq=2,
        turn=2, actor=cid, content="另一条未来", created_at=now + timedelta(seconds=1))]
    left.story_events.append(StoryEvent(anchor_message_id=private.id, title="私人事件", visible_to="all"))
    for sid, source, text in [("sess-a", common, "本线往事"), ("sess-a", private, "秘密记忆"), ("sess-b", right.messages[-1], "未来记忆")]:
        mrp_container.memory_store.add(MemoryRecord(character_id=cid, session_id=sid, content=text,
            source_message_ids=[source.id], source_fingerprints={source.id: source.fingerprint}, created_at=now))
    review = review_branch(left, mrp_container.memory_store, actor_id=cid)
    encoded = json.dumps(review, ensure_ascii=False)
    assert "秘密" not in encoded and "私人事件" not in encoded and "未来" not in encoded
    assert "本线往事" in encoded
    compared = compare_branches(left, right, mrp_container.memory_store, actor_id=cid)
    assert compared["common_anchor"]["message_id"] == common.id
    assert not compared["left"]["messages"] and len(compared["right"]["messages"]) == 1
    assert "未来" not in json.dumps(compared["left"], ensure_ascii=False)
    old = review_branch(right, mrp_container.memory_store, actor_id=cid, anchor_message_id=common.id)
    assert "未来" not in json.dumps(old, ensure_ascii=False)
    with pytest.raises(ValueError):
        review_branch(left, mrp_container.memory_store, actor_id=cid, anchor_message_id=private.id)
    right.meta.story_id = "unrelated"
    with pytest.raises(ValueError, match="同一故事"):
        compare_branches(left, right, mrp_container.memory_store)


def test_selected_static_updates_preserve_history_ids_and_other_branch(mrp_client):
    cid = _character(mrp_client)
    state = mrp_client.post("/api/v1/sessions", json={"title": "旅程", "character_ids": [cid]}).json()
    sid = state["meta"]["id"]
    anchor = state["messages"][-1]
    branch = mrp_client.post(f"/api/v1/branches/{sid}/fork", json={"message_id": anchor["id"], "title": "旁线",
        "expected_revision": state["meta"]["branch_revision"], "idempotency_key": "static-test"})
    assert branch.status_code == 200, branch.text
    other = branch.json()["branch_id"]
    update = mrp_client.patch(f"/api/v1/characters/{cid}", json={"card": {"description": "新版设定", "personality": "新版性格"}})
    assert update.status_code == 200, update.text
    report = mrp_client.get(f"/api/v1/branches/{sid}/asset-updates").json()
    key = f"character:{cid}:description"
    assert key in {x["key"] for x in report["changes"]}
    apply = mrp_client.post(f"/api/v1/branches/{sid}/asset-updates/apply", json={"expected_revision": report["branch_revision"],
        "source_digest": report["source_digest"], "selected_fields": [key]})
    assert apply.status_code == 200, apply.text
    changed = mrp_client.get(f"/api/v1/sessions/{sid}").json()
    untouched = mrp_client.get(f"/api/v1/sessions/{other}").json()
    assert changed["characters"][0]["card"]["description"] == "新版设定"
    assert changed["characters"][0]["card"]["personality"] == ""
    assert untouched["characters"][0]["card"]["description"] == "旧设定"
    assert changed["messages"] == state["messages"]
    assert changed["characters"][0]["id"] == cid
    stale = mrp_client.post(f"/api/v1/branches/{sid}/asset-updates/apply", json={"expected_revision": report["branch_revision"],
        "source_digest": report["source_digest"], "selected_fields": [key]})
    assert stale.status_code == 409


def test_context_comparison_preserves_actual_and_labels_next_preview(mrp_client):
    cid = _character(mrp_client)
    state = mrp_client.post("/api/v1/sessions", json={"title": "旅程", "character_ids": [cid]}).json()
    sid = state["meta"]["id"]
    response = mrp_client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "请开门", "force_character": cid})
    assert response.status_code == 200, response.text
    current = mrp_client.get(f"/api/v1/sessions/{sid}").json()
    message = next(m for m in reversed(current["messages"]) if m["actor"] == cid and m["generation_id"])
    url = f"/api/v1/sessions/{sid}/context-comparison/{message['id']}/{message['generation_id']}"
    first = mrp_client.get(url)
    assert first.status_code == 200, first.text
    actual = first.json()["actual"]
    assert actual["mode"] == "actual" and first.json()["preview"]["mode"] == "preview"
    assert first.json()["mode"] == "actual_vs_next_preview" and not first.json()["input_same"]
    updated = mrp_client.patch(f"/api/v1/characters/{cid}", json={"card": {"description": "新修订的设定"}})
    assert updated.status_code == 200
    report = mrp_client.get(f"/api/v1/branches/{sid}/asset-updates").json()
    applied = mrp_client.post(f"/api/v1/branches/{sid}/asset-updates/apply", json={
        "expected_revision": report["branch_revision"], "source_digest": report["source_digest"],
        "selected_fields": [f"character:{cid}:description"]})
    assert applied.status_code == 200, applied.text
    comparison = mrp_client.get(url).json()
    assert comparison["actual"] == actual
    row = next(x for x in comparison["sources"] if x["entry_id"] == "character_persona")
    assert row["status"] == "changed" and "旧设定" in row["actual"]["content"]
    assert "新修订的设定" in row["preview"]["content"]
    correction = next(x for x in comparison["corrections"] if x["entry_id"] == "character_persona")
    assert correction["editable"] and correction["target"]["character_id"] == cid
    assert mrp_client.get(url.rsplit("/", 1)[0] + "/missing-record").status_code == 404
    options_url = f"/api/v1/sessions/{sid}/messages/{message['id']}/correction-options"
    options = mrp_client.get(options_url)
    assert options.status_code == 200, options.text
    opts = options.json()
    body = {name: opts[name] for name in ("expected_branch_revision", "expected_fingerprint", "expected_player_identity_id", "options_digest")}
    body.update(operation_id="explicit-correction", selected_static_fields=[f"character:{cid}:description"], selected_memory_revisions={})
    regen_url = options_url.replace("correction-options", "regenerate-corrected")
    response = mrp_client.post(regen_url, json=body)
    assert response.status_code == 200, response.text
    new_message = next(m for m in response.json()["messages"] if m["id"] == message["id"])
    materials = mrp_client.get(f"/api/v1/sessions/{sid}/context-materials/{message['id']}/{new_message['generation_id']}")
    assert materials.status_code == 200
    persona = next(row for row in materials.json()["corrections"] if row["source"] == "character")
    assert "新修订的设定" in persona["actual_content"] and "旧设定" not in persona["actual_content"]
    repeat = mrp_client.post(regen_url, json=body)
    assert repeat.status_code == 200 and repeat.json() == response.json()


def test_offline_private_library_backup_roundtrip_hash_and_nonoverwrite(tmp_path):
    source = tmp_path / "synthetic-source"
    for directory in ["characters", "worlds", "sessions", "model_requests", "memories", "usage-ledger", "story_media"]:
        (source / directory).mkdir(parents=True)
    (source / "settings.json").write_text(json.dumps({"model": "fake", "api_key": "synthetic-secret", "provider_api_keys": {"fake": "other-secret"}}))
    (source / "local_credentials.json").write_text('{"token":"not-backed-up"}')
    (source / "worlds" / "world.json").write_text('{"body":"complete synthetic manuscript"}')
    (source / "sessions" / "sess.json").write_text('{"story":"synthetic"}')
    (source / "usage-ledger" / "sess.jsonl").write_text('{"tokens":7}')
    (source / "model_requests" / "req.json").write_text('{"prompt":"synthetic request"}')
    (source / "story_media" / "avatar.bin").write_bytes(b"synthetic-media")
    database = source / "memories" / "memory.db"
    with sqlite3.connect(database) as conn:
        conn.execute("create table synthetic (text)")
        conn.execute("insert into synthetic values ('remember')")
    output = tmp_path / "library.zip"
    backup = backup_library(source, output, offline_confirmed=True)
    assert backup["ok"] and not backup["secrets_included"]
    raw = output.read_bytes()
    verified = verify_library(raw)
    assert "usage-ledger" in verified["categories"]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert "local_credentials.json" not in archive.namelist()
        assert b"synthetic-secret" not in archive.read("settings.json")
        members = {name: archive.read(name) for name in archive.namelist()}
    target = tmp_path / "restored"
    restore_library(raw, target, offline_confirmed=True)
    assert (target / "story_media" / "avatar.bin").read_bytes() == b"synthetic-media"
    assert (target / "worlds" / "world.json").read_text() == (source / "worlds" / "world.json").read_text()
    with sqlite3.connect(target / "memories" / "memory.db") as conn:
        assert conn.execute("select text from synthetic").fetchone() == ("remember",)
    with pytest.raises(FileExistsError):
        restore_library(raw, target, offline_confirmed=True)
    members["sessions/sess.json"] = b'{"story":"tampered"}'
    altered = io.BytesIO()
    with zipfile.ZipFile(altered, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    untouched = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="哈希"):
        restore_library(altered.getvalue(), untouched, offline_confirmed=True)
    assert not untouched.exists()
    with pytest.raises(ValueError, match="停止"):
        backup_library(source, tmp_path / "denied.zip", offline_confirmed=False)


@pytest.mark.parametrize("names", [["sessions/../outside.json"], ["sessions/CON.json"],
                                 ["sessions/Case.json", "sessions/case.json"],
                                 ["sessions/story", "sessions/story/message.json"]])
def test_library_rejects_unsafe_archive_paths_before_creating_target(tmp_path, names):
    import hashlib
    members = {name: b"synthetic" for name in names}
    manifest = {"format": "mrp.personal_library", "format_version": 1, "secrets_included": False,
        "files": [{"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in members.items()]}
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, raw in members.items():
            archive.writestr(name, raw)
    target = tmp_path / "must-remain-absent"
    with pytest.raises(ValueError):
        restore_library(archive_bytes.getvalue(), target, offline_confirmed=True)
    assert not target.exists()


def test_review_open_matters_do_not_restore_completed_or_unknown_tasks(mrp_container):
    cid, sid = "char-task", "sess-task"
    source = Message(id="msg-task", session_id=sid, seq=1, turn=1, actor=cid, content="任务依据")
    state = SessionState(schema_version=3, meta=SessionMeta(id=sid),
        characters=[Character(id=cid, card=CharacterCard(name="守卫"))], messages=[source])
    for status in ("open", "completed", "cancelled", "unknown"):
        mrp_container.memory_store.add(MemoryRecord(id=f"mem-{status}", character_id=cid, session_id=sid,
            category="unfinished", content=status, matter_status=status,
            source_message_ids=[source.id], source_fingerprints={source.id: source.fingerprint}))
    report = review_branch(state, mrp_container.memory_store, actor_id=cid)
    assert [record["matter_status"] for record in report["unfinished"]] == ["open"]
    assert {record["matter_status"] for record in report["memories"]} == {"open", "completed", "cancelled", "unknown"}


def test_private_library_materializes_wal_without_copying_or_removing_sidecars(tmp_path):
    source = tmp_path / "wal-source"
    (source / "memories").mkdir(parents=True)
    database = source / "memories" / "memory.db"
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("pragma journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("create table evidence (value)")
        connection.execute("insert into evidence values ('committed WAL evidence')")
        connection.commit()
        wal = database.with_name("memory.db-wal")
        assert wal.exists()
        output = tmp_path / "wal.zip"
        backup_library(source, output, offline_confirmed=True)
        assert wal.exists()
        with zipfile.ZipFile(output) as archive:
            assert not any(name.endswith(("-wal", "-shm")) for name in archive.namelist())
        target = tmp_path / "wal-restored"
        restore_library(output.read_bytes(), target, offline_confirmed=True)
        with sqlite3.connect(target / "memories" / "memory.db") as restored:
            assert restored.execute("select value from evidence").fetchone() == ("committed WAL evidence",)
    finally:
        connection.close()


def test_private_library_refuses_backup_payload_and_temporary_files_inside_code(tmp_path, monkeypatch):
    import mrp.storage.personal_library as library
    from mrp.storage.paths import validate_data_root
    code = tmp_path / "synthetic-code"
    code.mkdir()
    source = tmp_path / "synthetic-data"
    (source / "sessions").mkdir(parents=True)
    (source / "sessions" / "sess.json").write_text('{"story":"private synthetic"}')
    monkeypatch.setattr(library, "validate_data_root", lambda path: validate_data_root(path, code))
    with pytest.raises(ValueError, match="outside the code"):
        backup_library(source, code / "private.zip", offline_confirmed=True)
    assert not any(code.iterdir())
