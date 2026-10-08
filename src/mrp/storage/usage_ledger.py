"""Append-only call receipts; durable even when a turn cannot be committed."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from mrp.shared.models import UsageRecord
from mrp.storage.paths import is_safe_name


class UsageLedger:
    def __init__(self, root: Path):
        self.root = root / "usage-ledger"
        self._lock = threading.Lock()

    def _path(self, session_id: str) -> Path:
        if not is_safe_name(session_id):
            raise ValueError("Invalid usage ledger identity")
        return self.root / f"{session_id}.jsonl"

    def read(self, session_id: str) -> tuple[list[UsageRecord], bool]:
        path = self._path(session_id)
        if not path.exists():
            return [], False
        records, damaged = {}, False
        with self._lock:
            for line in path.read_bytes().splitlines():
                try:
                    row = UsageRecord.model_validate_json(line)
                    records[row.id] = row
                except ValueError:
                    damaged = True
        return list(records.values()), damaged

    def append(self, session_id: str, record: UsageRecord) -> None:
        path = self._path(session_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a+b") as handle:
                # A crash can leave a partial JSON/UTF-8 tail. Delimit it so the
                # next valid receipt remains independently recoverable.
                handle.seek(0, os.SEEK_END)
                if handle.tell():
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":
                        handle.write(b"\n")
                handle.write((record.model_dump_json() + "\n").encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
