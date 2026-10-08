"""Recoverable, compare-and-set commits across the existing JSON registries.

The journal is private runtime data. Recovery never overwrites an unrelated edit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mrp.shared.models import Lorebook
from mrp.storage.atomic import read_json, write_json_atomic
from mrp.worlds.schema import World


def fingerprint(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def prepare(path: Path, *, operation_id: str, world_before: World | None,
            world_after: World | None, book_before: Lorebook | None,
            book_after: Lorebook | None, job_path: Path, job_after: dict[str, Any],
            additional_jobs: list[dict[str, Any]] | None = None,
            request_hash: str | None = None) -> dict[str, Any]:
    previous = read_json(path)
    if isinstance(previous, dict):
        return previous
    journal = {"operation_id": operation_id, "status": "prepared", "assets": [],
               "job_path": str(job_path), "job_after": job_after, "additional_jobs": additional_jobs or [],
               "request_hash": request_hash}
    for kind, before, after in (("book", book_before, book_after), ("world", world_before, world_after)):
        if after is not None:
            journal["assets"].append({"kind": kind, "id": after.id,
                                      "before_hash": fingerprint(before),
                                      "after_hash": fingerprint(after),
                                      "after": after.model_dump(mode="json")})
    write_json_atomic(path, journal, fsync=True)
    return journal


def _registry(container: Any, kind: str):
    return container.lorebook_registry if kind == "book" else container.world_registry


def _check(container: Any, journal: dict[str, Any]) -> None:
    for row in journal["assets"]:
        current = _registry(container, row["kind"]).by_id.get(row["id"])
        if fingerprint(current) not in (row["before_hash"], row["after_hash"]):
            raise RuntimeError("提交期间资产已有外部修改；草稿和恢复记录已保留，请重新检查差异")


def recover(container: Any, path: Path) -> dict[str, Any]:
    """Called after registries load, and before subsequent writes to this job."""
    journal = read_json(path)
    if not isinstance(journal, dict):
        raise RuntimeError("提交恢复记录损坏，不能自动覆盖资产")
    if journal.get("status") == "complete":
        return journal
    try:
        _check(container, journal)
        for row in journal["assets"]:
            registry = _registry(container, row["kind"])
            if fingerprint(registry.by_id.get(row["id"])) == row["after_hash"]:
                continue
            model = Lorebook if row["kind"] == "book" else World
            registry.save_sync(model.model_validate(row["after"]))
            row["applied"] = True
            write_json_atomic(path, journal, fsync=True)
        write_json_atomic(Path(journal["job_path"]), journal["job_after"], fsync=True)
        for item in journal.get("additional_jobs", []):
            write_json_atomic(Path(item["path"]), item["after"], fsync=True)
        journal["status"] = "complete"
        journal.pop("error", None)
        write_json_atomic(path, journal, fsync=True)
    except Exception as exc:
        journal["status"] = "conflict" if isinstance(exc, RuntimeError) else "interrupted"
        journal["error"] = str(exc)[:600]
        write_json_atomic(path, journal, fsync=True)
        raise
    return journal


async def apply(container: Any, path: Path) -> dict[str, Any]:
    # Every registry CAS editor uses these same locks. The ordering is fixed.
    async with container.world_registry._write_lock:
        async with container.lorebook_registry._write_lock:
            return recover(container, path)


def recover_all(container: Any, paths) -> list[str]:
    failures = []
    for path in paths:
        try:
            recover(container, path)
        except Exception:
            failures.append(str(path))
    return failures


def ensure_no_pending(paths) -> None:
    """Keep the reviewed draft and its recovery journal intact until recovery finishes."""
    for path in paths:
        journal = read_json(path)
        if not isinstance(journal, dict) or journal.get("status") != "complete":
            raise RuntimeError("此任务的提交尚未恢复完成；请重试原提交，恢复记录与草稿需要保留")
