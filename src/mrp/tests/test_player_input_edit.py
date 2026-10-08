from __future__ import annotations

import json

from fastapi.testclient import TestClient

from mrp.server.app import create_app
from mrp.tests.conftest import make_test_container, _teardown_sync


def _import_character(client: TestClient) -> str:
    response = client.post(
        "/api/v1/characters/import",
        files={"file": ("editor.json", json.dumps({"name": "编辑测试角色"}, ensure_ascii=False).encode("utf-8"), "application/json")},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _create_session(client: TestClient, character_id: str) -> str:
    response = client.post(
        "/api/v1/sessions",
        json={"title": "\u5206\u6bb5\u7f16\u8f91\u6d4b\u8bd5", "character_ids": [character_id]},
    )
    assert response.status_code == 200, response.text
    return response.json()["meta"]["id"]


def test_mixed_channel_submission_is_one_atomic_edit_group(tmp_path):
    container = make_test_container(tmp_path)
    try:
        with TestClient(create_app(container)) as client:
            session_id = _create_session(client, _import_character(client))
            sent = client.post(
                f"/api/v1/sessions/{session_id}/messages",
                json={
                    "content": "\u4f60\u597d\uff08\u5185\u5fc3\uff1a\u6709\u70b9\u7d27\u5f20\u3002\uff09\uff08\u65c1\u767d\uff1a\u7a97\u5916\u96e8\u58f0\u7a81\u7136\u53d8\u5927\u3002\uff09\u4f60\u542c\u89c1\u4e86\u5417\uff1f"
                },
            )
            assert sent.status_code == 200, sent.text
            player_parts = [m for m in sent.json()["messages"] if m["actor"] == "player"]
            assert [m["kind"] for m in player_parts] == ["roleplay", "inner", "scene", "roleplay"]
            assert len({m["input_group_id"] for m in player_parts}) == 1
            assert player_parts[1]["visible_to"] == ["player"]
            assert player_parts[0]["visible_to"] == "all"

            snapshot = client.get(f"/api/v1/sessions/{session_id}").json()
            revision = snapshot["meta"]["branch_revision"]
            edits = [
                {
                    "message_id": msg["id"],
                    "expected_fingerprint": msg["fingerprint"],
                    "content": f"\u7f16\u8f91{index}",
                }
                for index, msg in enumerate(player_parts)
            ]
            saved = client.patch(
                f"/api/v1/sessions/{session_id}/messages/{player_parts[1]['id']}/input-group",
                json={"expected_branch_revision": revision, "parts": edits},
            )
            assert saved.status_code == 200, saved.text
            assert [m["content"] for m in saved.json()["messages"]] == ["\u7f16\u8f91" + str(i) for i in range(4)]
            assert [m["kind"] for m in saved.json()["messages"]] == ["roleplay", "inner", "scene", "roleplay"]
            assert saved.json()["messages"][1]["visible_to"] == ["player"]

            after = client.get(f"/api/v1/sessions/{session_id}").json()
            by_id = {m["id"]: m for m in after["messages"]}
            assert all(by_id[msg["id"]]["content"] == f"\u7f16\u8f91{index}" for index, msg in enumerate(player_parts))
            assert after["meta"]["branch_revision"] == saved.json()["branch_revision"]

            stale = client.patch(
                f"/api/v1/sessions/{session_id}/messages/{player_parts[0]['id']}/input-group",
                json={"expected_branch_revision": revision, "parts": edits},
            )
            assert stale.status_code == 409
            assert [by_id[msg["id"]]["content"] for msg in player_parts] == [f"\u7f16\u8f91{i}" for i in range(4)]
    finally:
        _teardown_sync(container)


def test_group_edit_rejects_invalid_segment_without_partial_update(tmp_path):
    container = make_test_container(tmp_path)
    try:
        with TestClient(create_app(container)) as client:
            session_id = _create_session(client, _import_character(client))
            sent = client.post(
                f"/api/v1/sessions/{session_id}/messages",
                json={"content": "\u8bf4\u8bdd\uff08\u5185\u5fc3\uff1a\u4fdd\u7559\u7684\u60f3\u6cd5\u3002\uff09"},
            )
            player_parts = [m for m in sent.json()["messages"] if m["actor"] == "player"]
            snapshot = client.get(f"/api/v1/sessions/{session_id}").json()
            invalid_parts = [
                {"message_id": m["id"], "expected_fingerprint": m["fingerprint"],
                 "content": "" if m["kind"] == "inner" else "\u4fee\u6539\u540e\u7684\u6b63\u6587"}
                for m in player_parts
            ]
            response = client.patch(
                f"/api/v1/sessions/{session_id}/messages/{player_parts[0]['id']}/input-group",
                json={"expected_branch_revision": snapshot["meta"]["branch_revision"], "parts": invalid_parts},
            )
            assert response.status_code == 400
            after = client.get(f"/api/v1/sessions/{session_id}").json()
            after_by_id = {m["id"]: m for m in after["messages"]}
            assert [after_by_id[m["id"]]["content"] for m in player_parts] == [m["content"] for m in player_parts]
    finally:
        _teardown_sync(container)


async def test_legacy_input_group_falls_back_to_adjacent_player_parts():
    from mrp.tests.test_integration import make_runner

    runner, _ = make_runner(replies=["\u4f60\u542c\u89c1\u4e86\u3002"])
    created = await runner.player_say(
        "\u4f60\u597d\uff08\u5185\u5fc3\uff1a\u6709\u70b9\u7d27\u5f20\u3002\uff09\uff08\u65c1\u767d\uff1a\u5916\u9762\u4e0b\u8d77\u96e8\u3002\uff09\u7ee7\u7eed\u8bf4\u8bdd\u3002"
    )
    parts = [message for message in created if message.actor == "player"]
    for message in parts:
        message.input_group_id = None  # simulate an old save without the additive field

    async with runner.runtime.turn_lock:
        edited = await runner.message_ops.edit_input_group_locked(
            parts[2].id,
            [(message.id, message.fingerprint, f"\u6bb5\u843d{i}") for i, message in enumerate(parts)],
            expected_branch_revision=runner.state.meta.branch_revision,
        )
    assert [message.id for message in edited] == [message.id for message in parts]
    assert [message.content for message in edited] == [f"\u6bb5\u843d{i}" for i in range(4)]
    assert [message.kind for message in edited] == ["roleplay", "inner", "scene", "roleplay"]
    assert edited[1].visible_to == ["player"]
