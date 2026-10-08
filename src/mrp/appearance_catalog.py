"""Visual-style ids shared with the web client's button frames.

The client draws the geometry. This module only reads the ids so a test can
keep the settings field and the picker on the same list. Well-formed ids that
are not in the catalog stay valid; a newer client may store one this process
does not draw.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CATALOG_PATH = (
    Path(__file__).resolve().parents[1]
    / "web"
    / "src"
    / "appearance"
    / "buttons"
    / "catalog.json"
)


@lru_cache(maxsize=1)
def load_button_catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
