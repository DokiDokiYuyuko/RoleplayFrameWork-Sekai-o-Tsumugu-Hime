"""Cross-world biology copy: new ids, provenance identity, one revision bump."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from mrp.worlds.biology_import import apply_biology_import
from mrp.worlds.schema import ArchiveRecord, World


def _world(client, title):
    response = client.post("/api/v1/worlds", json={"title": title, "runtime_policy": "raw"})
    assert response.status_code == 200, response.text
    return response.json()


def _biology(title="哥布林", *, visibility="public", body="绿色皮肤。", abilities="夜视"):
    return {
        "kind": "biology",
        "title": title,
        "subtype": "穴居",
        "aliases": ["哥布"],
        "tags": ["魔物"],
        "summary": "成群活动",
        "body": body,
        "visibility": visibility,
        "kind_data": {
            "classification": "race",
            "appearance": "矮小",
            "habitat": "洞穴",
            "culture": "部落",
            "abilities": abilities,
            "limitations": "怕火",
        },
    }


def _add(client, world, record):
    response = client.post(
        f"/api/v1/worlds/{world['id']}/archive",
        json={"expected_revision": world["revision"], "record": record},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _import(client, destination, source_id, items, expected=None):
    return client.post(
        f"/api/v1/worlds/{destination['id']}/archive/import-biology",
        json={
            "expected_revision": destination["revision"] if expected is None else expected,
            "source_world_id": source_id,
            "items": items,
        },
    )


def _by_title(world, title):
    return [row for row in world["archive_records"] if row["title"] == title and row["kind"] == "biology"]


def test_copy_keeps_source_and_writes_one_destination_revision(mrp_client):
    source = _world(mrp_client, "合成源世界")
    source = _add(mrp_client, source, _biology("哥布林"))
    source = _add(mrp_client, source, _biology("史莱姆", visibility="private", body="半透明。"))
    public = _by_title(source, "哥布林")[0]
    private = _by_title(source, "史莱姆")[0]
    before = mrp_client.get(f"/api/v1/worlds/{source['id']}").json()
    destination = _world(mrp_client, "维萨瑞恩")
    response = _import(mrp_client, destination, source["id"], [
        {"id": public["id"], "mode": "copy"},
        {"id": private["id"], "mode": "copy"},
    ])
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["revision"] == destination["revision"] + 1
    assert mrp_client.get(f"/api/v1/worlds/{source['id']}").json() == before
    copied_public = _by_title(saved, "哥布林")[0]
    copied_private = _by_title(saved, "史莱姆")[0]
    assert copied_public["id"] != public["id"] and copied_private["id"] != private["id"]
    assert copied_public["revision"] == 1 and copied_private["revision"] == 1
    assert copied_public["visibility"] == "public" and copied_private["visibility"] == "private"
    assert copied_public["body"] == public["body"]
    assert copied_public["kind_data"] == public["kind_data"]
    assert copied_public["aliases"] == public["aliases"]
    assert copied_public["tags"] == public["tags"]
    assert copied_public["summary"] == public["summary"]
    assert copied_public["subtype"] == public["subtype"]
    assert copied_public["copied_from_world_id"] == source["id"]
    assert copied_public["copied_from_archive_id"] == public["id"]
    assert copied_public["copied_from_revision"] == public["revision"]


def test_second_copy_is_rejected_and_overwrite_touches_only_body_and_kind_data(mrp_client):
    source = _add(mrp_client, _world(mrp_client, "合成源世界"), _biology("哥布林", body="旧正文"))
    origin = _by_title(source, "哥布林")[0]
    destination = _world(mrp_client, "维萨瑞恩")
    first = _import(mrp_client, destination, source["id"], [{"id": origin["id"], "mode": "copy"}])
    assert first.status_code == 200, first.text
    destination = first.json()
    copied = _by_title(destination, "哥布林")[0]
    again = _import(mrp_client, destination, source["id"], [{"id": origin["id"], "mode": "copy"}])
    assert again.status_code == 400 and "已经导入过" in again.text
    assert mrp_client.get(f"/api/v1/worlds/{destination['id']}").json()["revision"] == destination["revision"]

    renamed = mrp_client.patch(
        f"/api/v1/worlds/{source['id']}/archive/{origin['id']}",
        json={"expected_revision": source["revision"], "record": _biology("哥布林改名", body="新正文", abilities="喷酸")},
    )
    assert renamed.status_code == 200, renamed.text
    source = renamed.json()
    updated = _by_title(source, "哥布林改名")[0]
    overwritten = _import(mrp_client, destination, source["id"], [{"id": origin["id"], "mode": "overwrite"}])
    assert overwritten.status_code == 200, overwritten.text
    saved = overwritten.json()
    assert saved["revision"] == destination["revision"] + 1
    row = next(item for item in saved["archive_records"] if item["id"] == copied["id"])
    assert row["title"] == "哥布林" and row["visibility"] == "public"
    assert row["aliases"] == copied["aliases"] and row["summary"] == copied["summary"]
    assert row["subtype"] == copied["subtype"] and row["tags"] == copied["tags"]
    assert row["body"] == "新正文" and row["kind_data"]["abilities"] == "喷酸"
    assert row["revision"] == copied["revision"] + 1
    assert row["copied_from_revision"] == updated["revision"]
    assert row["copied_from_archive_id"] == origin["id"]
    assert row["created_at"] == copied["created_at"]


def test_same_title_without_provenance_is_a_second_record(mrp_client):
    destination = _add(mrp_client, _world(mrp_client, "维萨瑞恩"), _biology("哥布林", body="本地的"))
    local = _by_title(destination, "哥布林")[0]
    source = _add(mrp_client, _world(mrp_client, "合成源世界"), _biology("哥布林", body="外来的"))
    origin = _by_title(source, "哥布林")[0]
    response = _import(mrp_client, destination, source["id"], [{"id": origin["id"], "mode": "copy"}])
    assert response.status_code == 200, response.text
    rows = _by_title(response.json(), "哥布林")
    assert len(rows) == 2
    kept = next(row for row in rows if row["id"] == local["id"])
    fresh = next(row for row in rows if row["id"] != local["id"])
    assert kept["copied_from_world_id"] is None and kept["body"] == "本地的"
    assert fresh["body"] == "外来的" and fresh["copied_from_archive_id"] == origin["id"]


def test_archive_patch_keeps_provenance_when_the_editor_omits_it(mrp_client):
    source = _add(mrp_client, _world(mrp_client, "合成源世界"), _biology())
    origin = _by_title(source, "哥布林")[0]
    destination = _world(mrp_client, "维萨瑞恩")
    imported = _import(mrp_client, destination, source["id"], [{"id": origin["id"], "mode": "copy"}]).json()
    copied = _by_title(imported, "哥布林")[0]
    payload = _biology("洞穴哥布林")
    payload["copied_from_world_id"] = None
    payload["copied_from_archive_id"] = None
    payload["copied_from_revision"] = None
    response = mrp_client.patch(
        f"/api/v1/worlds/{imported['id']}/archive/{copied['id']}",
        json={"expected_revision": imported["revision"], "record": payload},
    )
    assert response.status_code == 200, response.text
    row = _by_title(response.json(), "洞穴哥布林")[0]
    assert row["id"] == copied["id"] and row["revision"] == copied["revision"] + 1
    assert row["copied_from_world_id"] == source["id"]
    assert row["copied_from_archive_id"] == origin["id"]
    assert row["copied_from_revision"] == origin["revision"]


def test_rejected_import_writes_nothing(mrp_client):
    source = _add(mrp_client, _world(mrp_client, "合成源世界"), _biology())
    source = _add(mrp_client, source, {
        "kind": "background", "title": "运河", "body": "退潮开启",
        "visibility": "public", "kind_data": {"section": "geography"},
    })
    biology = _by_title(source, "哥布林")[0]
    background = next(row for row in source["archive_records"] if row["kind"] == "background")
    destination = _world(mrp_client, "维萨瑞恩")
    missing = _import(mrp_client, destination, source["id"], [
        {"id": biology["id"], "mode": "copy"},
        {"id": "archive-missing", "mode": "copy"},
    ])
    assert missing.status_code == 400 and "不存在或已删除" in missing.text
    background_row = _import(mrp_client, destination, source["id"], [{"id": background["id"], "mode": "copy"}])
    assert background_row.status_code == 400 and "不存在或已删除" in background_row.text
    same = _import(mrp_client, source, source["id"], [{"id": biology["id"], "mode": "copy"}])
    assert same.status_code == 400 and "不能从世界自身导入" in same.text
    stale = _import(mrp_client, destination, source["id"], [{"id": biology["id"], "mode": "copy"}], expected=99)
    assert stale.status_code == 409
    empty = mrp_client.post(
        f"/api/v1/worlds/{destination['id']}/archive/import-biology",
        json={"expected_revision": destination["revision"], "source_world_id": source["id"], "items": []},
    )
    assert empty.status_code == 422
    duplicate = _import(mrp_client, destination, source["id"], [
        {"id": biology["id"], "mode": "copy"},
        {"id": biology["id"], "mode": "overwrite"},
    ])
    assert duplicate.status_code == 400 and "重复" in duplicate.text
    absent = _import(mrp_client, destination, "world-missing", [{"id": biology["id"], "mode": "copy"}])
    assert absent.status_code == 404
    archived = mrp_client.patch(
        f"/api/v1/worlds/{destination['id']}",
        json={"expected_revision": destination["revision"], "archived": True},
    )
    assert archived.status_code == 200, archived.text
    refused = _import(mrp_client, archived.json(), source["id"], [{"id": biology["id"], "mode": "copy"}])
    assert refused.status_code == 400 and "归档世界不能修改" in refused.text
    fresh = _world(mrp_client, "另一处")
    source_archived = mrp_client.patch(
        f"/api/v1/worlds/{source['id']}",
        json={"expected_revision": source["revision"], "archived": True},
    )
    assert source_archived.status_code == 200, source_archived.text
    readable = _import(mrp_client, fresh, source["id"], [{"id": biology["id"], "mode": "copy"}])
    assert readable.status_code == 200, readable.text
    untouched = mrp_client.get(f"/api/v1/worlds/{destination['id']}").json()
    assert untouched["revision"] == archived.json()["revision"]
    assert untouched["archive_records"] == archived.json()["archive_records"]


def test_public_copy_is_visible_to_group_sources_and_a_story_without_a_world_stays_empty(mrp_client):
    source = _add(mrp_client, _world(mrp_client, "合成源世界"), _biology("哥布林"))
    source = _add(mrp_client, source, _biology("史莱姆", visibility="private"))
    public = _by_title(source, "哥布林")[0]
    private = _by_title(source, "史莱姆")[0]
    destination = _world(mrp_client, "维萨瑞恩")
    imported = _import(mrp_client, destination, source["id"], [
        {"id": public["id"], "mode": "copy"},
        {"id": private["id"], "mode": "copy"},
    ]).json()
    copied = _by_title(imported, "哥布林")[0]
    character = mrp_client.post("/api/v1/characters/import", files={"file": (
        "fiction.json",
        json.dumps({"name": "合成守卫", "description": "资料", "first_mes": "迎接"}).encode(),
        "application/json",
    )})
    assert character.status_code == 200, character.text
    opened = mrp_client.post("/api/v1/sessions", json={
        "title": "有世界", "character_ids": [character.json()["id"]], "world_id": destination["id"],
    })
    assert opened.status_code == 200, opened.text
    sources = mrp_client.get(f"/api/v1/sessions/{opened.json()['meta']['id']}/groups/sources")
    assert sources.status_code == 200, sources.text
    listed = sources.json()["sources"]
    assert [row["id"] for row in listed] == [copied["id"]]
    assert listed[0]["title"] == "哥布林"
    plain = mrp_client.post("/api/v1/sessions", json={
        "title": "无世界", "character_ids": [character.json()["id"]],
    })
    assert plain.status_code == 200, plain.text
    empty = mrp_client.get(f"/api/v1/sessions/{plain.json()['meta']['id']}/groups/sources")
    assert empty.status_code == 200, empty.text
    assert empty.json()["world_id"] is None and empty.json()["sources"] == []


def test_provenance_must_be_complete():
    with pytest.raises(ValidationError, match="导入来源记录不完整"):
        ArchiveRecord(kind="biology", title="哥布林", copied_from_world_id="world-1")
    with pytest.raises(ValidationError, match="导入来源修订号无效"):
        ArchiveRecord(
            kind="biology", title="哥布林",
            copied_from_world_id="world-1", copied_from_archive_id="archive-1", copied_from_revision=0,
        )
    blank = ArchiveRecord(kind="biology", title="哥布林", copied_from_world_id="  ")
    assert blank.copied_from_world_id is None


def test_import_checks_the_whole_batch_before_writing():
    source_record = ArchiveRecord(kind="biology", title="史莱姆", body="旧", visibility="public",
                                  kind_data={"classification": "creature", "abilities": "分裂"})
    other = ArchiveRecord(kind="biology", title="食尸鬼", body="外来", visibility="private",
                          kind_data={"classification": "creature"})
    source = World(title="合成源世界", archive_records=[source_record, other])
    destination = World(title="维萨瑞恩", archive_records=[
        ArchiveRecord(kind="background", title=f"背景{index}", body="x") for index in range(1999)
    ] + [
        ArchiveRecord(
            kind="biology", title="已导入的史莱姆", body="旧副本", visibility="public",
            kind_data={"classification": "creature"},
            copied_from_world_id=source.id, copied_from_archive_id=source_record.id, copied_from_revision=1,
        ),
    ])
    before = destination.model_dump(mode="json")
    source_before = source.model_dump(mode="json")
    with pytest.raises(ValueError, match="2000"):
        apply_biology_import(destination, source, [(source_record.id, "overwrite"), (other.id, "copy")])
    assert destination.model_dump(mode="json") == before
    assert source.model_dump(mode="json") == source_before

    apply_biology_import(destination, source, [(source_record.id, "overwrite")])
    kept = destination.archive_records[-1]
    assert len(destination.archive_records) == 2000
    assert kept.body == "旧" and kept.title == "已导入的史莱姆"
    assert kept.kind_data["abilities"] == "分裂"
    assert kept.copied_from_revision == source_record.revision
    assert source.model_dump(mode="json") == source_before

    duplicate = World(title="重复", archive_records=[
        ArchiveRecord(kind="biology", title="甲", copied_from_world_id=source.id,
                      copied_from_archive_id=source_record.id, copied_from_revision=1),
        ArchiveRecord(kind="biology", title="乙", copied_from_world_id=source.id,
                      copied_from_archive_id=source_record.id, copied_from_revision=1),
    ])
    with pytest.raises(ValueError, match="多份副本"):
        apply_biology_import(duplicate, source, [(source_record.id, "overwrite")])
    assert [row.title for row in duplicate.archive_records] == ["甲", "乙"]

    fresh = World(title="空")
    shared = other.kind_data
    apply_biology_import(fresh, source, [(other.id, "copy")])
    fresh.archive_records[0].kind_data["abilities"] = "改掉"
    assert other.kind_data is shared and fresh.archive_records[0].kind_data is not shared
    assert "改掉" not in other.kind_data.values()
    assert fresh.archive_records[0].visibility == "private"
