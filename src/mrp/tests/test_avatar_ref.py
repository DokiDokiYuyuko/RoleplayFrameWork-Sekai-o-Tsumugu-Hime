"""头像文件是导出和加载的依据。不读用户的数据目录。"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from PIL import Image

from mrp.storage import AppPaths, CharacterRegistry


def _dump(body: dict) -> str:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def _write_character(directory: Path, body: dict, *, suffix: str = "") -> Path:
    path = directory / f"{body['id']}.json"
    path.write_text(_dump(body) + suffix, encoding="utf-8")
    return path


def _card(character_id: str, avatar_path) -> dict:
    return {
        "id": character_id,
        "revision": 4,
        "updated_at": "2026-01-02T03:04:05Z",
        "created_at": "2025-12-01T00:00:00Z",
        "custom_marker": "stay",
        "card": {
            "name": "冰羽",
            "extra_note": "keep",
            "description": "原文",
            "avatar_path": avatar_path,
        },
    }


def _touch_avatar(characters_dir: Path, character_id: str) -> None:
    dest = characters_dir / character_id / "avatar.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"\x89PNG\r\n")


def _same_except_avatar(before: dict, after: dict, avatar_path: str) -> None:
    assert list(before) == list(after)
    assert list(before["card"]) == list(after["card"])
    assert after["revision"] == before["revision"]
    assert after["updated_at"] == before["updated_at"]
    assert after["custom_marker"] == "stay"
    assert after["card"]["extra_note"] == "keep"
    assert after["card"]["description"] == "原文"
    assert after["card"]["avatar_path"] == avatar_path


def test_load_records_canonical_avatar_without_touching_revision_or_rewriting_again(tmp_path: Path):
    paths = AppPaths(tmp_path / "data").ensure()
    body = _card("char-keep", None)
    path = _write_character(paths.characters_dir, body)
    _touch_avatar(paths.characters_dir, body["id"])
    registry = CharacterRegistry(paths)

    loaded = registry.load_all_sync()
    assert [item.id for item in loaded] == [body["id"]]
    assert loaded[0].revision == 4
    assert loaded[0].card.avatar_path == "char-keep/avatar.png"
    assert loaded[0].card.model_extra["extra_note"] == "keep"
    repaired = json.loads(path.read_text(encoding="utf-8"))
    _same_except_avatar(body, repaired, "char-keep/avatar.png")

    stamped = path.stat().st_mtime_ns
    text = path.read_text(encoding="utf-8")
    again = registry.load_all_sync()
    assert again[0].card.avatar_path == "char-keep/avatar.png"
    assert again[0].revision == 4
    assert path.read_text(encoding="utf-8") == text
    assert path.stat().st_mtime_ns == stamped


def test_reload_one_records_the_same_way(tmp_path: Path):
    paths = AppPaths(tmp_path / "data").ensure()
    body = _card("char-reload", "legacy/face.png")
    path = _write_character(paths.characters_dir, body)
    _touch_avatar(paths.characters_dir, body["id"])
    registry = CharacterRegistry(paths)

    item = registry.reload_one_sync(body["id"])
    assert item is not None
    assert item.revision == 4
    assert item.card.avatar_path == "char-reload/avatar.png"
    repaired = json.loads(path.read_text(encoding="utf-8"))
    _same_except_avatar(body, repaired, "char-reload/avatar.png")
    stamped = path.stat().st_mtime_ns
    registry.reload_one_sync(body["id"])
    assert path.stat().st_mtime_ns == stamped


def test_load_without_avatar_file_leaves_the_record_untouched(tmp_path: Path):
    paths = AppPaths(tmp_path / "data").ensure()
    body = _card("char-plain", None)
    path = _write_character(paths.characters_dir, body, suffix="\n")
    original = path.read_bytes()
    stamped = path.stat().st_mtime_ns

    loaded = CharacterRegistry(paths).load_all_sync()
    assert loaded[0].card.avatar_path is None
    assert loaded[0].revision == 4
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == stamped


def test_load_keeps_a_dangling_avatar_path(tmp_path: Path):
    paths = AppPaths(tmp_path / "data").ensure()
    body = _card("char-dangling", "notes/portrait.png")
    path = _write_character(paths.characters_dir, body, suffix="\n")
    original = path.read_bytes()
    stamped = path.stat().st_mtime_ns

    loaded = CharacterRegistry(paths).load_all_sync()
    assert loaded[0].card.avatar_path == "notes/portrait.png"
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == stamped


def _import_character(client):
    response = client.post(
        "/api/v1/characters/import",
        files={"file": ("card.json", b'{"name":"Synthetic guide","description":"Original"}', "application/json")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _png(color, size=(8, 8)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _put_avatar(client, character_id: str, data: bytes) -> None:
    container = client.app.state.container
    dest = container.paths.characters_dir / character_id / "avatar.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def test_png_export_embeds_the_file_when_avatar_path_is_null(mrp_client):
    card = _import_character(mrp_client)
    portrait = _png((220, 20, 60))
    _put_avatar(mrp_client, card["id"], portrait)
    stored = mrp_client.app.state.container.characters[card["id"]]
    assert stored.card.avatar_path is None

    response = mrp_client.get(f"/api/v1/characters/{card['id']}/export", params={"format": "png"})
    assert response.status_code == 200, response.text
    image = Image.open(io.BytesIO(response.content))
    assert image.size == (8, 8)
    assert image.getpixel((0, 0))[:3] == (220, 20, 60)
    assert stored.card.avatar_path is None


def test_png_export_without_a_file_stays_a_plain_card(mrp_client):
    card = _import_character(mrp_client)
    response = mrp_client.get(f"/api/v1/characters/{card['id']}/export", params={"format": "png"})
    image = Image.open(io.BytesIO(response.content))
    assert image.size == (512, 512)
    assert image.getpixel((0, 0))[:3] == (99, 102, 241)


def test_bundle_exports_include_the_file_when_avatar_path_is_null(mrp_client):
    card = _import_character(mrp_client)
    portrait = _png((20, 180, 90), size=(6, 6))
    _put_avatar(mrp_client, card["id"], portrait)
    container = mrp_client.app.state.container
    assert container.characters[card["id"]].card.avatar_path is None
    request = {"characters": [{"id": card["id"], "expected_revision": card["revision"]}]}

    preview = mrp_client.post("/api/v1/bundle/export-selected/preview", json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()["avatars"] == 1

    selected = mrp_client.post("/api/v1/bundle/export-selected", json=request)
    assert selected.status_code == 200, selected.text
    with zipfile.ZipFile(io.BytesIO(selected.content)) as archive:
        assert archive.read(f"characters/{card['id']}/avatar.png") == portrait

    library = mrp_client.get("/api/v1/bundle/export")
    assert library.status_code == 200, library.text
    with zipfile.ZipFile(io.BytesIO(library.content)) as archive:
        assert archive.read(f"characters/{card['id']}/avatar.png") == portrait

    on_disk = json.loads((container.paths.characters_dir / f"{card['id']}.json").read_text(encoding="utf-8"))
    assert on_disk["card"]["avatar_path"] is None


def test_canonical_file_is_exported_even_if_the_recorded_path_escapes(mrp_client):
    card = _import_character(mrp_client)
    portrait = _png((10, 20, 30))
    _put_avatar(mrp_client, card["id"], portrait)
    container = mrp_client.app.state.container
    container.characters[card["id"]].card.avatar_path = "../secret.png"
    secret = container.paths.data_root / "secret.png"
    secret.write_bytes(b"SECRET-BYTES")
    request = {"characters": [{"id": card["id"], "expected_revision": card["revision"]}]}

    selected = mrp_client.post("/api/v1/bundle/export-selected", json=request)
    assert selected.status_code == 200, selected.text
    assert b"SECRET-BYTES" not in selected.content
    with zipfile.ZipFile(io.BytesIO(selected.content)) as archive:
        assert archive.read(f"characters/{card['id']}/avatar.png") == portrait


def test_dangling_recorded_path_is_left_out_of_the_bundle(mrp_client):
    card = _import_character(mrp_client)
    mrp_client.app.state.container.characters[card["id"]].card.avatar_path = "notes/portrait.png"
    request = {"characters": [{"id": card["id"], "expected_revision": card["revision"]}]}

    preview = mrp_client.post("/api/v1/bundle/export-selected/preview", json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()["avatars"] == 0
    selected = mrp_client.post("/api/v1/bundle/export-selected", json=request)
    assert selected.status_code == 200, selected.text
    with zipfile.ZipFile(io.BytesIO(selected.content)) as archive:
        assert f"characters/{card['id']}/avatar.png" not in archive.namelist()


def test_escaping_avatar_path_is_rejected_when_no_canonical_file_exists(mrp_client):
    card = _import_character(mrp_client)
    container = mrp_client.app.state.container
    container.characters[card["id"]].card.avatar_path = "../secret.png"
    secret = container.paths.data_root / "secret.png"
    secret.write_bytes(b"SECRET-BYTES")
    request = {"characters": [{"id": card["id"], "expected_revision": card["revision"]}]}

    selected = mrp_client.post("/api/v1/bundle/export-selected", json=request)
    assert selected.status_code == 409
    library = mrp_client.get("/api/v1/bundle/export")
    assert library.status_code == 200
    assert b"SECRET-BYTES" not in library.content
    png = mrp_client.get(f"/api/v1/characters/{card['id']}/export", params={"format": "png"})
    image = Image.open(io.BytesIO(png.content))
    assert image.size == (512, 512)


def test_next_load_records_the_path_without_changing_revision(mrp_client):
    card = _import_character(mrp_client)
    portrait = _png((1, 2, 3))
    _put_avatar(mrp_client, card["id"], portrait)
    container = mrp_client.app.state.container
    disk = container.paths.characters_dir / f"{card['id']}.json"
    before = json.loads(disk.read_text(encoding="utf-8"))
    assert before["card"]["avatar_path"] is None

    container.character_registry.load_all_sync()
    loaded = container.characters[card["id"]]
    assert loaded.card.avatar_path == f"{card['id']}/avatar.png"
    assert loaded.revision == card["revision"]
    assert loaded.updated_at.isoformat().replace("+00:00", "Z") == card["updated_at"].replace("+00:00", "Z")
    after = json.loads(disk.read_text(encoding="utf-8"))
    assert list(before) == list(after)
    assert list(before["card"]) == list(after["card"])
    assert after["revision"] == before["revision"]
    assert after["updated_at"] == before["updated_at"]
    assert after["card"]["avatar_path"] == f"{card['id']}/avatar.png"

    stamped = disk.stat().st_mtime_ns
    text = disk.read_text(encoding="utf-8")
    container.character_registry.load_all_sync()
    assert disk.read_text(encoding="utf-8") == text
    assert disk.stat().st_mtime_ns == stamped
