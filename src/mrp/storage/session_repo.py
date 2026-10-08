"""会话仓储：`sessions/<id>.json`（正文）+ `sessions/index.json`（首屏索引）。

列表首屏**绝不解析会话体**：index 缺失/损坏/schema 不符 → 扫目录重建；
index 行与文件 (mtime_ns, size) 不一致 → 只重读该条（惰性修正），不整表重建。

正文仍为紧凑的 `SessionState` JSON；v3 增加世界线字段，旧 app.py 不负责读取 v3。
- 迁移备份名 `{file}.v{schema_version}.bak`，永不覆盖第一份；
- `index.json` 顶层带 `"schema_version": 2`：旧 app.py 的迁移器见此版本号直接返回，
  **不会**把索引当 v1 会话迁移改写（旧的 `SessionState.model_validate` 失败后仅跳过）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mrp.orchestrator.migration import migrate_state_to_v3_dict
from mrp.orchestrator.worldline_state import record_persisted_head
from mrp.shared.models import SessionState

from .atomic import read_json, unlink_missing_ok, write_json_atomic, write_text_atomic, json_text
from .json_store import (
    FileStamp,
    raw_schema_version,
    read_text_many_sync,
    scan_json_files_sync,
)
from .paths import AppPaths, is_safe_name
from .story_sqlite import BranchRevisionConflict, StorySqlite
from .command_receipts import (CommandConflict, CommandAlreadyCommitted,
    portable_receipts, put_portable_receipts, public_receipt, validate_receipt,
    legacy_document, unwrap_legacy_document)
from .command_receipts import put_portable_jobs, portable_jobs
from .command_receipts import GenerationIncomplete, portable_claims, put_portable_claims, merge_claims

logger = logging.getLogger("mrp.storage.session")

INDEX_FILENAME = "index.json"
INDEX_SCHEMA = 3  # Rebuild rows with compact graph/event summaries.
_INDEX_COMPAT_SCHEMA = 2  # 见模块 docstring：防旧迁移器改写索引


def validate_state(raw: Any) -> SessionState:
    """会话校验的唯一入口（测试可替换，证明列表路径不解析会话体）。"""
    return SessionState.model_validate(unwrap_legacy_document(raw))


# 旧 list_sessions 输出键（顺序一致；updated_at=文件 mtime 浮点）
_API_FIELDS = (
    "id",
    "title",
    "created_at",
    "source_world_id",
    "story_id",
    "branch_name",
    "parent_branch_id",
    "branch_revision",
    "archived",
    "prompt_preset_id",
    "character_ids",
    "character_names",
    "persona",
    "player_character_id",
    "reply_max_tokens",
    "turn",
    "messages",
    "options_enabled",
    "options_style",
    "options_direct_send",
    "streaming_enabled",
    "hygiene_enabled",
    "director_mode",
    "narrative_pov",
    "response_style_id",
    "response_style_overrides",
    "narrative_density",
    "short_input_padding",
    "proactive_turn_limit",
    "updated_at",
)


class SessionSummary(BaseModel):
    """首屏所需字段，与旧 `/api/v1/sessions` 输出逐字段对齐。"""

    id: str
    title: str = ""
    created_at: datetime
    source_world_id: str | None = None
    prompt_preset_id: str | None = None
    # Worldline relation fields stay in the rebuildable index, not in legacy API output.
    story_id: str = ""
    branch_name: str = ""
    parent_branch_id: str | None = None
    fork_message_id: str | None = None
    branch_revision: int = 0
    archived: bool = False
    fork_save_id: str | None = None
    first_message_id: str | None = None
    last_message_id: str | None = None
    event_nodes: list[dict[str, Any]] = Field(default_factory=list)
    character_ids: list[str] = Field(default_factory=list)
    character_names: list[str] = Field(default_factory=list)
    persona: str = ""  # = meta.player_persona
    player_character_id: str | None = None
    reply_max_tokens: int | None = None
    turn: int = 0
    messages: int = 0
    options_enabled: bool = True
    options_style: str = "mixed"
    options_direct_send: bool = False
    streaming_enabled: bool = True
    hygiene_enabled: bool = True
    director_mode: str = "auto"
    narrative_pov: str = "free"
    response_style_id: str | None = None
    response_style_overrides: dict = Field(default_factory=dict)
    narrative_density: str = "balanced"
    short_input_padding: bool = True
    proactive_turn_limit: int = 1
    updated_at: float = 0.0  # = 文件 st_mtime（旧 API 的 updated_at）
    # ---- 内部：索引失效判定（不进 API 输出）----
    mtime_ns: int = 0
    size: int = 0

    def api_dict(self) -> dict[str, Any]:
        """旧 `/api/v1/sessions` 响应体的逐键复刻（含 `.isoformat()`，勿用 model_dump 直接返回）。"""
        d = self.model_dump(mode="json", include=set(_API_FIELDS))
        d["created_at"] = self.created_at.isoformat()
        return d


def summary_from_state(state: SessionState, stamp: FileStamp | None) -> SessionSummary:
    m = state.meta
    return SessionSummary(
        id=m.id,
        title=m.title,
        created_at=m.created_at,
        source_world_id=m.source_world_id,
        prompt_preset_id=m.prompt_preset_id,
        story_id=m.story_id,
        branch_name=m.branch_name,
        parent_branch_id=m.parent_branch_id,
        fork_message_id=m.fork_message_id,
        branch_revision=m.branch_revision,
        archived=m.archived,
        fork_save_id=m.fork_save_id,
        first_message_id=state.messages[0].id if state.messages else None,
        last_message_id=state.messages[-1].id if state.messages else None,
        event_nodes=[{
            "id": event.id,
            "anchor_message_id": event.anchor_message_id,
            "title": event.title,
            "kind": event.kind,
            "visible_to": event.visible_to,
            "created_at": event.created_at.isoformat(),
        } for event in state.story_events],
        character_ids=list(m.character_ids),
        character_names=[c.card.name for c in state.characters],
        persona=m.player_persona,
        player_character_id=m.player_character_id,
        reply_max_tokens=m.reply_max_tokens,
        turn=state.current_turn(),
        messages=len(state.messages),
        options_enabled=m.options_enabled,
        options_style=m.options_style,
        options_direct_send=m.options_direct_send,
        streaming_enabled=m.streaming_enabled,
        hygiene_enabled=m.hygiene_enabled,
        director_mode=m.director_mode,
        narrative_pov=m.narrative_pov,
        response_style_id=m.response_style_id,
        response_style_overrides=m.response_style_overrides,
        narrative_density=m.narrative_density,
        short_input_padding=m.short_input_padding,
        proactive_turn_limit=m.proactive_turn_limit,
        updated_at=stamp.mtime if stamp else 0.0,
        mtime_ns=stamp.mtime_ns if stamp else 0,
        size=stamp.size if stamp else 0,
    )


class _CrossLoopLock:
    """Async context manager backed by a regular lock shared across event loops."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    async def __aenter__(self) -> "_CrossLoopLock":
        # Poll without occupying a default-executor thread: a burst of waiters
        # must not starve the to_thread() work performed inside the critical section.
        while not self._lock.acquire(blocking=False):
            await asyncio.sleep(0.005)
        return self

    async def __aexit__(self, *_: object) -> None:
        self._lock.release()


class SessionRepo:
    """会话读写 + 索引维护。写路径按 session id 串行（跨事件循环锁）。"""

    def require_visible_branch(self, branch_id):
        from .lifecycle_repo import LifecycleConflict
        with self.story_db.connect() as db:
            if db.execute('SELECT 1 FROM branch_purges WHERE branch_id=?',(branch_id,)).fetchone():
                raise LifecycleConflict('故事正文已永久清除','operation_result_purged',410)
            row = db.execute('SELECT active FROM branches WHERE id=?',(branch_id,)).fetchone()
            if row is not None and row[0] == -1:
                raise LifecycleConflict('路线不可见','not_found',404)
        if (row is None or row[0] == 0) and self.load_state_readonly_sync(branch_id) is None:
            raise LifecycleConflict('路线不存在','not_found',404)

    def _write_legacy_guarded(self,path,state):
        with self.story_db.transaction() as db:
            self.story_db.require_lifecycle_access(db,state.meta.story_id or state.meta.id)
            row=db.execute('SELECT active FROM branches WHERE id=?',(state.meta.id,)).fetchone()
            if row and row[0] != 0:
                raise BranchRevisionConflict('存储已切换，请重新加载路线')
            write_json_atomic(path,legacy_document(state),indent=None,fsync=True)

    def create_lifecycle_repo(self, saves):
        from .lifecycle_repo import LifecycleRepo
        return LifecycleRepo(self, saves)

    def creation_active(self):
        from .creation_operations import scope_for
        return scope_for(self.story_db) is not None

    async def execute_creation(self, memory_store, action, payload, operation_id, create, response_meta=None, publication_gate=None):
        from .creation_operations import execute
        return await execute(self, memory_store, action, payload, operation_id, create, response_meta, publication_gate)

    async def claim_creation_entity(self, kind, identity):
        from .creation_operations import claim
        await asyncio.to_thread(claim, self.story_db, kind, identity)

    async def adopt_creation_branch(self, state):
        from .creation_operations import adopt
        await asyncio.to_thread(adopt, self.story_db, state.meta.id, state.meta.branch_revision)

    def __init__(self, paths: AppPaths, *, sqlite_new_stories: bool = False) -> None:
        self.paths = paths
        self.sqlite_new_stories = sqlite_new_stories
        # Sharing the memory database permits explicit pure-SQL memory commands
        # in the same transaction. Existing MemoryStore APIs retain their journal.
        self.story_db = StorySqlite(paths.data_root / "memories" / "memory.db")
        # This compatibility app may be reused by TestClients on separate event
        # loops. A thread lock keeps same-path writes serialized across loops.
        self._index_lock = _CrossLoopLock()
        self._locks: dict[str, _CrossLoopLock] = {}
        self._rows: dict[str, SessionSummary] | None = None  # 内存索引快照（None=未加载）

    # ---------- 路径 ----------

    def path_for(self, session_id: str) -> Path:
        if not is_safe_name(session_id):
            raise ValueError(f"非法会话 id: {session_id!r}")
        return self.paths.sessions_dir / f"{session_id}.json"

    def _lock(self, session_id: str) -> _CrossLoopLock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = self._locks[session_id] = _CrossLoopLock()
        return lock

    # ---------- 读 ----------

    async def list_summaries(self) -> list[SessionSummary]:
        """首屏列表：一次目录扫描 + 索引行比对；只对失效条目重读文件。"""
        async with self._index_lock:
            return await asyncio.to_thread(self.list_summaries_sync)

    async def has_child_at(self, branch_id: str, message_id: str) -> bool:
        """Protect parent anchors from legacy destructive message operations."""
        rows = await self.list_summaries()
        return any(row.parent_branch_id == branch_id and row.fork_message_id == message_id
                   for row in rows)

    def list_summaries_sync(self) -> list[SessionSummary]:
        rows = self._read_index_sync()
        entries = scan_json_files_sync(self.paths.sessions_dir, skip=(INDEX_FILENAME,))
        owned = self.story_db.owned_ids()
        entries = [(path, stamp) for path, stamp in entries if path.stem not in owned]
        changed = rows is None
        if rows is None:
            rows = {}

        seen: set[str] = set()
        stale: list[tuple[Path, FileStamp]] = []
        for path, stamp in entries:
            sid = path.stem
            seen.add(sid)
            cur = rows.get(sid)
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
                    rows[path.stem] = summary  # 键=文件名 stem（与文件一一对应，防 id 不一致来回重读）

        for sid in [sid for sid in rows if sid not in seen]:
            rows.pop(sid)
            changed = True

        if changed:
            self._write_index_sync(rows)
        self._rows = rows
        combined = dict(rows)
        combined.update({row['id']: SessionSummary.model_validate(row) for row in self.story_db.summaries()})
        return sorted(combined.values(), key=lambda s: (-s.updated_at, s.id))

    async def load_state(self, session_id: str) -> SessionState | None:
        """读会话体；缺失/损坏 → None（不抛）。旧 schema → v3 时留备份。"""
        try:
            path = self.path_for(session_id)
            if await asyncio.to_thread(self.story_db.owns, session_id):
                return await asyncio.to_thread(self.story_db.load, session_id)
        except ValueError:
            return None
        try:
            return await asyncio.to_thread(self._load_state_sync, path)
        except FileNotFoundError:
            return None
        except Exception:  # noqa: BLE001 —— 坏会话不炸端点
            logger.warning("会话读取失败: %s", path, exc_info=True)
            return None

    async def load_state_readonly(self, session_id: str) -> SessionState | None:
        """Read an old or current branch without rewriting its session file."""
        try:
            path = self.path_for(session_id)
            if await asyncio.to_thread(self.story_db.owns, session_id):
                return await asyncio.to_thread(self.story_db.load, session_id)
            return await asyncio.to_thread(self._load_state_readonly_sync, path)
        except (ValueError, FileNotFoundError):
            return None
        except Exception:  # noqa: BLE001
            logger.warning("会话只读读取失败: %s", session_id, exc_info=True)
            return None

    def _load_state_readonly_sync(self, path: Path) -> SessionState:
        raw = json.loads(path.read_text(encoding="utf-8"))
        migrated, _ = migrate_state_to_v3_dict(unwrap_legacy_document(raw))
        return validate_state(migrated)

    async def migrate_if_needed(self, session_id: str) -> bool:
        """显式迁移入口；返回是否发生迁移。文件不存在/损坏 → False。"""
        try:
            path = self.path_for(session_id)
            if await asyncio.to_thread(self.story_db.owns, session_id):
                return False
            _, changed = await asyncio.to_thread(self._load_state_with_migration, path)
            return changed
        except FileNotFoundError:
            return False
        except Exception:  # noqa: BLE001
            logger.warning("会话迁移失败: %s", session_id, exc_info=True)
            return False

    # ---------- 写 ----------

    async def create_state_exclusive(
        self, state: SessionState, memory_watermark: int | None = None,
    ) -> SessionSummary:
        """Publish a complete new story atomically; never replace any existing file."""
        sid = state.meta.id
        async with self._lock(sid):
            path = self.path_for(sid)
            if self.creation_active() or self.sqlite_new_stories:
                if path.exists() or await asyncio.to_thread(self.story_db.active, sid):
                    raise FileExistsError(sid)
                return await self._commit_sqlite(state, memory_watermark, exclusive=True)
            staged = state.model_copy(deep=True)
            if staged.schema_version >= 3:
                record_persisted_head(staged, memory_watermark)
                staged.meta.branch_revision += 1
            payload = json_text(legacy_document(staged), indent=None)
            def publish():
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(prefix=".mrp-create-", suffix=".tmp", dir=path.parent)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary, path)  # Atomic and exclusive, including across containers.
                finally:
                    Path(temporary).unlink(missing_ok=True)
            await asyncio.to_thread(publish)
            state.meta.branch_revision = staged.meta.branch_revision
            state.state_revisions = staged.state_revisions
            state.head_state_revision_id = staged.head_state_revision_id
            stamp = await asyncio.to_thread(FileStamp.of, path)
            summary = summary_from_state(state, stamp)
            try:
                await self._upsert_row(summary)
            except Exception:
                logger.warning("新故事已提交，派生索引稍后重建: %s", sid, exc_info=True)
            return summary

    async def save_state(
        self, state: SessionState, memory_watermark: int | None = None, *,
        expected_revision: int | None = None, operation_id: str | None = None,
        outbox_events=(), memory_actions=(), command_receipt=None,
    ) -> SessionSummary:
        """写正文（原子）+ 更新索引行（一次索引写）。同 session 串行。"""
        sid = state.meta.id
        async with self._lock(sid):
            path = self.path_for(sid)
            if self.creation_active() or await asyncio.to_thread(self.story_db.owns, sid) or (self.sqlite_new_stories and not path.exists()):
                return await self._commit_sqlite(state, memory_watermark,
                    expected_revision=expected_revision, operation_id=operation_id,
                    outbox_events=outbox_events, memory_actions=memory_actions,
                    command_receipt=command_receipt)
            if memory_actions:
                raise ValueError("Transactional memory commands require an active SQLite story")
            current = await asyncio.to_thread(self._load_state_readonly_sync, path) if path.exists() else None
            persisted_receipts = portable_receipts(current) if current is not None else {}
            if command_receipt is not None:
                validate_receipt(command_receipt)
                claim = portable_claims(current).get(command_receipt['operation_id']) if current is not None else None
                if claim is not None and claim != command_receipt['fingerprint']:
                    raise CommandConflict('操作 ID 已被另一生成请求占用')
                recorded = persisted_receipts.get(command_receipt['operation_id'])
                if recorded is not None:
                    if recorded['fingerprint'] != command_receipt['fingerprint']:
                        raise CommandConflict("同一命令ID已用于不同请求")
                    raise CommandAlreadyCommitted(public_receipt(recorded))
            if expected_revision is not None and current is not None:
                if current.meta.branch_revision != expected_revision:
                    raise BranchRevisionConflict("世界线已更新，请刷新后重试")
            staged = state.model_copy(deep=True)
            if current is not None:
                merge_claims(staged, current)
            if staged.schema_version >= 3:
                record_persisted_head(staged, memory_watermark)
                staged.meta.branch_revision += 1
            entries = dict(portable_receipts(staged))
            for operation, receipt in persisted_receipts.items():
                if operation in entries and entries[operation] != receipt:
                    raise CommandConflict("保存不能改写已提交命令结果")
                entries[operation] = receipt
            if command_receipt is not None:
                operation = command_receipt['operation_id']
                receipt = {**public_receipt(command_receipt), 'revision': staged.meta.branch_revision}
                if operation in entries and entries[operation] != receipt:
                    raise CommandConflict("命令凭据与已有状态不一致")
                entries[operation] = receipt
            put_portable_receipts(staged, entries)
            await asyncio.to_thread(self._write_legacy_guarded, path, staged)
            state.meta.branch_revision = staged.meta.branch_revision
            state.state_revisions = staged.state_revisions
            state.head_state_revision_id = staged.head_state_revision_id
            put_portable_receipts(state, portable_receipts(staged))
            put_portable_claims(state, portable_claims(staged))
            put_portable_jobs(state,portable_jobs(staged))
            stamp = await asyncio.to_thread(FileStamp.of, path)
            summary = summary_from_state(state, stamp)
            try:
                await self._upsert_row(summary)
            except Exception:
                logger.warning("正文已提交，派生索引稍后重建: %s", sid, exc_info=True)
            return summary

    async def commit_state(self, state: SessionState, memory_watermark=None, **kwargs):
        """CAS commit for application commands; failed CAS never restores a head."""
        return await self.save_state(state, memory_watermark, **kwargs)

    async def _commit_sqlite(self, state, memory_watermark, **kwargs):
        staged = state.model_copy(deep=True)
        if staged.schema_version >= 3:
            record_persisted_head(staged, memory_watermark)
            staged.meta.branch_revision += 1
        now = time.time()
        summary = summary_from_state(staged, FileStamp(now, int(now * 1e9), 0))
        await asyncio.to_thread(self.story_db.write, staged, summary.model_dump(mode='json'), **kwargs)
        state.meta.branch_revision = staged.meta.branch_revision
        state.state_revisions = staged.state_revisions
        state.head_state_revision_id = staged.head_state_revision_id
        put_portable_receipts(state, portable_receipts(staged))
        put_portable_claims(state, portable_claims(staged))
        put_portable_jobs(state,portable_jobs(staged))
        # No full JSON mirror: path_for is a frozen legacy source or explicit export.
        return summary

    async def lookup_command(self, branch_id, operation_id):
        self.path_for(branch_id)  # Identity/path validation before either route.
        if await asyncio.to_thread(self.story_db.owns, branch_id):
            return await asyncio.to_thread(self.story_db.lookup_command, branch_id, operation_id)
        state = await self.load_state_readonly(branch_id)
        receipt = portable_receipts(state).get(operation_id) if state is not None else None
        return public_receipt(receipt) if receipt is not None else None

    async def reserve_generation(self, branch_id, operation_id, fingerprint):
        if not all(isinstance(value, str) and value for value in (operation_id, fingerprint)):
            raise CommandConflict('生成操作身份格式无效')
        async with self._lock(branch_id):
            if await asyncio.to_thread(self.story_db.owns, branch_id):
                return await asyncio.to_thread(self.story_db.reserve_generation, branch_id, operation_id, fingerprint)
            path = self.path_for(branch_id)
            state = await asyncio.to_thread(self._load_state_readonly_sync, path)
            if operation_id in portable_jobs(state): raise CommandConflict('操作ID已被记忆任务协议占用')
            entries = dict(portable_claims(state))
            if operation_id in entries:
                if entries[operation_id] != fingerprint:
                    raise CommandConflict('生成操作 ID 已用于不同请求')
                raise GenerationIncomplete('生成尝试已经启动；完整结果未确认，不能自动重复模型调用')
            entries[operation_id] = fingerprint
            put_portable_claims(state, entries)
            await asyncio.to_thread(self._write_legacy_guarded,path,state)

    async def lookup_generation(self, branch_id, operation_id):
        if await asyncio.to_thread(self.story_db.owns, branch_id):
            return await asyncio.to_thread(self.story_db.lookup_generation, branch_id, operation_id)
        state = await self.load_state_readonly(branch_id)
        fingerprint = portable_claims(state).get(operation_id) if state is not None else None
        return fingerprint

    async def lookup_memory_job(self, branch_id, operation_id):
        def lookup():
            with self.story_db.connect() as connection:
                row = connection.execute('SELECT fingerprint,status,plan,result,version FROM memory_jobs WHERE branch_id=? AND operation_id=?', (branch_id, operation_id)).fetchone()
                if row is None:
                    return None
                if row[4] != 1:
                    raise CommandConflict('记忆任务版本不支持')
                return {'fingerprint':row[0], 'status':row[1], 'plan':json.loads(row[2]),
                        'result':json.loads(row[3]) if row[3] is not None else None}
        return await asyncio.to_thread(lookup)

    async def claim_memory_job(self, branch_id, operation_id, fingerprint, plan):
        def claim():
            with self.story_db.transaction() as connection:
                branchrow=connection.execute('SELECT story_id,active FROM branches WHERE id=?',(branch_id,)).fetchone()
                if branchrow is None or branchrow[1] != 1: raise CommandConflict('记忆任务目标不可见')
                self.story_db.require_lifecycle_access(connection,branchrow[0])
                for table in ('generation_claims','command_receipts'):
                    claimed=connection.execute(f'SELECT fingerprint FROM {table} WHERE branch_id=? AND operation_id=?',(branch_id,operation_id)).fetchone()
                    if claimed:
                        raise CommandConflict('操作ID已被其他命令协议占用')
                row = connection.execute('SELECT fingerprint,status,plan,result,version FROM memory_jobs WHERE branch_id=? AND operation_id=?', (branch_id, operation_id)).fetchone()
                if row:
                    if row[0] != fingerprint or row[4] != 1:
                        raise CommandConflict('记忆任务 ID 已用于不同请求')
                    return {'fingerprint':row[0], 'status':row[1], 'plan':json.loads(row[2]),
                        'result':json.loads(row[3]) if row[3] is not None else None, 'new':False}
                connection.execute('INSERT INTO memory_jobs VALUES(?,?,?,?,?,?,1)',
                    (branch_id, operation_id, fingerprint, 'pending', json.dumps(plan, ensure_ascii=False, allow_nan=False), None))
                return {'fingerprint':fingerprint, 'status':'pending', 'plan':plan, 'result':None, 'new':True}
        self.path_for(branch_id)
        if await self.load_state_readonly(branch_id) is None:
            raise CommandConflict('记忆任务目标不存在或已删除')
        return await asyncio.to_thread(claim)

    async def lookup_restore_command(self, operation_id, save_id):
        """Locate an existing receipt without requiring its immutable save source.

        This is an index over the authoritative branch receipts, not another
        receipt ledger. Ambiguous identities are rejected rather than guessed.
        """
        import hashlib
        fingerprint = hashlib.sha256(json.dumps({'action':'save.restore', 'payload':{'save_id':save_id}},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        def sql_candidates():
            with self.story_db.connect() as connection:
                return [row[0] for row in connection.execute('SELECT r.branch_id FROM command_receipts r JOIN branches b ON b.id=r.branch_id WHERE r.operation_id=? AND b.active=1', (operation_id,))]
        candidates = await asyncio.to_thread(sql_candidates)
        for summary in await self.list_summaries():
            if not await asyncio.to_thread(self.story_db.owns, summary.id):
                state = await self.load_state_readonly(summary.id)
                if state is not None and operation_id in portable_receipts(state):
                    candidates.append(summary.id)
        matched = []
        for branch in set(candidates):
            receipt = await self.lookup_command(branch, operation_id)
            if receipt is not None and receipt['fingerprint'] == fingerprint:
                matched.append(receipt)
        if len(matched) > 1:
            raise CommandConflict('恢复操作身份不唯一，拒绝猜测目标路线')
        if matched:
            return matched[0]
        if candidates:
            raise CommandConflict('操作 ID 已用于不同的恢复请求')
        return None

    async def lookup_command_scope(self, operation_id, fingerprint):
        def sql_candidates():
            with self.story_db.connect() as connection:
                return [row[0] for row in connection.execute('SELECT r.branch_id FROM command_receipts r JOIN branches b ON b.id=r.branch_id WHERE r.operation_id=? AND b.active=1', (operation_id,))]
        def purged_candidates():
            from .lifecycle_repo import LifecycleConflict
            with self.story_db.connect() as connection:
                rows=connection.execute('SELECT r.fingerprint FROM command_receipts r JOIN branch_purges p ON p.branch_id=r.branch_id WHERE r.operation_id=?',(operation_id,)).fetchall()
                return [row[0] for row in rows]
        purged = await asyncio.to_thread(purged_candidates)
        candidates = set(await asyncio.to_thread(sql_candidates))
        for summary in await self.list_summaries():
            if not await asyncio.to_thread(self.story_db.owns, summary.id):
                state = await self.load_state_readonly(summary.id)
                if state is not None and operation_id in portable_receipts(state):
                    candidates.add(summary.id)
        matches = []
        for branch in candidates:
            receipt = await self.lookup_command(branch, operation_id)
            if receipt and receipt['fingerprint'] == fingerprint:
                matches.append({'branch_id':branch, **receipt})
        if len(matches) > 1 or (matches and fingerprint in purged):
            raise CommandConflict('操作身份不唯一，拒绝猜测目标路线')
        if matches:
            return matches[0]
        if fingerprint in purged:
            from .lifecycle_repo import LifecycleConflict
            raise LifecycleConflict('命令结果已永久清除','operation_result_purged',410)
        if candidates or purged:
            raise CommandConflict('操作 ID 已用于不同请求')
        return None

    def recover_memory_jobs(self):
        with self.story_db.transaction() as connection:
            connection.execute("UPDATE memory_jobs SET status='failed',result=? WHERE status='pending'",
                (json.dumps({'code':'operation_incomplete', 'message':'记忆任务在重启前未确认完整提交，不能自动重复模型调用'}, ensure_ascii=False),))

    def exists_sync(self, session_id):
        if self.story_db.owns(session_id):
            return True  # Deleted identities remain occupied; restoration is explicit.
        return self.path_for(session_id).exists()

    def load_state_readonly_sync(self, session_id):
        if self.story_db.owns(session_id):
            return self.story_db.load(session_id)
        path = self.path_for(session_id)
        return self._load_state_readonly_sync(path) if path.exists() else None

    async def restore_state(self, state: SessionState) -> None:
        """Compensate a failed write without inventing a new branch revision."""
        async with self._lock(state.meta.id):
            path = self.path_for(state.meta.id)
            if await asyncio.to_thread(self.story_db.owns, state.meta.id):
                # Compensation can only restore the head it directly replaced.
                summary = summary_from_state(state, FileStamp(time.time(), time.time_ns(), 0))
                await asyncio.to_thread(self.story_db.restore, state, summary.model_dump(mode='json'))
                return
            restored = state.model_copy(deep=True)
            if path.exists():
                current = await asyncio.to_thread(self._load_state_readonly_sync, path)
                if current is not None:
                    merge_claims(restored, current)
            await asyncio.to_thread(write_json_atomic, path, legacy_document(restored), indent=None, fsync=True)
            stamp = await asyncio.to_thread(FileStamp.of, path)
            await self._upsert_row(summary_from_state(state, stamp))

    async def delete_state(self, session_id: str) -> bool:
        """删正文 + 删索引行；返回正文此前是否存在。"""
        try:
            path = self.path_for(session_id)
        except ValueError:
            return False
        async with self._lock(session_id):
            if await asyncio.to_thread(self.story_db.owns, session_id):
                # A frozen migration source is retained and must not resurrect.
                existed = await asyncio.to_thread(self.story_db.delete, session_id)
                return existed
            existed = await asyncio.to_thread(unlink_missing_ok, path)
            async with self._index_lock:
                rows = await asyncio.to_thread(self._rows_for_write)
                if rows.pop(session_id, None) is not None:
                    await asyncio.to_thread(self._write_index_sync, rows)
                    self._rows = rows
            return existed

    # ---------- 同步实现（单次 to_thread 内跑，避免多次线程跳转） ----------

    def _load_state_sync(self, path: Path) -> SessionState:
        state, _ = self._load_state_with_migration(path)
        return state

    def _load_state_with_migration(self, path: Path) -> tuple[SessionState, bool]:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text)
        migrated, changed = migrate_state_to_v3_dict(unwrap_legacy_document(raw))
        validated = validate_state(migrated)
        if changed:
            self._write_backup(path, raw_schema_version(raw), text)
            write_json_atomic(path, legacy_document(validated), indent=None)
        return validated, changed

    def _write_backup(self, path: Path, version: int, text: str) -> None:
        """版本化备份（`{name}.v{version}.bak`），永不覆盖第一份。"""
        bak = path.with_suffix(path.suffix + f".v{version}.bak")
        if not bak.exists():
            write_text_atomic(bak, text)

    def _summary_from_text(self, path: Path, text: str | None, stamp: FileStamp) -> SessionSummary | None:
        if text is None:
            logger.warning("会话文件读取失败，索引行丢弃: %s", path)
            return None
        try:
            raw = json.loads(text)
            return summary_from_state(validate_state(raw), stamp)
        except Exception:  # noqa: BLE001 —— 坏文件不进列表
            logger.warning("会话解析失败，索引行丢弃: %s", path, exc_info=True)
            return None

    # ---------- 索引 ----------

    def _read_index_sync(self) -> dict[str, SessionSummary] | None:
        raw = read_json(self.paths.sessions_index_file)
        if not isinstance(raw, dict) or raw.get("index_schema") != INDEX_SCHEMA:
            return None
        rows = raw.get("sessions")
        if not isinstance(rows, dict):
            return None
        out: dict[str, SessionSummary] = {}
        for sid, row in rows.items():
            try:
                out[str(sid)] = SessionSummary.model_validate(row)
            except Exception:  # noqa: BLE001 —— 单行坏 → 整表判不可信，回退重建
                logger.warning("索引行损坏，触发重建: %s", sid)
                return None
        return out

    def _write_index_sync(self, rows: dict[str, SessionSummary]) -> None:
        # exclude_defaults：索引是缓存，行内只留实际值（cephfs 上索引体积≈列表/保存延迟）
        payload = {
            "index_schema": INDEX_SCHEMA,
            # 兼容守卫：旧 app.py 迁移器见 >=2 即跳过，绝不改写本文件（见模块 docstring）
            "schema_version": _INDEX_COMPAT_SCHEMA,
            "sessions": {
                sid: s.model_dump(mode="json", exclude_defaults=True) for sid, s in rows.items()
            },
        }
        write_json_atomic(
            self.paths.sessions_index_file, payload, indent=None, separators=(",", ":")
        )

    def _rows_for_write(self) -> dict[str, SessionSummary]:
        """写索引前的基础行：优先内存快照（最新），否则读盘（可能为空）。"""
        if self._rows is not None:
            return dict(self._rows)
        return dict(self._read_index_sync() or {})

    async def _upsert_row(self, summary: SessionSummary) -> None:
        async with self._index_lock:
            rows = await asyncio.to_thread(self._rows_for_write)
            rows[summary.id] = summary
            await asyncio.to_thread(self._write_index_sync, rows)
            self._rows = rows
