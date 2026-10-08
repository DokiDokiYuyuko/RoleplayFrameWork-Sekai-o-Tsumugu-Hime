"""Human-readable download names; persistent storage always uses stable IDs."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote


_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_filename_stem(value: str, *, fallback: str = "asset", max_length: int = 100) -> str:
    """Make a display title safe as a filename without using it as an identity."""
    stem = unicodedata.normalize("NFC", value or "")
    stem = _INVALID_FILENAME.sub("_", stem).strip().rstrip(".")
    stem = stem.lstrip(".").strip()
    stem = stem[:max_length].rstrip(" .")
    if not stem:
        stem = fallback
    if stem.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    return stem


def content_disposition(value: str, extension: str, *, fallback: str = "asset") -> str:
    """RFC 5987 attachment header with a Unicode display name."""
    suffix = extension if extension.startswith(".") else f".{extension}"
    filename = f"{safe_filename_stem(value, fallback=fallback)}{suffix}"
    return f"attachment; filename*=UTF-8''{quote(filename)}"
