"""v6 story workflow over isolated fake-engine data, including portable archives."""
from __future__ import annotations

import io
import json
import zipfile

from fastapi.testclient import TestClient

from mrp.shared.models import MemoryRecord


def _character(client: TestClient) -> str:
    response = client.post("/api/v1/characters/import", files={
        "file": ("card.json", json.dumps({"name": "阿澜", "first_mes": "你终于到了。"}, ensure_ascii=False).encode(), "application/json"),
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _story(client: TestClient, cid: str) -> tuple[str, dict]:
    response = client.post("/api/v1/sessions", json={"title": "灯塔", "character_ids": [cid]})
    assert response.status_code == 200, response.text
    state = response.json()
    return state["meta"]["id"], state


def test_branch_event_save_and_story_archive(mrp_client: TestClient):
    container = mrp_client.app.state.container
    cid = _character(mrp_client)
    root, state = _story(mrp_client, cid)
    opening = state["messages"][-1]
    assert opening["post_state_revision_id"]
    preview = mrp_client.get(f"/api/v1/sessions/{root}/context-preview/{cid}")
    assert preview.status_code == 200, preview.text
    assert preview.json()["mode"] == "preview" and preview.json()["branch_id"] == root
    assert "sources" in preview.json() and "not_triggered" in preview.json()
    first = mrp_client.get(f"/api/v1/branches/{root}/messages/{opening['id']}/point")
    assert first.status_code == 200 and first.json()["forkable"]

    memory = MemoryRecord(character_id=cid, session_id=root, content="钥匙在塔顶", source_message_ids=[opening["id"]])
    container.memory_store.add(memory)
    sent = mrp_client.post(f"/api/v1/sessions/{root}/messages", json={"content": "我们去灯塔。"})
    assert sent.status_code == 200, sent.text
    parent = mrp_client.get(f"/api/v1/sessions/{root}").json()
    anchor = parent["messages"][-1]
    request = {"message_id": anchor["id"], "title": "前往塔顶", "expected_revision": parent["meta"]["branch_revision"], "idempotency_key": "fork-once"}
    fork = mrp_client.post(f"/api/v1/branches/{root}/fork", json=request)
    assert fork.status_code == 200, fork.text
    child = fork.json()["branch_id"]
    repeat = mrp_client.post(f"/api/v1/branches/{root}/fork", json=request)
    assert repeat.status_code == 200 and repeat.json()["branch_id"] == child and repeat.json()["repeated"]
    assert len(mrp_client.get(f"/api/v1/sessions/{child}").json()["messages"]) == len(parent["messages"])
    assert any(row.id == memory.id or row.inherited_from_id == memory.id for row in container.memory_store.records_for(cid, session_id=child))

    event = mrp_client.post(f"/api/v1/branches/{child}/events", json={
        "anchor_message_id": anchor["id"], "title": "找到钥匙", "visible_to": "all",
    })
    assert event.status_code == 200, event.text
    graph = mrp_client.get(f"/api/v1/stories/{root}/worldline")
    assert graph.status_code == 200 and graph.json()["total_branches"] == 2
    assert any(row["id"] == child and row["events"] for row in graph.json()["branches"])

    save = mrp_client.post(f"/api/v1/sessions/{root}/save?name=塔前")
    assert save.status_code == 200, save.text
    assert container.memory_store.delete_record(memory.id)
    saved_fork = mrp_client.post(f"/api/v1/saves/{save.json()['id']}/fork", json={
        "title": "从存档重来", "idempotency_key": "saved-once",
    })
    assert saved_fork.status_code == 200, saved_fork.text
    assert saved_fork.json()["branch_id"] != child
    assert any(row.content == "钥匙在塔顶" for row in container.memory_store.records_for(cid, session_id=saved_fork.json()["branch_id"]))
    assert len(mrp_client.get("/api/v1/stories").json()) == 1

    exported = mrp_client.get(f"/api/v1/stories/{root}/export")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as zf:
        assert "settings.json" not in zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["format"] == "mrp.story" and len(manifest["branches"]) == 3
    imported = mrp_client.post("/api/v1/stories/import", files={"file": ("story.zip", exported.content, "application/zip")})
    assert imported.status_code == 200, imported.text
    report = imported.json()
    assert report["story_id"] != root and len(report["branches"]) == 3
    imported_graph = mrp_client.get(f"/api/v1/stories/{report['story_id']}/worldline").json()
    assert imported_graph["total_branches"] == 3
    assert sum(len(row["events"]) for row in imported_graph["branches"]) >= 1
    imported_save_id = next(item["new_id"] for item in report["saves"] if item["old_id"] == save.json()["id"])
    recovered = mrp_client.post(f"/api/v1/saves/{imported_save_id}/fork", json={
        "title": "导入存档续线", "idempotency_key": "imported-save",
    })
    assert recovered.status_code == 200, recovered.text
    assert any(row.content == "钥匙在塔顶" for row in container.memory_store.records_for(cid, session_id=recovered.json()["branch_id"]))


def test_private_event_visibility_and_memory_scope(mrp_client: TestClient):
    container = mrp_client.app.state.container
    cid = _character(mrp_client)
    root, state = _story(mrp_client, cid)
    opening = state["messages"][-1]
    private = mrp_client.post(f"/api/v1/sessions/{root}/messages", json={"content": "秘密", "channel": "inner"})
    assert private.status_code == 200, private.text
    private_id = private.json()["messages"][0]["id"]
    overbroad = mrp_client.post(f"/api/v1/branches/{root}/events", json={
        "anchor_message_id": private_id, "title": "秘密", "visible_to": "all",
    })
    assert overbroad.status_code == 422
    valid = mrp_client.post(f"/api/v1/branches/{root}/events", json={
        "anchor_message_id": private_id, "title": "秘密", "visible_to": ["player"],
    })
    assert valid.status_code == 200

    parent = mrp_client.get(f"/api/v1/sessions/{root}").json()
    child = mrp_client.post(f"/api/v1/branches/{root}/fork", json={
        "message_id": opening["id"], "title": "另一条线",
        "expected_revision": parent["meta"]["branch_revision"], "idempotency_key": "other",
    }).json()["branch_id"]
    container.memory_store.add(MemoryRecord(character_id=cid, session_id=child, content="兄弟路线独有", source_message_ids=[opening["id"]]))
    assert all("兄弟路线独有" not in row.content for row in container.memory_store.records_for(cid, session_id=root))
    assert all("兄弟路线独有" not in row.content for row, _ in container.memory_store.search(cid, "兄弟路线独有", session_id=root))


def test_story_archive_redacts_credentials_and_marks_missing_memory(mrp_client: TestClient):
    cid = _character(mrp_client)
    root, state = _story(mrp_client, cid)
    opening = state["messages"][-1]
    token = "sk-or-v1-" + "x" * 40
    changed = mrp_client.patch(f"/api/v1/sessions/{root}/messages/{opening['id']}", json={"content": token})
    assert changed.status_code == 200, changed.text
    exported = mrp_client.get(f"/api/v1/stories/{root}/export?include_memory=false")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert token.encode() not in archive.read(f"branches/{root}.json")
        assert json.loads(archive.read("manifest.json"))["credential_fields_redacted"] > 0
        assert "memories.json" not in archive.namelist()
    imported = mrp_client.post("/api/v1/stories/import", files={"file": ("no-memory.zip", exported.content, "application/zip")})
    assert imported.status_code == 200, imported.text
    new_root = imported.json()["story_id"]
    preview = mrp_client.get(f"/api/v1/branches/{new_root}/messages/{opening['id']}/point")
    assert preview.status_code == 200 and preview.json()["forkable"]
    assert not preview.json()["history_complete"] and preview.json()["history_warning"]
    container = mrp_client.app.state.container
    container.memory_store.add(MemoryRecord(character_id=cid, session_id=new_root,
        content="synthetic current memory must not stand in for lost history",
        source_message_ids=[opening['id']]))
    fork = mrp_client.post(f"/api/v1/branches/{new_root}/fork", json={
        "message_id": opening['id'], "title": "degraded history", "idempotency_key": "degraded-memory",
        "expected_revision": preview.json()['branch_revision'],
    })
    assert fork.status_code == 200, fork.text
    assert fork.json()['warnings']
    assert container.memory_store.records_for(cid, session_id=fork.json()['branch_id']) == []


def test_many_branches_use_summary_paging_and_global_event_search(mrp_client: TestClient):
    cid = _character(mrp_client)
    root, state = _story(mrp_client, cid)
    anchor_id = state["messages"][-1]["id"]
    revision = state["meta"]["branch_revision"]
    last_child = ""
    for n in range(45):
        response = mrp_client.post(f"/api/v1/branches/{root}/fork", json={
            "message_id": anchor_id, "title": f"路线 {n:02d}",
            "expected_revision": revision, "idempotency_key": f"scale-{n}",
        })
        assert response.status_code == 200, response.text
        last_child = response.json()["branch_id"]
    marked = mrp_client.post(f"/api/v1/branches/{last_child}/events", json={
        "anchor_message_id": anchor_id, "title": "只有末页能找到的灯塔钥匙",
    })
    assert marked.status_code == 200, marked.text
    first = mrp_client.get(f"/api/v1/stories/{root}/worldline?limit=10").json()
    assert first["total_branches"] == 46 and len(first["branches"]) == 10
    assert first["next_cursor"] == "10"
    assert all("messages" not in row for row in first["branches"])
    found = mrp_client.get(f"/api/v1/stories/{root}/worldline?limit=10&query=灯塔钥匙").json()
    assert found["total_branches"] == 46 and found["matched_branches"] == 1
    assert found["branches"][0]["id"] == last_child
    assert found["next_cursor"] is None

    # 摘要索引可丢弃：下一次读取从会话文件重建，故事关系仍完整。
    index = mrp_client.app.state.container.paths.sessions_dir / "index.json"
    assert index.exists()
    index.unlink()
    rebuilt = mrp_client.get(f"/api/v1/stories/{root}/worldline?limit=10").json()
    assert rebuilt["total_branches"] == 46 and index.exists()


def test_legacy_head_is_offered_but_earlier_history_is_protected(mrp_client: TestClient):
    mrp_client.app.state.container.sessions.sqlite_new_stories = False
    cid = _character(mrp_client)
    root, state = _story(mrp_client, cid)
    container = mrp_client.app.state.container
    from mrp.storage.atomic import write_json_atomic
    write_json_atomic(container.sessions.path_for(root), state)
    with container.sessions.story_db.transaction() as connection:
        connection.execute('UPDATE branches SET active=0 WHERE id=?', (root,))
    opening_id = state["messages"][-1]["id"]
    inner = mrp_client.post(f"/api/v1/sessions/{root}/messages", json={
        "content": "一个旧版末端事实", "channel": "inner",
    })
    assert inner.status_code == 200, inner.text
    head_id = inner.json()["messages"][-1]["id"]
    path = mrp_client.app.state.container.sessions.path_for(root)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 2
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    # The session cache is bounded. Opening four others evicts this test root,
    # so the subsequent command exercises the v2->v3 persisted migration.
    for _ in range(4):
        _story(mrp_client, cid)
    older = mrp_client.get(f"/api/v1/branches/{root}/messages/{opening_id}/point")
    head = mrp_client.get(f"/api/v1/branches/{root}/messages/{head_id}/point")
    assert older.status_code == 200 and not older.json()["forkable"]
    assert head.status_code == 200 and head.json()["forkable"]
    revision = mrp_client.get(f"/api/v1/sessions/{root}").json()["meta"]["branch_revision"]
    fork = mrp_client.post(f"/api/v1/branches/{root}/fork", json={
        "message_id": head_id, "title": "旧版末端续线",
        "expected_revision": revision, "idempotency_key": "legacy-head",
    })
    assert fork.status_code == 200, fork.text
