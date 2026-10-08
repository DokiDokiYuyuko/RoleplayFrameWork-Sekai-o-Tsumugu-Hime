"""Cover persistence and safe media lookup using only synthetic assets."""
import json

from mrp.tests.test_world_runtime_policy import character


def catalog(client):
    root = client.app.state.container.data_root / "world_cover_assets"
    root.mkdir()
    (root / "fixture.webp").write_bytes(b"synthetic-image-only")
    rows = [{"id": "synthetic-cover", "title": "Synthetic", "theme": "fantasy",
             "theme_label": "Fantasy", "filename": "fixture.webp", "prompt": "private metadata"}]
    (root / "catalog.json").write_text(json.dumps(rows), encoding="utf-8")
    return root, rows


def test_catalog_does_not_expose_paths_or_prompts_and_rejects_escape(mrp_client):
    root, rows = catalog(mrp_client)
    (root.parent / "private.webp").write_bytes(b"private")
    rows.append({**rows[0], "id": "escape", "filename": "../private.webp"})
    (root / "catalog.json").write_text(json.dumps(rows), encoding="utf-8")
    response = mrp_client.get("/api/v1/world-covers")
    assert response.status_code == 200
    assert [row for row in response.json() if not row["id"].startswith("moonweave-")] == [{"id": "synthetic-cover", "title": "Synthetic", "theme": "fantasy",
                               "theme_label": "Fantasy", "image_url": "/api/v1/world-covers/synthetic-cover/image"}]
    assert all(set(row) == {"id", "title", "theme", "theme_label", "image_url"} for row in response.json())
    image = mrp_client.get(response.json()[0]["image_url"])
    assert image.status_code == 200 and image.content == b"synthetic-image-only"
    assert image.headers["content-type"] == "image/webp"
    assert mrp_client.get("/api/v1/world-covers/escape/image").status_code == 404
    assert mrp_client.get("/api/v1/world-covers/unknown/image").status_code == 404


def test_cover_survives_save_reload_and_can_be_removed_without_losing_prose(mrp_client):
    catalog(mrp_client)
    world = mrp_client.post("/api/v1/worlds", json={"title": "Synthetic world",
        "cover_id": "synthetic-cover", "manuscript_body": "Immutable synthetic prose"}).json()
    assert world["cover_id"] == "synthetic-cover"
    c = mrp_client.app.state.container
    c.world_registry.load_all_sync()
    path = "/api/v1/worlds/" + world["id"]
    assert mrp_client.get(path).json()["cover_id"] == "synthetic-cover"
    assert mrp_client.get("/api/v1/worlds").json()[0]["cover_id"] == "synthetic-cover"
    updated = mrp_client.patch(path, json={"expected_revision": world["revision"], "description": "changed"}).json()
    assert updated["cover_id"] == "synthetic-cover"
    removed = mrp_client.patch(path, json={"expected_revision": updated["revision"], "cover_id": None}).json()
    assert removed["cover_id"] is None
    assert removed["archive_records"][0]["body"] == "Immutable synthetic prose"
    assert mrp_client.patch(path, json={"expected_revision": world["revision"], "cover_id": "synthetic-cover"}).status_code == 409


def test_unknown_or_path_cover_cannot_be_saved(mrp_client):
    catalog(mrp_client)
    assert mrp_client.post("/api/v1/worlds", json={"title": "Invalid", "cover_id": "unknown"}).status_code == 400
    assert mrp_client.post("/api/v1/worlds", json={"title": "Invalid", "cover_id": "../private"}).status_code == 422
    assert mrp_client.get("/api/v1/worlds").json() == []


def test_story_shelf_uses_its_world_cover_and_legacy_stories_still_work(mrp_client):
    catalog(mrp_client)
    cid = character(mrp_client)
    world = mrp_client.post("/api/v1/worlds", json={"title": "Synthetic world", "cover_id": "synthetic-cover"}).json()
    opened = mrp_client.post("/api/v1/sessions", json={"title": "Covered story", "character_ids": [cid], "world_id": world["id"]})
    assert opened.status_code == 200, opened.text
    legacy = mrp_client.post("/api/v1/sessions", json={"title": "Legacy story", "character_ids": [cid]})
    assert legacy.status_code == 200, legacy.text
    stories = {row["title"]: row for row in mrp_client.get("/api/v1/stories").json()}
    assert stories["Covered story"]["cover_id"] == "synthetic-cover"
    assert stories["Legacy story"]["cover_id"] is None


def test_builtin_cover_is_available_without_private_catalog_and_persists(mrp_client):
    rows = mrp_client.get('/api/v1/world-covers').json()
    key = 'moonweave-modern-coast'
    assert any(row['id'] == key for row in rows)
    image = mrp_client.get(f'/api/v1/world-covers/{key}/image')
    assert image.status_code == 200 and image.headers['content-type'] == 'image/webp'
    created = mrp_client.post('/api/v1/worlds', json={'title': 'Builtin example', 'cover_id': key})
    assert created.status_code == 200
    mrp_client.app.state.container.world_registry.load_all_sync()
    assert mrp_client.get('/api/v1/worlds/' + created.json()['id']).json()['cover_id'] == key
