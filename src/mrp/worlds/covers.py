"""Local, curated world cover catalog. Images never become prompt material."""
from pathlib import Path
import re
import json

from mrp.storage.atomic import read_json


def _builtin_path(filename: str) -> Path | None:
    # A packaged server serves dist; a source checkout can use the reviewed public files.
    web = Path(__file__).resolve().parents[2] / "web"
    for directory in (web / "dist", web / "public"):
        root = (directory / "brand" / "moonweave" / "ui-v3").resolve()
        path = (root / filename).resolve()
        if path.is_relative_to(root) and path.is_file():
            return path
    return None


def builtin_cover_catalog() -> list[dict]:
    rows = json.loads(Path(__file__).with_name("builtin_covers.json").read_text(encoding="utf-8"))
    return [dict(row, image_url=f"/api/v1/world-covers/{row['id']}/image")
            for row in rows if _builtin_path(row["filename"]) is not None]


def cover_catalog(data_root: Path) -> list[dict]:
    root = (data_root / "world_cover_assets").resolve()
    rows = read_json(root / "catalog.json")
    if not isinstance(rows, list):
        rows = []
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key, filename = row.get("id", ""), row.get("filename", "")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", key) or key in seen:
            continue
        if not isinstance(filename, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+\.(png|jpg|jpeg|webp)", filename):
            continue
        path = (root / filename).resolve()
        if path.parent != root or not path.is_file():
            continue
        if not all(isinstance(row.get(field), str) and row[field].strip() for field in ("title", "theme", "theme_label")):
            continue
        result.append({"id": key, "title": row["title"], "theme": row["theme"],
                       "theme_label": row["theme_label"], "filename": filename,
                       "image_url": f"/api/v1/world-covers/{key}/image"})
        seen.add(key)
    result.extend(row for row in builtin_cover_catalog() if row["id"] not in seen)
    return result


def cover_image(data_root: Path, cover_id: str) -> Path | None:
    row = next((row for row in cover_catalog(data_root) if row["id"] == cover_id), None)
    if not row:
        return None
    if "/" in row["filename"]:
        return _builtin_path(row["filename"])
    return (data_root / "world_cover_assets" / row["filename"]).resolve()
