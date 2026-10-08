"""Project and local credential paths, independent of the shell directory."""
from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_root() -> Path:
    configured = os.environ.get("MRP_DATA_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else (PROJECT_ROOT.parent / "data").resolve()


def modelscope_token(cli_token: str = "") -> str:
    if cli_token:
        return cli_token
    local_file = data_root() / "local_credentials.json"
    if local_file.exists():
        try:
            value = json.loads(local_file.read_text(encoding="utf-8-sig")).get("modelscope_token", "")
        except (ValueError, OSError) as exc:
            raise SystemExit("Cannot read data/local_credentials.json; check the local file.") from exc
        if isinstance(value, str) and value.strip():
            return value.strip()
    return os.environ.get("MODELSCOPE_TOKEN", "")
