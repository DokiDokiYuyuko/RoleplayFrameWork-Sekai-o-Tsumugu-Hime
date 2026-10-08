"""Validated character images, normalized to metadata-free PNG and written atomically."""
from io import BytesIO
import os
from pathlib import Path
import tempfile
import hashlib

from PIL import Image, ImageOps, UnidentifiedImageError


def normalize_image(raw: bytes) -> bytes:
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("请上传 PNG、JPEG 或 WebP 图片")
            if source.width * source.height > 32_000_000:
                raise ValueError("图片像素过大，请缩小后上传")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.info.clear()
            output = BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取这张图片，请上传完整的 PNG、JPEG 或 WebP 图片") from exc


def write_image_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".image-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def preserve_image_version(directory: Path, kind: str, data: bytes) -> Path:
    """Keep private originals/replaced images by content identity before adopting a crop."""
    if kind not in {"avatar-original", "avatar-previous", "full-body-original", "full-body-previous"}:
        raise ValueError("Unsupported image history kind")
    target = directory / ".media-history" / f"{kind}-{hashlib.sha256(data).hexdigest()}.image"
    if not target.exists():
        write_image_atomic(target, data)
    return target
