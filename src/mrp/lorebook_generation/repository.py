from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mrp.storage.atomic import read_json, write_json_atomic


class LorebookGenerationRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def valid_id(job_id: str) -> bool:
        return bool(re.fullmatch(r"lbg-[a-f0-9]{20}", job_id))

    def directory(self, job_id: str) -> Path:
        if not self.valid_id(job_id):
            raise KeyError(job_id)
        return self.root / job_id

    def job_path(self, job_id: str) -> Path:
        return self.directory(job_id) / "job.json"

    def snapshot_path(self, job_id: str) -> Path:
        return self.directory(job_id) / "snapshot.json"

    def telemetry_path(self, job_id: str) -> Path:
        return self.directory(job_id) / "telemetry.json"

    def get(self, job_id: str) -> dict[str, Any]:
        job = read_json(self.job_path(job_id))
        if not isinstance(job, dict):
            raise KeyError(job_id)
        return job

    def snapshot(self, job_id: str) -> dict[str, Any]:
        snapshot = read_json(self.snapshot_path(job_id))
        if not isinstance(snapshot, dict):
            raise KeyError(job_id)
        return snapshot

    def save(self, job: dict[str, Any]) -> None:
        write_json_atomic(self.job_path(job["id"]), job)

    def save_snapshot(self, job_id: str, snapshot: dict[str, Any]) -> None:
        write_json_atomic(self.snapshot_path(job_id), snapshot)

    def list(self) -> list[dict[str, Any]]:
        rows = []
        for path in self.root.glob("lbg-*/job.json"):
            try:
                job = read_json(path)
                if isinstance(job, dict):
                    rows.append({key: job.get(key) for key in (
                        "id", "world_id", "world_title", "status", "created_at", "updated_at"
                    )})
            except OSError:
                continue
        return sorted(rows, key=lambda row: row.get("updated_at") or "", reverse=True)
