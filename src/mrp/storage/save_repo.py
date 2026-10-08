"""存档仓储：`saves/save-<session>-<msgcount>[-k].json` + `saves/index.json`。

与旧实现的关系：
- id 格式**不变**（`save-{session_id}-{len(messages)}`），旧存档原样可读；
  同长度重复保存不再覆盖（旧缺陷）：已存在则追加 `-2`/`-3` 后缀并返回实际 id。
- `list()` 只读索引；坏文件跳过并告警（旧 app.py:931 对 glob 结果裸 `stat()` 会 500）。
- `load()` 将旧状态迁移到 v3（备份 `{name}.v{version}.bak`，永不覆盖第一份）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mrp.orchestrator.migration import migrate_save_to_v3_dict
from mrp.shared.models import MemoryRecord, SaveFile, SessionState

from .atomic import read_json, unlink_missing_ok, write_json_atomic, write_text_atomic
from .story_sqlite import StorySqlite
import time
from .json_store import (
    FileStamp,
    raw_schema_version,
    read_text_many_sync,
    scan_json_files_sync,
)
from .paths import AppPaths, is_safe_name

logger = logging.getLogger("mrp.storage.save")

INDEX_FILENAME = "index.json"
INDEX_SCHEMA = 1
_INDEX_COMPAT_SCHEMA = 2

_API_FIELDS = ("id", "session_id", "name", "created_at", "turn", "message_count")


class SaveSummary(BaseModel):
    """旧 `/api/v1/saves` 输出字段 + 内部失效判定。"""

    id: str
    session_id: str
    name: str = ""  # 已做"空名回落会话标题"，与旧输出一致
    created_at: datetime
    turn: int = 0
    message_count: int = 0
    updated_at: float = 0.0
    mtime_ns: int = 0
    size: int = 0

    def api_dict(self) -> dict[str, Any]:
        """旧 `/api/v1/saves` 响应体逐键复刻（saved_at → created_at.isoformat）。"""
        d = self.model_dump(mode="json", include=set(_API_FIELDS))
        d["created_at"] = self.created_at.isoformat()
        return d


def summary_from_save(save_id: str, save: SaveFile, stamp: FileStamp | None) -> SaveSummary:
    state = save.state
    return SaveSummary(
        id=save_id,
        session_id=state.meta.id,
        name=save.name or state.meta.title,
        created_at=save.saved_at,
        turn=state.current_turn(),
        message_count=len(state.messages),
        updated_at=stamp.mtime if stamp else 0.0,
        mtime_ns=stamp.mtime_ns if stamp else 0,
        size=stamp.size if stamp else 0,
    )


class SaveRepo:
    """存档读写 + 索引。create/delete 按 save id 串行（进程内 asyncio.Lock）。"""

    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.story_db = StorySqlite(paths.data_root / "memories" / "memory.db")
        self._index_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._rows: dict[str, SaveSummary] | None = None

    # ---------- 路径 ----------

    def path_for(self, save_id: str) -> Path:
        if not is_safe_name(save_id):
            raise ValueError(f"非法存档 id: {save_id!r}")
        return self.paths.saves_dir / f"{save_id}.json"

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def base_id(self, state: SessionState) -> str:
        """旧 id 语义（兼容保留）：`save-{session_id}-{len(messages)}`。"""
        return f"save-{state.meta.id}-{len(state.messages)}"

    # ---------- 读 ----------

    async def list(self, session_id: str | None = None) -> list[SaveSummary]:
        async with self._index_lock:
            return await asyncio.to_thread(self.list_sync, session_id)

    def list_sync(self, session_id: str | None = None) -> list[SaveSummary]:
        rows = self._read_index_sync()
        entries = scan_json_files_sync(self.paths.saves_dir, skip=(INDEX_FILENAME,))
        owned = self.story_db.owned_save_ids()
        entries = [(path, stamp) for path, stamp in entries if path.stem not in owned]
        changed = rows is None
        if rows is None:
            rows = {}

        seen: set[str] = set()
        stale: list[tuple[Path, FileStamp]] = []
        for path, stamp in entries:
            seen.add(path.stem)
            cur = rows.get(path.stem)
            if cur is not None and cur.mtime_ns == stamp.mtime_ns and cur.size == stamp.size:
                continue
            stale.append((path, stamp))

        if stale:
            changed = True
            texts = read_text_many_sync([p for p, _ in stale])
            for (path, stamp), text in zip(stale, texts):
                summary = self._summary_from_text(path, text, stamp)
                if summary is None:
                    rows.pop(path.stem, None)
                else:
                    rows[path.stem] = summary

        for sid in [sid for sid in rows if sid not in seen]:
            rows.pop(sid)
            changed = True

        if changed:
            self._write_index_sync(rows)
        self._rows = rows
        combined = dict(rows)
        combined.update({row['id']: SaveSummary.model_validate(row) for row in self.story_db.save_summaries()})
        out = [s for s in combined.values() if not session_id or s.session_id == session_id]
        return sorted(out, key=lambda s: (-s.updated_at, s.id))

    async def load(self, save_id: str) -> SaveFile | None:
        """读存档；缺失/损坏 → None。旧 schema → v3 时留备份。"""
        try:
            path = self.path_for(save_id)
            if await asyncio.to_thread(self.story_db.save_owns, save_id):
                return await asyncio.to_thread(self.story_db.load_save, save_id)
        except ValueError:
            return None
        try:
            return await asyncio.to_thread(self._load_sync, path)
        except FileNotFoundError:
            return None
        except Exception:  # noqa: BLE001
            logger.warning("存档读取失败: %s", path, exc_info=True)
            return None

    # ---------- 写 ----------

    async def create(
        self, state: SessionState, name: str = "",
        memory_snapshot: list[MemoryRecord] | None = None,
    ) -> tuple[str, SaveSummary]:
        """新建存档，返回 (实际 save_id, SaveSummary)。同长度重复保存自动加后缀，绝不覆盖。"""
        save_name = name.strip() or state.meta.title
        async with self._lock(state.meta.id):  # 同一会话的并发保存串行（防 id 竞争）
            save_id = self.base_id(state)
            k = 2
            while await asyncio.to_thread(self.exists_sync, save_id):
                save_id = f"{self.base_id(state)}-{k}"
                k += 1
            save = SaveFile(
                schema_version=state.schema_version, name=save_name, state=state,
                memory_snapshot=memory_snapshot or [],
                memory_snapshot_complete=memory_snapshot is not None,
                memory_refs=[record.id for record in memory_snapshot or []],
            )
            path = self.path_for(save_id)
            from .creation_operations import scope_for
            if scope_for(self.story_db) or await asyncio.to_thread(self.story_db.owns, state.meta.id):
                summary = summary_from_save(save_id, save, FileStamp(time.time(), time.time_ns(), 0))
                await asyncio.to_thread(self.story_db.put_save, save_id, save, summary.model_dump(mode='json'))
                return save_id, summary
            await asyncio.to_thread(self._write_legacy_guarded,path,save)
            stamp = await asyncio.to_thread(FileStamp.of, path)
            summary = summary_from_save(save_id, save, stamp)
            await self._upsert_row(summary)
            return save_id, summary

    async def delete(self, save_id: str) -> bool:
        try:
            path = self.path_for(save_id)
        except ValueError:
            return False
        async with self._lock(save_id):
            if await asyncio.to_thread(self.story_db.save_owns, save_id):
                return await asyncio.to_thread(self.story_db.delete_save, save_id)
            existed = await asyncio.to_thread(unlink_missing_ok, path)
            async with self._index_lock:
                rows = await asyncio.to_thread(self._rows_for_write)
                if rows.pop(save_id, None) is not None:
                    await asyncio.to_thread(self._write_index_sync, rows)
                    self._rows = rows
            return existed

    def exists_sync(self, save_id):
        return self.story_db.save_owns(save_id) or self.path_for(save_id).exists()

    async def publish(self, save_id, save):
        """Publish an imported immutable save through its branch's active route."""
        from .creation_operations import scope_for
        if scope_for(self.story_db) or self.story_db.owns(save.state.meta.id):
            summary = summary_from_save(save_id, save, FileStamp(time.time(), time.time_ns(), 0))
            await asyncio.to_thread(self.story_db.put_save, save_id, save, summary.model_dump(mode='json'))
        else:
            path = self.path_for(save_id)
            if path.exists():
                raise FileExistsError(save_id)
            await asyncio.to_thread(self._write_legacy_guarded,path,save)

    def _write_legacy_guarded(self,path,save):
        from .story_sqlite import BranchRevisionConflict
        with self.story_db.transaction() as db:
            self.story_db.require_lifecycle_access(db,save.state.meta.story_id or save.state.meta.id)
            row=db.execute('SELECT active FROM branches WHERE id=?',(save.state.meta.id,)).fetchone()
            if row and row[0] != 0:
                raise BranchRevisionConflict('存储已切换或路线已删除，请重新读取')
            write_json_atomic(path,save,indent=None,fsync=True)

    # ---------- 同步实现 ----------

    def _load_sync(self, path: Path) -> SaveFile:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text)
        migrated, changed = migrate_save_to_v3_dict(raw)
        validated = SaveFile.model_validate(migrated)
        if changed:
            bak = path.with_suffix(path.suffix + f".v{raw_schema_version(raw)}.bak")
            if not bak.exists():
                write_text_atomic(bak, text)
            write_json_atomic(path, migrated, indent=None)
        return validated

    def _summary_from_text(self, path: Path, text: str | None, stamp: FileStamp) -> SaveSummary | None:
        """列表路径**不做迁移回写**（旧 list_saves 亦如此，避免列表触发写放大）。"""
        if text is None:
            logger.warning("存档文件读取失败，索引行丢弃: %s", path)
            return None
        try:
            save = SaveFile.model_validate(json.loads(text))
            return summary_from_save(path.stem, save, stamp)
        except Exception:  # noqa: BLE001 —— 坏文件不进列表
            logger.warning("存档解析失败，索引行丢弃: %s", path, exc_info=True)
            return None

    # ---------- 索引 ----------

    def _read_index_sync(self) -> dict[str, SaveSummary] | None:
        raw = read_json(self.paths.saves_index_file)
        if not isinstance(raw, dict) or raw.get("index_schema") != INDEX_SCHEMA:
            return None
        rows = raw.get("saves")
        if not isinstance(rows, dict):
            return None
        out: dict[str, SaveSummary] = {}
        for sid, row in rows.items():
            try:
                out[str(sid)] = SaveSummary.model_validate(row)
            except Exception:  # noqa: BLE001
                logger.warning("索引行损坏，触发重建: %s", sid)
                return None
        return out

    def _write_index_sync(self, rows: dict[str, SaveSummary]) -> None:
        payload: dict[str, Any] = {
            "index_schema": INDEX_SCHEMA,
            "schema_version": _INDEX_COMPAT_SCHEMA,
            "saves": {
                sid: s.model_dump(mode="json", exclude_defaults=True) for sid, s in rows.items()
            },
        }
        write_json_atomic(
            self.paths.saves_index_file, payload, indent=None, separators=(",", ":")
        )

    def _rows_for_write(self) -> dict[str, SaveSummary]:
        if self._rows is not None:
            return dict(self._rows)
        return dict(self._read_index_sync() or {})

    async def _upsert_row(self, summary: SaveSummary) -> None:
        async with self._index_lock:
            rows = await asyncio.to_thread(self._rows_for_write)
            rows[summary.id] = summary
            await asyncio.to_thread(self._write_index_sync, rows)
            self._rows = rows
