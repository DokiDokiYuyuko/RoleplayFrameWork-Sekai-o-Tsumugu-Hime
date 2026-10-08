"""Local archive of complete model request JSON bodies, without HTTP credentials."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


class RequestArchive:
    def __init__(self, data_root: Path) -> None:
        self.root = Path(data_root) / "model_requests"

    def _session_dir(self, session_id: str) -> Path:
        if not _SAFE_ID.fullmatch(session_id):
            raise ValueError("Invalid session ID")
        return self.root / session_id

    def write(self, session_id: str, character_id: str, turn: int,
              engine: str, body: dict[str, Any], *, plan_id: str | None = None,
              player_identity_source: dict | None = None, message_id: str | None = None,
              generation_id: str | None = None, operation_id: str | None = None,
              attempt_id: str | None = None) -> str:
        if not _SAFE_ID.fullmatch(character_id):
            raise ValueError("Invalid character ID")
        directory = self._session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        request_id = uuid.uuid4().hex
        record = {
            "id": request_id,
            "session_id": session_id,
            "character_id": character_id,
            "turn": turn,
            "engine": engine,
            "plan_id": plan_id,
            "message_id": message_id, "generation_id": generation_id,
            "operation_id": operation_id, "attempt_id": attempt_id,
            "player_identity_source": player_identity_source or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "body": body,
        }
        target = directory / f"{turn:06d}-{request_id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return request_id

    def list(self, session_id: str) -> list[dict[str, Any]]:
        directory = self._session_dir(session_id)
        if not directory.exists():
            return []
        records = []
        for path in directory.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                records.append({key: record[key] for key in (
                    "id", "session_id", "character_id", "turn", "engine", "created_at"
                )})
                records[-1]["plan_id"] = record.get("plan_id")
                records[-1]["player_identity_source"] = record.get("player_identity_source", {})
                for key in ("message_id", "generation_id", "operation_id", "attempt_id"):
                    records[-1][key] = record.get(key)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return sorted(records, key=lambda item: item["created_at"], reverse=True)

    def get(self, session_id: str, request_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9a-f]{32}", request_id):
            return None
        directory = self._session_dir(session_id)
        for path in directory.glob(f"*-{request_id}.json"):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return None

    def delete_session(self, session_id: str) -> None:
        directory = self._session_dir(session_id)
        if directory.is_dir():
            shutil.rmtree(directory)
