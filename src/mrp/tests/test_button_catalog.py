"""Button frame ids stay aligned with the open visual_style_id string."""
from __future__ import annotations

import re

from mrp.appearance_catalog import CATALOG_PATH, load_button_catalog
from mrp.settings import AppearancePreferences

# Frames drawn by buttons.css and its explicitly imported frame stylesheet.
KNOWN_FRAMES = {"celestial", "ink", "prism", "starship", "quiet", "moonweave"}
STYLE_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def test_catalog_matches_the_button_stylesheet():
    catalog = load_button_catalog()
    stylesheet = CATALOG_PATH.with_name("buttons.css")
    css = stylesheet.read_text(encoding="utf-8")
    # A separate frame file counts only when the production entry imports it.
    assert re.search(r'@import\s+["\']\./moonweave\.css["\']\s*;', css)
    css += "\n" + stylesheet.with_name("moonweave.css").read_text(encoding="utf-8")
    assert CATALOG_PATH.is_file()
    assert set(catalog["frames"]) == KNOWN_FRAMES
    for frame in catalog["frames"]:
        assert f"@container button-frame style(--btn-kind: {frame})" in css
    ids = [style["id"] for style in catalog["styles"]]
    assert ids == list(dict.fromkeys(ids))
    assert "celestial-atelier" in ids
    for style in catalog["styles"]:
        assert STYLE_ID.fullmatch(style["id"])
        assert style["frame"] in KNOWN_FRAMES
        assert style["name"] and style["note"]
        AppearancePreferences(visual_style_id=style["id"])


def test_unknown_visual_style_is_stored():
    appearance = AppearancePreferences(visual_style_id="future-frame")
    assert appearance.visual_style_id == "future-frame"
