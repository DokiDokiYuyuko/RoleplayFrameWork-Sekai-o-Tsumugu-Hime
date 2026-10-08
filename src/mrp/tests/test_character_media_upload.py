from io import BytesIO

from PIL import Image
import pytest


def picture(format="PNG"):
    output = BytesIO()
    Image.new("RGB", (32, 64), "navy").save(output, format=format)
    return output.getvalue()


def create(client):
    response = client.post("/api/v1/characters/import", files={"file": ("card.json", b'{"name":"Synthetic guide","description":"Original"}', "application/json")})
    assert response.status_code == 200
    return response.json()


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_avatar_upload_then_save_uses_new_revision_and_preserves_image(mrp_client, format):
    card = create(mrp_client)
    base = f'/api/v1/characters/{card["id"]}'
    uploaded = mrp_client.post(base + "/avatar", data={"expected_revision": str(card["revision"])}, files={"file": ("image", picture(format), "application/octet-stream")})
    assert uploaded.status_code == 200
    assert uploaded.json()["revision"] == card["revision"] + 1
    saved = mrp_client.patch(base, json={"expected_revision": uploaded.json()["revision"], "card": {"description": "Edited draft"}})
    assert saved.status_code == 200
    assert saved.json()["card"]["description"] == "Edited draft"
    assert Image.open(BytesIO(mrp_client.get(base + "/avatar").content)).format == "PNG"
    assert mrp_client.patch(base, json={"expected_revision": card["revision"], "card": {"description": "Stale"}}).status_code == 409


def test_full_body_is_independent_of_avatar_and_card_revision(mrp_client):
    card = create(mrp_client)
    base = f'/api/v1/characters/{card["id"]}'
    result = mrp_client.post(base + "/full-body", data={"expected_revision": str(card["revision"])}, files={"file": ("portrait.jpg", picture("JPEG"), "image/jpeg")})
    assert result.status_code == 200
    assert result.json()["revision"] == card["revision"]
    assert mrp_client.get(base + "/avatar").status_code == 404
    image = Image.open(BytesIO(mrp_client.get(base + "/full-body").content))
    assert image.size == (32, 64) and image.format == "PNG"
    assert mrp_client.get(base).json()["card"]["description"] == "Original"


def test_invalid_image_and_stale_media_upload_do_not_replace_existing(mrp_client):
    card = create(mrp_client)
    base = f'/api/v1/characters/{card["id"]}'
    assert mrp_client.post(base + "/avatar", files={"file": ("ok.png", picture(), "image/png")}).status_code == 200
    before = mrp_client.get(base + "/avatar").content
    assert mrp_client.post(base + "/avatar", files={"file": ("bad.png", b"not an image", "image/png")}).status_code == 400
    assert mrp_client.post(base + "/avatar", data={"expected_revision": str(card["revision"])}, files={"file": ("ok.png", picture(), "image/png")}).status_code == 409
    assert mrp_client.get(base + "/avatar").content == before
    assert mrp_client.post(base + "/full-body", files={"file": ("large.png", b"x" * (20 * 1024 * 1024 + 1), "image/png")}).status_code == 413


def test_failed_card_save_restores_previous_avatar(mrp_client, monkeypatch):
    card = create(mrp_client)
    base = f'/api/v1/characters/{card["id"]}'
    assert mrp_client.post(base + "/avatar", files={"file": ("old.png", picture(), "image/png")}).status_code == 200
    previous = mrp_client.get(base + "/avatar").content
    revision = mrp_client.get(base).json()["revision"]
    async def fail(_):
        raise OSError("Synthetic save failure")
    monkeypatch.setattr(mrp_client.app.state.container, "save_character", fail)
    with pytest.raises(OSError):
        mrp_client.post(base + "/avatar", files={"file": ("new.png", picture("JPEG"), "image/jpeg")})
    assert mrp_client.get(base + "/avatar").content == previous
    assert mrp_client.get(base).json()["revision"] == revision


def test_replacing_media_keeps_private_uploaded_bytes_and_previous_image(mrp_client):
    card = create(mrp_client)
    base = f'/api/v1/characters/{card["id"]}'
    original = picture('JPEG')
    assert mrp_client.post(base + '/full-body', files={'file': ('first.jpg', original, 'image/jpeg')}).status_code == 200
    previous = mrp_client.get(base + '/full-body').content
    assert mrp_client.post(base + '/full-body', files={'file': ('next.png', picture(), 'image/png')}).status_code == 200
    directory = mrp_client.app.state.container.paths.characters_dir / card['id'] / '.media-history'
    versions = [path.read_bytes() for path in directory.iterdir()]
    assert original in versions and previous in versions
    assert mrp_client.get(base).json()['revision'] == card['revision']
