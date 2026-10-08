"""Immutable, content-addressed story pictures, outside the code repository."""
from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from pathlib import Path
from PIL import Image

_REF = re.compile(r"[0-9a-f]{64}\.png\Z")


def media_path(data_root: Path, ref: str) -> Path:
    if not _REF.fullmatch(ref):
        raise ValueError("无效故事图片引用")
    return Path(data_root) / "story_media" / ref


def store_media(data_root: Path, data: bytes, *, expected_ref: str | None = None) -> str:
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("头像图片过大")
    with Image.open(io.BytesIO(data)) as im:
        if im.format != "PNG":
            raise ValueError("故事头像必须为 PNG")
        im.verify()
    ref = hashlib.sha256(data).hexdigest() + ".png"
    if expected_ref is not None and expected_ref != ref:
        raise ValueError("故事图片指纹不一致")
    path = media_path(data_root, ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(ref + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return ref


def capture_avatar(data_root: Path, character_id: str | None) -> str | None:
    from mrp.storage.paths import is_safe_name
    if not character_id or not is_safe_name(character_id):
        return None
    source = Path(data_root) / "characters" / character_id / "avatar.png"
    if not source.is_file():
        return None
    try:
        return store_media(data_root, source.read_bytes())
    except (OSError, ValueError):
        # A missing/broken optional picture must not block a story or control transfer.
        return None
