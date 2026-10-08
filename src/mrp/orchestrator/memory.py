"""mrp 记忆系统（R5.2 记忆 / R5.3 防误写）。

三层结构：
- SQLite 主存（WAL + busy_timeout，cephfs 保守锁策略）：
  memory_records 主表 + FTS5 全文（trigram 分词，中文子串可召回）
  + sqlite-vec 向量虚表（混合检索）
- 人类可读 JSONL 镜像：mirror_dir/<character_id>/records.jsonl，
  一行一条（id/kind/turn_range/content），用户手改后 reload_mirrors() 回写
- 混合检索：0.6×向量（sqlite-vec KNN）+ 0.4×BM25（FTS5），
  各自 min-max 归一化后加权融合；sqlite-vec 不可用时降级纯 BM25（权重 1.0）

sqlite-vec 0.1.9 实测 API 注记：
- 虚表 DDL 支持 metadata 列：vec0(embedding float[N], record_id TEXT, character_id TEXT)
- KNN 语法（metadata 过滤可与 KNN 组合，参数绑定均可）：
    SELECT record_id, distance FROM memory_vec
    WHERE embedding MATCH ? AND k = ? AND character_id = ?
  embedding 参数传 float32 blob（struct.pack("<Nf", *vec)）；distance 为欧氏距离
- vec0 不自增 rowid，插入必须显式给 rowid（本实现复用 memory_records 的 rowid）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import httpx

from mrp.shared.models import MemoryRecord, SessionState, new_id

# ---------------------------------------------------------------- 嵌入后端


class EmbeddingProvider(Protocol):
    """嵌入后端协议：生产 OpenAICompatEmbeddings，测试 HashEmbedding。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAICompatEmbeddings:
    """OpenAI 兼容 /embeddings 端点客户端（api_key 只从环境变量取）。"""

    def __init__(self, base_url: str, api_key_env: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        api_key = os.environ[self.api_key_env]  # 未配置环境变量即 KeyError，显式失败
        resp = httpx.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": self.model, "input": texts},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]


class HashEmbedding:
    """确定性本地嵌入（测试/离线，无网络）。

    对文本分词（ASCII 按词、CJK 逐字、统一小写），每个 token 用
    sha256("{token}:{dim}") 前 4 字节映射为 [0,1) 加到对应维度，
    最后 L2 归一化。性质：同文本必同向量；token 重合越多余弦距离越近。
    """

    def __init__(self, dim: int = 64):
        self.dim = dim

    def _tokens(self, text: str) -> list[str]:
        out: list[str] = []
        buf: list[str] = []
        for ch in text.lower():
            if "\u4e00" <= ch <= "\u9fff":  # CJK 基本区逐字
                if buf:
                    out.append("".join(buf))
                    buf = []
                out.append(ch)
            elif ch.isalnum():
                buf.append(ch)
            else:
                if buf:
                    out.append("".join(buf))
                    buf = []
        if buf:
            out.append("".join(buf))
        return out

    def embed(self, texts: list[str]) -> list[list[float]]:
        result: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in self._tokens(text):
                for i in range(self.dim):
                    h = hashlib.sha256(f"{tok}:{i}".encode("utf-8")).digest()
                    vec[i] += int.from_bytes(h[:4], "big") / 2**32
            norm = math.sqrt(sum(x * x for x in vec))
            if norm == 0.0:
                vec[0] = 1.0
            else:
                vec = [x / norm for x in vec]
            result.append(vec)
        return result


def _pack_f32(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


# ---------------------------------------------------------------- 存储


MEMORY_DETAILS = {
    "category", "participant_ids", "source_fingerprints", "evidence", "important",
    "revision", "manually_revised", "source_changed", "revisions", "matter_status", "supersedes",
}


class MemoryHistoryUnavailable(ValueError):
    """The requested checkpoint predates retained memory history."""


class MemoryStore:
    """角色记忆存储：SQLite 三表 + JSONL 人类可读镜像（R5.2）。
    R36：结构化字段（participants/keywords/importance/scene_id）additive 列，
    存量库经 ALTER 迁移；检索支持新近度+重要性加权（纯 FTS，向量分支维持禁用）。"""

    _RECORD_COLS = (
        "id, character_id, session_id, turn_start, turn_end, kind, "
        "content, source_message_ids, created_at, "
        "participants, keywords, importance, scene_id, "
        "effective_message_id, commit_seq, inherited_from_id, invalidated, details"
    )

    # R36.2 检索三元权重（design/v3/m9-r36 §3）：相关度/新近度/重要性
    MEMORY_W_REL = 0.6
    MEMORY_W_REC = 0.25
    MEMORY_W_IMP = 0.15
    MEMORY_RECENCY_HALFLIFE = 15  # 回合半衰期
    MEMORY_CROSS_SESSION_RECENCY = 0.2  # 跨会话记录的固定新近度（可被高相关抬回）

    def __init__(
        self,
        db_path: Path,
        mirror_dir: Path,
        embedding: EmbeddingProvider | None,
        vec_dim: int = 64,
    ):
        self.db_path = Path(db_path)
        self.mirror_dir = Path(mirror_dir)
        self.embedding = embedding
        self.vec_dim = vec_dim
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + 进程内锁：FastAPI/uvicorn 可能跨线程使用 store；
        # 设计本就是单写者（ADR/计划 §7），锁保证串行化，WAL 保证 cephfs 保守语义
        self._lock = __import__("threading").RLock()
        # 镜像目录创建缓存（W5-C11）：cephfs 上每条记录都 mkdir 代价高，首个 append 后记住
        self._mirror_ready: set[str] = set()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")  # cephfs 保守策略
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.vector_enabled = False
        ext_loaded = False
        try:
            import sqlite_vec

            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            ext_loaded = True
        except Exception:
            ext_loaded = False
        # 降级判定 = 扩展可用 且 注入了 embedding，二者缺一即纯 FTS5
        self.vector_enabled = ext_loaded and self.embedding is not None
        self._create_tables(ext_loaded)

    def _create_tables(self, ext_loaded: bool) -> None:
        old_columns = {r[1] for r in self.conn.execute("PRAGMA table_info(memory_records)")}
        if old_columns and "details" not in old_columns:
            # SQLite backup includes committed WAL pages; a file copy does not.
            backup = self.db_path.with_name(self.db_path.name + ".before-important-memory.bak")
            if not backup.exists():
                with sqlite3.connect(backup) as target:
                    self.conn.backup(target)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_records (
                id TEXT PRIMARY KEY,
                character_id TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                turn_start INTEGER NOT NULL DEFAULT 0,
                turn_end INTEGER NOT NULL DEFAULT 0,
                kind TEXT NOT NULL DEFAULT 'summary',
                content TEXT NOT NULL,
                source_message_ids TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                participants TEXT NOT NULL DEFAULT '[]',
                keywords TEXT NOT NULL DEFAULT '[]',
                importance INTEGER NOT NULL DEFAULT 3,
                scene_id TEXT NOT NULL DEFAULT '',
                effective_message_id TEXT NOT NULL DEFAULT '',
                commit_seq INTEGER NOT NULL DEFAULT 0,
                inherited_from_id TEXT NOT NULL DEFAULT '',
                invalidated INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # 存量库 additive 迁移（R36）：缺列补列，幂等
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(memory_records)")}
        for col, decl in (
            ("participants", "TEXT NOT NULL DEFAULT '[]'"),
            ("keywords", "TEXT NOT NULL DEFAULT '[]'"),
            ("importance", "INTEGER NOT NULL DEFAULT 3"),
            ("scene_id", "TEXT NOT NULL DEFAULT ''"),
            ("effective_message_id", "TEXT NOT NULL DEFAULT ''"),
            ("commit_seq", "INTEGER NOT NULL DEFAULT 0"),
            ("inherited_from_id", "TEXT NOT NULL DEFAULT ''"),
            ("invalidated", "INTEGER NOT NULL DEFAULT 0"),
            ("details", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            if col not in existing:
                self.conn.execute(f"ALTER TABLE memory_records ADD COLUMN {col} {decl}")
        # trigram 分词：默认 unicode61 会把整段连续中文当一个 token，
        # 中文关键词无法召回；trigram 支持子串匹配且 bm25 排序正常
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
            "content, character_id UNINDEXED, tokenize='trigram')"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_branch_clock ("
            "session_id TEXT PRIMARY KEY, last_seq INTEGER NOT NULL)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS memory_branch_commit_idx "
            "ON memory_records(session_id, commit_seq)"
        )
        self.conn.execute("""CREATE TABLE IF NOT EXISTS memory_windows (
            session_id TEXT NOT NULL, character_id TEXT NOT NULL,
            turn_start INTEGER NOT NULL, turn_end INTEGER NOT NULL,
            status TEXT NOT NULL, fingerprints TEXT NOT NULL DEFAULT '{}',
            record_ids TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(session_id, character_id, turn_start, turn_end))""")
        self.conn.execute("CREATE TABLE IF NOT EXISTS memory_generation_recovery ("
                          "session_id TEXT NOT NULL, operation_id TEXT NOT NULL, snapshot TEXT NOT NULL, "
                          "PRIMARY KEY(session_id, operation_id))")
        # Recover clocks before backfilling rows in a partially upgraded DB.
        for branch_id, maximum in self.conn.execute(
            "SELECT session_id, MAX(commit_seq) FROM memory_records "
            "GROUP BY session_id"
        ).fetchall():
            self.conn.execute(
                "INSERT INTO memory_branch_clock(session_id, last_seq) VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "last_seq=MAX(last_seq, excluded.last_seq)",
                (branch_id, int(maximum or 0)),
            )
        # Old rows receive deterministic branch-local sequence numbers once.
        # This happens before any new memory write and preserves existing IDs.
        legacy_rows = self.conn.execute(
            "SELECT rowid, session_id, source_message_ids FROM memory_records "
            "WHERE commit_seq=0 ORDER BY rowid"
        ).fetchall()
        for rowid, branch_id, sources_json in legacy_rows:
            next_seq = self._allocate_commit_seq(branch_id)
            try:
                sources = json.loads(sources_json or "[]")
            except (TypeError, ValueError):
                sources = []
            effective = sources[-1] if sources else ""
            self.conn.execute(
                "UPDATE memory_records SET commit_seq=?, effective_message_id=? "
                "WHERE rowid=?",
                (next_seq, effective, rowid),
            )
        if ext_loaded:
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0("
                f"embedding float[{int(self.vec_dim)}], record_id TEXT, character_id TEXT)"
            )
            try:
                self.conn.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec_branch USING vec0("
                    f"embedding float[{int(self.vec_dim)}], record_id TEXT, "
                    "character_id TEXT, session_id TEXT)"
                )
                self.branch_vector_enabled = self.vector_enabled
            except sqlite3.Error:
                self.branch_vector_enabled = False
        else:
            self.branch_vector_enabled = False
        self._create_history()
        self.conn.commit()

    def _create_history(self) -> None:
        """Keep temporal versions independently of the mutable recall tables.

        SQL triggers cover every existing mutation path, including mirror reload
        and recovery. Existing databases declare a floor rather than inventing
        versions for checkpoints that can no longer be reconstructed.
        """
        self.conn.execute("CREATE TABLE IF NOT EXISTS memory_history_floor (session_id TEXT PRIMARY KEY, first_seq INTEGER NOT NULL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS memory_history AS SELECT *, commit_seq AS valid_from, CAST(NULL AS INTEGER) AS valid_until FROM memory_records WHERE 0")
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS memory_history_version ON memory_history(session_id,id,valid_from)")
        self.conn.execute("INSERT OR IGNORE INTO memory_history_floor SELECT session_id,last_seq FROM memory_branch_clock")
        cols = self._RECORD_COLS
        self.conn.execute(f"INSERT INTO memory_history ({cols},valid_from,valid_until) SELECT {cols}, (SELECT first_seq FROM memory_history_floor f WHERE f.session_id=r.session_id), NULL FROM memory_records r WHERE NOT EXISTS (SELECT 1 FROM memory_history h WHERE h.session_id=r.session_id AND h.id=r.id)")
        names = [name.strip() for name in cols.split(',')]
        new_values = ','.join('NEW.' + name for name in names)
        # A delete or in-place change is itself a branch commit. The old version
        # remains valid before that commit; inserts retain their allocated clock.
        advance = "UPDATE memory_branch_clock SET last_seq=last_seq+1 WHERE session_id=OLD.session_id;"
        close = "UPDATE memory_history SET valid_until=(SELECT last_seq FROM memory_branch_clock WHERE session_id=OLD.session_id) WHERE session_id=OLD.session_id AND id=OLD.id AND valid_until IS NULL;"
        self.conn.executescript(f"""
        CREATE TRIGGER IF NOT EXISTS memory_history_insert AFTER INSERT ON memory_records BEGIN
            INSERT OR IGNORE INTO memory_history_floor VALUES(NEW.session_id,0);
            INSERT INTO memory_history ({cols},valid_from,valid_until) VALUES({new_values},MAX(NEW.commit_seq,COALESCE((SELECT last_seq FROM memory_branch_clock WHERE session_id=NEW.session_id),0)),NULL);
        END;
        CREATE TRIGGER IF NOT EXISTS memory_history_delete BEFORE DELETE ON memory_records BEGIN
            {advance} {close}
        END;
        CREATE TRIGGER IF NOT EXISTS memory_history_update AFTER UPDATE ON memory_records BEGIN
            {advance} {close}
            INSERT INTO memory_history ({cols},valid_from,valid_until) VALUES({new_values},(SELECT last_seq FROM memory_branch_clock WHERE session_id=NEW.session_id),NULL);
        END;
        """)

    def history_at(self, branch_id: str, watermark: int) -> list[MemoryRecord]:
        """Return historical versions; missing old history is an explicit error."""
        with self._lock:
            floor = self.conn.execute("SELECT first_seq FROM memory_history_floor WHERE session_id=?", (branch_id,)).fetchone()
            if floor and watermark < floor[0]:
                raise MemoryHistoryUnavailable("该节点早于保留的记忆历史，不能保证安全分叉")
            rows = self.conn.execute(f"SELECT {self._RECORD_COLS} FROM memory_history WHERE session_id=? AND valid_from<=? AND (valid_until IS NULL OR valid_until>?) AND invalidated=0 ORDER BY commit_seq,id", (branch_id, watermark, watermark)).fetchall()
            return [self._row_to_record(row) for row in rows]

    def history_status(self, branch_id: str, watermark: int | None) -> dict:
        with self._lock:
            floor = self.conn.execute("SELECT first_seq FROM memory_history_floor WHERE session_id=?", (branch_id,)).fetchone()
            complete = watermark is not None and (floor is None or watermark >= floor[0])
            return {'history_complete': complete,
                    'history_warning': '' if complete else '该节点缺少可靠历史记忆；新分支将不继承未验证记忆。',
                    'history_floor': int(floor[0]) if floor else 0}

    def export_history(self, branch_id):
        with self._lock:
            floor = self.conn.execute('SELECT first_seq FROM memory_history_floor WHERE session_id=?', (branch_id,)).fetchone()
            rows = self.conn.execute(f'SELECT {self._RECORD_COLS},valid_from,valid_until FROM memory_history WHERE session_id=? ORDER BY valid_from,id', (branch_id,)).fetchall()
            clock = self.conn.execute('SELECT last_seq FROM memory_branch_clock WHERE session_id=?', (branch_id,)).fetchone()
            return {'floor': floor[0] if floor else 0, 'clock': clock[0] if clock else 0, 'versions': [
                {'record': self._row_to_record(row[:18]).model_dump(mode='json'),
                 'valid_from': row[18], 'valid_until': row[19]} for row in rows]}

    def import_history(self, branch_id, payload):
        """Restore validated archive versions after branch records are imported."""
        with self._lock:
            try:
                self.conn.execute('DELETE FROM memory_history WHERE session_id=?', (branch_id,))
                floor = int(payload['floor'])
                if floor < 0:
                    raise ValueError('Invalid history floor')
                for version in payload['versions']:
                    record = MemoryRecord.model_validate(version['record'])
                    start, end = int(version['valid_from']), version['valid_until']
                    if record.session_id != branch_id or start < floor or (end is not None and int(end) <= start):
                        raise ValueError('Invalid historical memory interval')
                    values = (record.id, record.character_id, branch_id, record.turn_start, record.turn_end,
                              record.kind, record.content, json.dumps(record.source_message_ids), record.created_at.isoformat(),
                              json.dumps(record.participants), json.dumps(record.keywords), record.importance, record.scene_id,
                              record.effective_message_id or '', record.commit_seq, record.inherited_from_id or '',
                              int(record.invalidated), json.dumps(record.model_dump(mode='json', include=MEMORY_DETAILS)))
                    self.conn.execute(f"INSERT INTO memory_history ({self._RECORD_COLS},valid_from,valid_until) VALUES ({','.join('?' for _ in range(20))})", (*values, start, end))
                self.conn.execute('INSERT INTO memory_history_floor VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET first_seq=excluded.first_seq', (branch_id, floor))
                self.conn.execute('INSERT INTO memory_branch_clock VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET last_seq=MAX(last_seq,excluded.last_seq)', (branch_id, int(payload.get('clock', floor))))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def declare_snapshot_history(self, branch_id, watermark):
        """An old archive proves its head snapshot, not missing prior versions."""
        with self._lock:
            self.conn.execute('INSERT INTO memory_history_floor VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET first_seq=excluded.first_seq', (branch_id, watermark))
            self.conn.execute('UPDATE memory_history SET valid_from=MAX(valid_from,?) WHERE session_id=?', (watermark, branch_id))
            self.conn.commit()

    def _allocate_commit_seq(self, session_id: str) -> int:
        """Allocate a branch-local sequence under the store's process lock."""
        row = self.conn.execute(
            "SELECT last_seq FROM memory_branch_clock WHERE session_id=?",
            (session_id,),
        ).fetchone()
        seq = int(row[0]) + 1 if row else 1
        self.conn.execute(
            "INSERT INTO memory_branch_clock(session_id, last_seq) VALUES (?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET last_seq=excluded.last_seq",
            (session_id, seq),
        )
        return seq

    def current_watermark(self, session_id: str) -> int:
        """Latest committed sequence for one branch (0 when it has no memory)."""
        self._check_open()
        with self._lock:
            row = self.conn.execute(
                "SELECT last_seq FROM memory_branch_clock WHERE session_id=?",
                (session_id,),
            ).fetchone()
            return int(row[0]) if row else 0

    def import_branch_records(self, branch_id: str, records: list[MemoryRecord], watermark: int) -> None:
        """Restore a fresh branch's records and its original checkpoint clock."""
        self._check_open()
        with self._lock:
            if self.conn.execute(
                "SELECT 1 FROM memory_records WHERE session_id=? LIMIT 1", (branch_id,)
            ).fetchone():
                raise ValueError("目标路线已有记忆，不能覆盖")
            touched: set[str] = set()
            try:
                for record in records:
                    if record.session_id != branch_id or record.commit_seq <= 0:
                        raise ValueError("记忆分支或提交序号无效")
                    self._insert_db(record)
                    touched.add(record.character_id)
                final_clock = max(watermark, *(r.commit_seq for r in records), 0)
                self.conn.execute(
                    "INSERT INTO memory_branch_clock(session_id,last_seq) VALUES (?,?) "
                    "ON CONFLICT(session_id) DO UPDATE SET last_seq=max(last_seq,excluded.last_seq)",
                    (branch_id, final_clock),
                )
                for character_id in touched:
                    self._rewrite_mirror(character_id)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def snapshot_at(
        self, branch_id: str, watermark: int, prefix_messages: list,
    ) -> list[MemoryRecord]:
        """Immutable save payload of records effective at a branch checkpoint."""
        self._check_open()
        by_id = {message.id: message for message in prefix_messages}
        with self._lock:
            records = self.history_at(branch_id, watermark)
            result = []
            for record in records:
                if record.effective_message_id and record.effective_message_id not in by_id:
                    continue
                if record.effective_message_id and not by_id[record.effective_message_id].can_see(record.character_id):
                    continue
                if any(mid not in by_id or not by_id[mid].can_see(record.character_id)
                       for mid in record.source_message_ids):
                    continue
                result.append(record.model_copy(deep=True))
            return result

    def copy_at_fork(
        self, parent_id: str, child_id: str, watermark: int,
        prefix_messages: list,
    ) -> list[MemoryRecord]:
        """Copy only facts committed and effective at the requested anchor.

        All source messages must be in the prefix and visible to the memory's
        character. Unanchored manual records stay on the parent branch.
        """
        self._check_open()
        by_id = {message.id: message for message in prefix_messages}
        with self._lock:
            records = self.history_at(parent_id, watermark)
            copied: list[MemoryRecord] = []
            id_map: dict[str, str] = {}
            touched: set[str] = set()
            try:
                for source in records:
                    if (
                        not source.effective_message_id
                        or source.effective_message_id not in by_id
                        or not by_id[source.effective_message_id].can_see(source.character_id)
                    ):
                        continue
                    if any(
                        message_id not in by_id
                        or not by_id[message_id].can_see(source.character_id)
                        for message_id in source.source_message_ids
                    ):
                        continue
                    clone = source.model_copy(deep=True)
                    clone.id = new_id("mem")
                    clone.session_id = child_id
                    # Keep ancestor sequence values. The child clock is raised
                    # to the anchor watermark below, so future child writes
                    # cannot appear to exist at a copied ancestor checkpoint.
                    clone.commit_seq = source.commit_seq
                    clone.inherited_from_id = source.inherited_from_id or source.id
                    id_map[source.id] = clone.id
                    copied.append(clone)
                    touched.add(clone.character_id)
                # Link states within the child branch, rather than to parent memory IDs.
                for clone in copied:
                    clone.supersedes = [id_map[mid] for mid in clone.supersedes if mid in id_map]
                    self._insert_db(clone)
                self._copy_history_prefix(parent_id, child_id, watermark, by_id, id_map)
                self.conn.execute(
                    "INSERT INTO memory_branch_clock(session_id, last_seq) VALUES (?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET "
                    "last_seq=MAX(last_seq, excluded.last_seq)",
                    (child_id, watermark),
                )
                for clone in copied:
                    self._append_mirror(clone)
                self.conn.commit()
                return copied
            except Exception:
                self.conn.rollback()
                for character_id in touched:
                    self._rewrite_mirror(character_id)
                raise

    def _copy_history_prefix(self, parent_id, child_id, watermark, messages, id_map):
        rows = self.conn.execute(f"SELECT {self._RECORD_COLS},valid_from,valid_until FROM memory_history WHERE session_id=? AND valid_from<=? ORDER BY valid_from,id", (parent_id, watermark)).fetchall()
        eligible = []
        for row in rows:
            record = self._row_to_record(row[:18])
            evidence = set(record.source_message_ids) | {record.effective_message_id}
            if not record.effective_message_id or any(mid not in messages or not messages[mid].can_see(record.character_id) for mid in evidence):
                continue
            id_map.setdefault(record.id, new_id('mem'))
            eligible.append(row)
        self.conn.execute('DELETE FROM memory_history WHERE session_id=?', (child_id,))
        for row in eligible:
            values = list(row)
            source_id = values[0]
            values[0], values[2] = id_map[source_id], child_id
            values[15] = values[15] or source_id
            details = json.loads(values[17] or '{}')
            details['supersedes'] = [id_map[mid] for mid in details.get('supersedes', []) if mid in id_map]
            values[17] = json.dumps(details, ensure_ascii=False)
            if values[19] is not None and values[19] > watermark:
                values[19] = None
            self.conn.execute(f"INSERT INTO memory_history ({self._RECORD_COLS},valid_from,valid_until) VALUES ({','.join('?' for _ in range(20))})", values)
        floor = self.conn.execute('SELECT first_seq FROM memory_history_floor WHERE session_id=?', (parent_id,)).fetchone()
        self.conn.execute('INSERT INTO memory_history_floor VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET first_seq=excluded.first_seq', (child_id, floor[0] if floor else 0))

    def purge_branch(self, branch_id: str) -> int:
        """Remove an uncommitted child branch's copied memory during rollback."""
        self._check_open()
        with self._lock:
            rows = self.conn.execute(
                "SELECT rowid, character_id FROM memory_records WHERE session_id=?",
                (branch_id,),
            ).fetchall()
            for rowid, _ in rows:
                if self.vector_enabled:
                    self.conn.execute("DELETE FROM memory_vec WHERE rowid=?", (rowid,))
                if self.branch_vector_enabled:
                    self.conn.execute("DELETE FROM memory_vec_branch WHERE rowid=?", (rowid,))
                self.conn.execute("DELETE FROM memory_fts WHERE rowid=?", (rowid,))
                self.conn.execute("DELETE FROM memory_records WHERE rowid=?", (rowid,))
            self.conn.execute(
                "DELETE FROM memory_branch_clock WHERE session_id=?", (branch_id,)
            )
            self.conn.execute("DELETE FROM memory_windows WHERE session_id=?", (branch_id,))
            self.conn.execute("DELETE FROM memory_history WHERE session_id=?", (branch_id,))
            self.conn.execute("DELETE FROM memory_history_floor WHERE session_id=?", (branch_id,))
            self.conn.commit()
            for character_id in {cid for _, cid in rows}:
                self._rewrite_mirror(character_id)
            return len(rows)

    def prepare_generation_operation(self, branch_id: str, operation_id: str) -> None:
        """Durable compensation before changing validity in the separate SQLite store."""
        with self._lock:
            records = self.conn.execute(
                "SELECT id,character_id,invalidated,details,commit_seq FROM memory_records WHERE session_id=?",
                (branch_id,)).fetchall()
            windows = self.conn.execute("SELECT * FROM memory_windows WHERE session_id=?", (branch_id,)).fetchall()
            snapshot = json.dumps({"records": records, "windows": windows}, ensure_ascii=False)
            self.conn.execute("INSERT OR IGNORE INTO memory_generation_recovery VALUES(?,?,?)",
                              (branch_id, operation_id, snapshot))
            self.conn.commit()

    def finish_generation_operation(self, branch_id: str, operation_id: str, *, rollback: bool = False) -> None:
        with self._lock:
            row = self.conn.execute("SELECT snapshot FROM memory_generation_recovery WHERE session_id=? AND operation_id=?",
                                    (branch_id, operation_id)).fetchone()
            if row is None:
                return
            data = json.loads(row[0])
            if rollback:
                for saved in data["records"]:
                    record_id, _, invalidated, details = saved[:4]
                    if len(saved) >= 5:
                        # Compensation restores the pre-attempt record identity,
                        # including its watermark; branch history clock stays monotone.
                        self.conn.execute("UPDATE memory_records SET invalidated=?,details=?,commit_seq=? WHERE id=? AND session_id=?",
                                          (invalidated, details, saved[4], record_id, branch_id))
                    else:
                        # Pending journals written by earlier releases lack a watermark.
                        self.conn.execute("UPDATE memory_records SET invalidated=?,details=? WHERE id=? AND session_id=?",
                                          (invalidated, details, record_id, branch_id))
                self.conn.execute("DELETE FROM memory_windows WHERE session_id=?", (branch_id,))
                self.conn.executemany("INSERT INTO memory_windows VALUES(?,?,?,?,?,?,?,?)", data["windows"])
            self.conn.execute("DELETE FROM memory_generation_recovery WHERE session_id=? AND operation_id=?",
                              (branch_id, operation_id))
            self.conn.commit()
            if rollback:
                for cid in {r[1] for r in data["records"]}:
                    self._refresh_committed_mirror(cid)

    def recover_generation_operations(self, state) -> None:
        """JSON's committed operation marker decides crash recovery; no cross-store transaction is assumed."""
        with self._lock:
            pending = self.conn.execute("SELECT operation_id FROM memory_generation_recovery WHERE session_id=?",
                                        (state.meta.id,)).fetchall()
        for (operation_id,) in pending:
            committed = operation_id in state.generation_operations
            self.finish_generation_operation(state.meta.id, operation_id, rollback=not committed)

    @staticmethod
    def invalidate_sources_transaction(connection, branch_id: str, message_ids: set[str]) -> list[str]:
        """Pure SQL command; caller owns commit/rollback and connection locking."""
        if not message_ids:
            return []
        rows = connection.execute(
            "SELECT id, character_id, source_message_ids, kind, details, turn_start, turn_end FROM memory_records "
            "WHERE session_id=? AND invalidated=0",
            (branch_id,),
        ).fetchall()
        affected = [
            (record_id, character_id, kind, json.loads(details or "{}"), json.loads(sources or "[]"), start, end)
            for record_id, character_id, sources, kind, details, start, end in rows
            if message_ids.intersection(json.loads(sources or "[]"))
        ]
        tracked_sources = {}
        for cid, hashes in connection.execute("SELECT character_id,fingerprints FROM memory_windows WHERE session_id=?", (branch_id,)).fetchall():
            tracked_sources.setdefault(cid, set()).update(json.loads(hashes))
        for record_id, cid, kind, details, sources, start, end in affected:
            manual = kind == "manual" or details.get("manually_revised", False)
            details["source_changed"] = True
            details["revision"] = int(details.get("revision", 1)) + 1
            connection.execute(
                "UPDATE memory_records SET invalidated=?, details=?, commit_seq=? WHERE id=?",
                (0 if manual else 1, json.dumps(details, ensure_ascii=False),
                 int((connection.execute("SELECT last_seq FROM memory_branch_clock WHERE session_id=?", (branch_id,)).fetchone() or (0,))[0]) + 1, record_id),
            )
            if not manual and kind in {"episodic", "summary", "fact"} and not set(sources).issubset(tracked_sources.get(cid, set())):
                # Legacy records have no window coverage. Create their repair window explicitly,
                # so a later completed watermark can never skip this changed experience.
                hashes = {mid: details.get("source_fingerprints", {}).get(mid, "untracked") for mid in sources}
                connection.execute("INSERT INTO memory_windows(session_id,character_id,turn_start,turn_end,status,fingerprints,record_ids) VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id,character_id,turn_start,turn_end) DO UPDATE SET status='dirty'",
                                  (branch_id, cid, max(1, start), max(1, end), "dirty", json.dumps(hashes), json.dumps([record_id])))
        windows = connection.execute(
            "SELECT character_id, turn_start, turn_end, fingerprints FROM memory_windows WHERE session_id=?",
            (branch_id,),
        ).fetchall()
        for cid, start, end, hashes in windows:
            if message_ids.intersection(json.loads(hashes)):
                connection.execute("UPDATE memory_windows SET status='dirty' WHERE session_id=? AND character_id=? AND turn_start=? AND turn_end=?",
                                  (branch_id, cid, start, end))
        return [row[0] for row in affected]

    def invalidate_sources(self, branch_id: str, message_ids: set[str]) -> list[str]:
        self._check_open()
        with self._lock:
            affected = self.invalidate_sources_transaction(self.conn, branch_id, message_ids)
            self.conn.commit()
            for record_id in affected:
                row = self.conn.execute('SELECT character_id FROM memory_records WHERE id=?', (record_id,)).fetchone()
                if row:
                    self._rewrite_mirror(row[0])
            return affected

    def restore_invalidated(self, branch_id: str, record_ids: list[str]) -> None:
        """Rollback a failed regeneration's temporary source invalidation."""
        if not record_ids:
            return
        self._check_open()
        with self._lock:
            marks = ",".join("?" * len(record_ids))
            rows = self.conn.execute(
                f"SELECT DISTINCT character_id FROM memory_records "
                f"WHERE session_id=? AND id IN ({marks})",
                (branch_id, *record_ids),
            ).fetchall()
            self.conn.execute(
                f"UPDATE memory_records SET invalidated=0 "
                f"WHERE session_id=? AND id IN ({marks})",
                (branch_id, *record_ids),
            )
            for record_id, details in self.conn.execute(
                f"SELECT id, details FROM memory_records WHERE session_id=? AND id IN ({marks})",
                (branch_id, *record_ids),
            ).fetchall():
                payload = json.loads(details or "{}")
                payload["source_changed"] = False
                self.conn.execute("UPDATE memory_records SET details=? WHERE id=?", (json.dumps(payload), record_id))
            for cid, start, end, hashes, ids in self.conn.execute(
                "SELECT character_id,turn_start,turn_end,fingerprints,record_ids FROM memory_windows WHERE session_id=? AND status='dirty'", (branch_id,)
            ).fetchall():
                linked = set(json.loads(ids))
                if linked and linked.issubset(set(record_ids)) and "untracked" in json.loads(hashes).values():
                    self.conn.execute("DELETE FROM memory_windows WHERE session_id=? AND character_id=? AND turn_start=? AND turn_end=?",
                                      (branch_id, cid, start, end))
            self.conn.commit()
            for (character_id,) in rows:
                self._rewrite_mirror(character_id)

    # ---- 生命周期（W5-B26）----

    def _check_open(self) -> None:
        """关闭后调用给出明确错误（而非底层 AttributeError/SQLite 内部错误）。"""
        if self.conn is None:
            raise RuntimeError("MemoryStore 已关闭（close() 之后不可再调用）")

    def close(self) -> None:
        """关闭 SQLite 连接（幂等）。应用退出 / 容器 aclose 调用。

        WAL 模式下 close 前显式 commit，避免未落盘事务；本类不持有常驻文件句柄，
        故无其他资源需要释放。关闭后所有公开方法抛 RuntimeError。
        """
        with self._lock:
            if self.conn is None:
                return
            try:
                self.conn.commit()  # 正常路径 add/update 已 commit，此处为兜底
            except sqlite3.Error:
                pass
            self.conn.close()
            self.conn = None

    # ---- 写入 ----

    def add(self, record: MemoryRecord) -> None:
        self._check_open()
        with self._lock:
            self._insert_db(record)
            self._append_mirror(record)
            self.conn.commit()

    def add_many(self, records: list[MemoryRecord]) -> None:
        self._check_open()
        with self._lock:
            try:
                for record in records:
                    self._insert_db(record)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            for character_id in {r.character_id for r in records}:
                self._refresh_committed_mirror(character_id)

    def _insert_db(self, record: MemoryRecord) -> None:
        """写 records+fts（+vec），不 commit、不写镜像——reload 复用。
        R36：FTS 索引内容 = content + keywords（提升关键词召回）。"""
        if record.commit_seq <= 0:
            record.commit_seq = self._allocate_commit_seq(record.session_id)
        if record.effective_message_id is None and record.source_message_ids:
            record.effective_message_id = record.source_message_ids[-1]
        cur = self.conn.execute(
            f"INSERT INTO memory_records ({self._RECORD_COLS}) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.id,
                record.character_id,
                record.session_id,
                record.turn_start,
                record.turn_end,
                record.kind,
                record.content,
                json.dumps(record.source_message_ids, ensure_ascii=False),
                record.created_at.isoformat(),
                json.dumps(record.participants, ensure_ascii=False),
                json.dumps(record.keywords, ensure_ascii=False),
                record.importance,
                record.scene_id,
                record.effective_message_id or "",
                record.commit_seq,
                record.inherited_from_id or "",
                int(record.invalidated),
                json.dumps(record.model_dump(mode="json", include=MEMORY_DETAILS), ensure_ascii=False),
            ),
        )
        rowid = cur.lastrowid
        fts_content = record.content + (
            " " + " ".join(record.keywords) if record.keywords else ""
        )
        # fts/vec 与主表共用 rowid，检索结果可无损映射回 MemoryRecord
        self.conn.execute(
            "INSERT INTO memory_fts (rowid, content, character_id) VALUES (?,?,?)",
            (rowid, fts_content, record.character_id),
        )
        if self.vector_enabled:
            emb = self.embedding.embed([record.content])[0]
            if len(emb) != self.vec_dim:
                raise ValueError(
                    f"embedding 维度 {len(emb)} != 存储维度 {self.vec_dim}"
                )
            self.conn.execute(
                "INSERT INTO memory_vec (rowid, record_id, character_id, embedding) "
                "VALUES (?,?,?,?)",
                (rowid, record.id, record.character_id, _pack_f32(emb)),
            )
            if self.branch_vector_enabled:
                self.conn.execute(
                    "INSERT INTO memory_vec_branch "
                    "(rowid, record_id, character_id, session_id, embedding) "
                    "VALUES (?,?,?,?,?)",
                    (rowid, record.id, record.character_id, record.session_id, _pack_f32(emb)),
                )

    def _append_mirror(self, record: MemoryRecord) -> None:
        """追加一行到该角色镜像（调用方须已持有 self._lock）。

        W5-C11 IO 降放大：现状每条记录都 mkdir+open+write+close，cephfs 上
        元数据系统调用占大头。本实现：
        - 目录创建缓存 self._mirror_ready：同一角色仅首次 append 需要 mkdir；
          外部删除目录时 os.open 抛 FileNotFoundError → 重建一次再试
        - os.open(O_APPEND|O_CREAT) + 单次 os.write：O_APPEND 保证追加原子性
          （多线程/多进程不会交错写半行）；相比 open("a") 少一层 Python 缓冲对象
        权衡（实测 cephfs 上 wall-clock 方差 4~26ms/op，噪声大于收益，故按系统调用
        数选择最简安全解；常驻 fd 可再省 open/close 两个元数据 op，但镜像面向
        用户手改——外部编辑器 rename 重写会让常驻 fd 指向孤儿 inode 静默丢写，
        拒绝；写队列/批量 flush 会让镜像滞后，reload_mirrors 以镜像为真相源时
        可能反向删数据，同样拒绝）。不 fsync，与旧实现一致（close 刷页缓存）。
        """
        d = self.mirror_dir / record.character_id
        path = d / "records.jsonl"
        line = {
            **record.model_dump(mode="json", include=MEMORY_DETAILS),
            "id": record.id,
            "kind": record.kind,
            "turn_range": [record.turn_start, record.turn_end],
            "content": record.content,
            "session_id": record.session_id,
            "source_message_ids": record.source_message_ids,
            "effective_message_id": record.effective_message_id,
            "commit_seq": record.commit_seq,
            "inherited_from_id": record.inherited_from_id,
            "invalidated": record.invalidated,
            # R36 additive（reload 容忍缺失）
            "participants": record.participants,
            "keywords": record.keywords,
            "importance": record.importance,
            "scene_id": record.scene_id or None,
        }
        data = (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")
        if record.character_id not in self._mirror_ready:
            d.mkdir(parents=True, exist_ok=True)
            self._mirror_ready.add(record.character_id)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        except FileNotFoundError:
            # 目录被外部删除：重建一次（缓存失效），重建后仍失败则向上抛
            self._mirror_ready.discard(record.character_id)
            d.mkdir(parents=True, exist_ok=True)
            self._mirror_ready.add(record.character_id)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            view = memoryview(data)
            while view:  # os.write 可能部分写（大记录），循环至写完
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)

    # ---- R36 单条 CRUD（查看器；DB 为真相源，镜像全量重写）----

    def purge_unscoped_character(self, character_id: str) -> int:
        """Library deletion must preserve story-local experiences and coverage."""
        self._check_open()
        with self._lock:
            rows = self.conn.execute(
                "SELECT id FROM memory_records WHERE character_id=? AND (session_id IS NULL OR session_id='')",
                (character_id,),
            ).fetchall()
            return sum(self.delete_record(row[0]) for row in rows)

    def purge_character(self, character_id: str) -> int:
        """删除某角色的全部记忆记录（角色删除时调用）；返回删除条数。"""
        self._check_open()
        with self._lock:
            n = int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM memory_records WHERE character_id=?",
                    (character_id,),
                ).fetchone()[0]
            )
            if n:
                self._replace_character(character_id, [])
            self.conn.execute("DELETE FROM memory_windows WHERE character_id=?", (character_id,))
            self.conn.commit()
            if n:
                self._refresh_committed_mirror(character_id)
            return n

    def delete_record(self, record_id: str) -> bool:
        self._check_open()
        with self._lock:
            rows = self.conn.execute(
                "SELECT rowid, character_id FROM memory_records WHERE id=?", (record_id,)
            ).fetchall()
            if not rows:
                return False
            (rowid, cid) = rows[0]
            if self.vector_enabled:
                self.conn.execute("DELETE FROM memory_vec WHERE rowid=?", (rowid,))
            if self.branch_vector_enabled:
                self.conn.execute("DELETE FROM memory_vec_branch WHERE rowid=?", (rowid,))
            self.conn.execute("DELETE FROM memory_fts WHERE rowid=?", (rowid,))
            self.conn.execute("DELETE FROM memory_records WHERE id=?", (record_id,))
            self.conn.commit()
            self._rewrite_mirror(cid)
            return True

    def update_record(self, record: MemoryRecord) -> bool:
        """同 id 删旧重插（FTS/关键词随新内容重建）。"""
        self._check_open()
        with self._lock:
            rows = self.conn.execute(
                "SELECT rowid FROM memory_records WHERE id=?", (record.id,)
            ).fetchall()
            if not rows:
                return False
            (rowid,) = rows[0]
            try:
                if self.vector_enabled:
                    self.conn.execute("DELETE FROM memory_vec WHERE rowid=?", (rowid,))
                if self.branch_vector_enabled:
                    self.conn.execute("DELETE FROM memory_vec_branch WHERE rowid=?", (rowid,))
                self.conn.execute("DELETE FROM memory_fts WHERE rowid=?", (rowid,))
                self.conn.execute("DELETE FROM memory_records WHERE id=?", (record.id,))
                record.commit_seq = 0  # edits are new branch commits, not retroactive history
                self._insert_db(record)
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            self._refresh_committed_mirror(record.character_id)
            return True

    def _refresh_committed_mirror(self, character_id: str) -> None:
        """A mirror I/O failure cannot turn an already committed database change into failure."""
        try:
            self._rewrite_mirror(character_id)
        except OSError:
            __import__("logging").getLogger(__name__).exception("memory mirror refresh failed (character=%s)", character_id)

    def _rewrite_mirror(self, character_id: str) -> None:
        """DB 为真相源：全量重写该角色的 JSONL 镜像（调用方须已持有 self._lock）。

        W5-C11 原子性：旧实现 open(...,"w") 原地截断后逐行写，中途崩溃/异常会
        留下半截镜像——而 reload_mirrors() 以镜像为真相源，半截文件会反向删数据。
        改为「同目录临时文件 + os.replace」：POSIX rename 原子，读者要么看到旧
        全文、要么看到新全文；临时文件前缀 "."，reload_mirrors 只认 records.jsonl，
        不会误读残留。

        调用约束：本方法与 delete_record/update_record/purge_character 一样是同步
        实现且需持锁；大角色（数千条）在 cephfs 上可能耗时百毫秒级，服务层若在
        事件循环中调用，应对整个 CRUD 方法用 asyncio.to_thread 包裹（不要只包
        本方法，否则锁外重写会与并发 append 竞争）。
        """
        records = self._records_for_locked(character_id)
        d = self.mirror_dir / character_id
        d.mkdir(parents=True, exist_ok=True)
        self._mirror_ready.add(character_id)
        target = d / "records.jsonl"
        tmp = d / f".records.jsonl.tmp-{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps({
                    **r.model_dump(mode="json", include=MEMORY_DETAILS),
                    "id": r.id, "kind": r.kind,
                    "turn_range": [r.turn_start, r.turn_end],
                    "content": r.content,
                    "session_id": r.session_id,
                    "source_message_ids": r.source_message_ids,
                    "effective_message_id": r.effective_message_id,
                    "commit_seq": r.commit_seq,
                    "inherited_from_id": r.inherited_from_id,
                    "invalidated": r.invalidated,
                    "participants": r.participants, "keywords": r.keywords,
                    "importance": r.importance,
                    "scene_id": r.scene_id or None,
                }, ensure_ascii=False) + "\n")
        os.replace(tmp, target)  # 同目录 rename：原子替换

    def last_consolidated_turn(self, character_id: str, session_id: str) -> int:
        """R36.1 无状态水位：该角色该会话已固化**情景记录**的最大 turn_end（无则 0）。

        只看 kind='episodic'——场景摘要（kind='scene'）是独立的压缩产物，
        不推进窗口水位（否则场景切换后的手动固化会被摘要的 turn_end 堵空）。
        """
        self._check_open()
        with self._lock:
            row = self.conn.execute(
                "SELECT MAX(end_turn) FROM (SELECT turn_end AS end_turn FROM memory_records "
                "WHERE character_id=? AND session_id=? AND kind='episodic' AND invalidated=0 "
                "UNION ALL SELECT turn_end FROM memory_windows WHERE character_id=? AND session_id=? AND status='complete')",
                (character_id, session_id, character_id, session_id),
            ).fetchone()
            return int(row[0] or 0)

    def window_rows(self, character_id: str, session_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT turn_start, turn_end, status, fingerprints, record_ids, error FROM memory_windows WHERE session_id=? AND character_id=? ORDER BY turn_start",
                                     (session_id, character_id)).fetchall()
            return [dict(start=a, end=b, status=c, fingerprints=json.loads(d), record_ids=json.loads(e), error=f)
                    for a, b, c, d, e, f in rows]

    def drop_window(self, session_id: str, character_id: str, start: int, end: int) -> None:
        """Remove a non-complete window so a narrower retry can replace it."""
        with self._lock:
            self.conn.execute(
                "DELETE FROM memory_windows WHERE session_id=? AND character_id=? AND turn_start=? AND turn_end=? AND status!='complete'",
                (session_id, character_id, start, end),
            )
            self.conn.commit()

    def next_window(self, character_id: str, session_id: str, turn: int, messages: list, *, retry_failed: bool = True) -> tuple[int, int, bool]:
        """Dirty/failed windows precede the ordinary watermark, even after later commits.

        Fingerprints also recover a reroll rollback, without reprocessing valid history.
        Automatic extraction passes retry_failed=False so a failed window does not block
        later turns and is not sent to the model again until a manual organize.
        """
        with self._lock:
            rows = self.window_rows(character_id, session_id)
            valid_ids = {r.id for r in self.records_for(character_id, session_id=session_id)
                         if not r.invalidated and not r.source_changed}
            for row in rows:
                window_current = {m.id: m.fingerprint for m in messages if m.status == "final" and row["start"] <= m.turn <= row["end"]}
                if (row["status"] == "dirty" and not row["error"]
                    and row["fingerprints"] == window_current
                    and set(row["record_ids"]).issubset(valid_ids)):
                    self.conn.execute("UPDATE memory_windows SET status='complete' WHERE session_id=? AND character_id=? AND turn_start=? AND turn_end=?",
                                      (session_id, character_id, row["start"], row["end"]))
                    row["status"] = "complete"
            self.conn.commit()
            if retry_failed:
                for row in rows:
                    if row["status"] != "complete" and row["end"] <= turn:
                        return row["start"], row["end"], True
                # Invalidate legacy summaries too: the old maximum must not skip an edited early window.
                legacy = self.conn.execute("SELECT MIN(turn_start), MAX(turn_end) FROM memory_records WHERE session_id=? AND character_id=? AND kind='episodic' AND invalidated=1",
                                           (session_id, character_id)).fetchone()
                if legacy[0] is not None and not rows:
                    return max(1, legacy[0]), min(turn, legacy[1]), True
            watermark = max(self.last_consolidated_turn(character_id, session_id),
                            max((row["end"] for row in rows), default=0))
            return watermark + 1, turn, False

    def plan_next_window(self, character_id, session_id, turn, *, retry_failed=True):
        """Read-only planning for caller-owned transactional consolidation."""
        with self._lock:
            rows = self.window_rows(character_id, session_id)
            if retry_failed:
                repair = next((row for row in rows if row['status'] != 'complete' and row['end'] <= turn), None)
                if repair:
                    return repair['start'], repair['end'], True
                legacy = self.conn.execute("SELECT MIN(turn_start),MAX(turn_end) FROM memory_records WHERE session_id=? AND character_id=? AND kind='episodic' AND invalidated=1", (session_id, character_id)).fetchone()
                if legacy[0] is not None and not rows:
                    return max(1, legacy[0]), min(turn, legacy[1]), True
            watermark = max(self.last_consolidated_turn(character_id, session_id), max((row['end'] for row in rows), default=0))
            return watermark + 1, turn, False

    def mark_window(self, session_id: str, character_id: str, start: int, end: int,
                    status: str, fingerprints: dict[str, str], error: str = "") -> None:
        with self._lock:
            self.conn.execute("INSERT INTO memory_windows(session_id, character_id, turn_start, turn_end, status, fingerprints, error) VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id,character_id,turn_start,turn_end) DO UPDATE SET status=excluded.status, fingerprints=excluded.fingerprints,error=excluded.error",
                              (session_id, character_id, start, end, status, json.dumps(fingerprints), error))
            self.conn.commit()

    def commit_window(self, session_id: str, character_id: str, start: int, end: int,
                      fingerprints: dict[str, str], records: list[MemoryRecord]) -> list[MemoryRecord]:
        """All validated records and even an empty coverage result commit together."""
        with self._lock:
            existing = self.window_rows(character_id, session_id)
            if any(w["start"] == start and w["end"] == end and w["status"] == "complete"
                   and w["fingerprints"] == fingerprints for w in existing):
                return []
            accepted = []
            try:
                old = self.records_for(character_id, session_id=session_id)
                fresh_keys = {(r.category, r.content, tuple(sorted(r.source_message_ids))) for r in records}
                for previous in old:
                    if (previous.kind in {"episodic", "summary", "fact"} and not previous.manually_revised
                        and previous.source_message_ids and set(previous.source_message_ids).issubset(fingerprints)
                        and (previous.category, previous.content, tuple(sorted(previous.source_message_ids))) not in fresh_keys):
                        self.conn.execute("UPDATE memory_records SET invalidated=1 WHERE id=?", (previous.id,))
                        previous.invalidated = True
                for item in records:
                    if item.session_id != session_id or item.character_id != character_id:
                        raise ValueError("记忆窗口归属不匹配")
                    # User corrections remain authoritative for their original evidence.
                    if any(r.manually_revised and set(r.source_message_ids) == set(item.source_message_ids)
                           and r.category == item.category for r in old):
                        continue
                    if any(not r.invalidated and r.content == item.content and r.category == item.category
                           and r.source_fingerprints == item.source_fingerprints for r in old + accepted):
                        continue
                    # Keep previous states as history. Recall suppresses them only while the
                    # superseding record's evidence remains valid, so deletion/rollback restores them.
                    self._insert_db(item)
                    accepted.append(item)
                self.conn.execute("INSERT INTO memory_windows VALUES(?,?,?,?,?,?,?,'') ON CONFLICT(session_id,character_id,turn_start,turn_end) DO UPDATE SET status='complete',fingerprints=excluded.fingerprints,record_ids=excluded.record_ids,error=''",
                                  (session_id, character_id, start, end, "complete", json.dumps(fingerprints), json.dumps([r.id for r in accepted])))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
            # The DB is authoritative; mirror failure must not roll back completed extraction.
            try:
                self._rewrite_mirror(character_id)
            except OSError:
                __import__("logging").getLogger(__name__).warning("memory mirror refresh failed", exc_info=True)
            return accepted

    def refresh_branch_mirrors(self, branch_id: str) -> None:
        """Refresh derived mirrors after an external same-database transaction."""
        with self._lock:
            characters = [row[0] for row in self.conn.execute('SELECT DISTINCT character_id FROM memory_records WHERE session_id=?', (branch_id,))]
            for character in characters:
                self._refresh_committed_mirror(character)

    def refresh_actor_mirror(self, actor_id: str) -> None:
        with self._lock:
            self._refresh_committed_mirror(actor_id)

    def prepare_records(self, records: list[MemoryRecord]) -> list[dict]:
        """Freeze records and perform optional embedding before a SQL transaction."""
        vectors = self.embedding.embed([record.content for record in records]) if self.vector_enabled and records else None
        if vectors is not None and (len(vectors) != len(records) or any(len(vector) != self.vec_dim for vector in vectors)):
            raise ValueError('embedding 返回维度或数量无效')
        return [{'record': record.model_dump(mode='json'), 'embedding': vectors[index] if vectors is not None else None}
                for index, record in enumerate(records)]

    def record_by_id(self, record_id: str) -> MemoryRecord | None:
        with self._lock:
            row = self.conn.execute(f'SELECT {self._RECORD_COLS} FROM memory_records WHERE id=?', (record_id,)).fetchone()
            return self._row_to_record(row) if row else None

    def branch_actor_ids(self, branch_id: str) -> list[str]:
        with self._lock:
            return [row[0] for row in self.conn.execute('SELECT DISTINCT character_id FROM memory_records WHERE session_id=?', (branch_id,))]

    def scene_summary(
        self, character_id: str, scene_id: str, session_id: str | None = None,
    ) -> MemoryRecord | None:
        """R36.3：该角色对某场景的摘要（最新一份）。"""
        self._check_open()
        with self._lock:
            scoped = " AND session_id=?" if session_id is not None else ""
            params = (character_id, scene_id, session_id) if session_id is not None else (character_id, scene_id)
            rows = self.conn.execute(
                f"SELECT {self._RECORD_COLS} FROM memory_records "
                f"WHERE character_id=? AND scene_id=? AND kind='scene' "
                f"AND invalidated=0{scoped} "
                "ORDER BY rowid DESC LIMIT 1",
                params,
            ).fetchall()
            return self._row_to_record(rows[0]) if rows else None

    # ---- 镜像回写 ----

    def reload_mirrors(self) -> int:
        """从 JSONL 镜像重建：用户手改后回写。返回重建的记录总数。"""
        self._check_open()
        if not self.mirror_dir.exists():
            return 0
        with self._lock:
            return self._reload_mirrors_locked()

    def _reload_mirrors_locked(self) -> int:
        total = 0
        for cdir in sorted(self.mirror_dir.iterdir()):
            if not cdir.is_dir():
                continue
            f = cdir / "records.jsonl"
            if not f.exists():
                continue
            previous = {item.id: item for item in self._records_for_locked(cdir.name)}
            records: list[MemoryRecord] = []
            for raw in f.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue  # 坏行跳过，不让单行错误毁掉整次重建
                turn_range = obj.get("turn_range") or [0, 0]
                kind = obj.get("kind", "manual")
                if kind not in ("summary", "fact", "manual", "episodic", "scene"):
                    kind = "manual"
                record_id = obj.get("id") or new_id("mem")
                old = previous.get(record_id)
                record = MemoryRecord(
                    **{key: obj.get(key, getattr(old, key)) if old else obj[key]
                       for key in MEMORY_DETAILS if key in obj or old is not None},
                    id=record_id,
                    character_id=cdir.name,
                    session_id=obj.get("session_id", old.session_id if old else ""),
                    kind=kind,
                    content=obj.get("content", ""),
                    turn_start=int(turn_range[0]),
                    turn_end=int(turn_range[1]),
                    source_message_ids=obj.get(
                        "source_message_ids", old.source_message_ids if old else []
                    ),
                    effective_message_id=obj.get(
                        "effective_message_id", old.effective_message_id if old else None
                    ),
                    inherited_from_id=obj.get(
                        "inherited_from_id", old.inherited_from_id if old else None
                    ),
                    invalidated=bool(obj.get("invalidated", old.invalidated if old else False)),
                    created_at=old.created_at if old else datetime.now(timezone.utc),
                    participants=obj.get("participants") or [],
                    keywords=obj.get("keywords") or [],
                    importance=int(obj.get("importance") or 3),
                    scene_id=obj.get("scene_id") or "",
                )
                if old is not None and (
                    old.model_dump(exclude={"commit_seq", "created_at"})
                    == record.model_dump(exclude={"commit_seq", "created_at"})
                ):
                    record.commit_seq = old.commit_seq
                records.append(record)
            self._replace_character(cdir.name, records)
            total += len(records)
        self.conn.commit()
        return total

    def _replace_character(self, character_id: str, records: list[MemoryRecord]) -> None:
        rowids = [
            r[0]
            for r in self.conn.execute(
                "SELECT rowid FROM memory_records WHERE character_id=?",
                (character_id,),
            )
        ]
        if rowids:
            marks = ",".join("?" * len(rowids))
            if self.vector_enabled:
                self.conn.execute(f"DELETE FROM memory_vec WHERE rowid IN ({marks})", rowids)
            if self.branch_vector_enabled:
                self.conn.execute(f"DELETE FROM memory_vec_branch WHERE rowid IN ({marks})", rowids)
            self.conn.execute(f"DELETE FROM memory_fts WHERE rowid IN ({marks})", rowids)
            self.conn.execute(
                "DELETE FROM memory_records WHERE character_id=?", (character_id,)
            )
        for rec in records:
            self._insert_db(rec)

    # ---- 检索 ----

    def _visibility_sql(self, alias='memory_records'):
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='branches'").fetchone():
            return ''
        return f' AND NOT EXISTS (SELECT 1 FROM branches b WHERE b.id={alias}.session_id AND b.active!=1)'

    def records_for(
        self, character_id: str, *, limit: int | None = None, offset: int = 0,
        session_id: str | None = None,
    ) -> list[MemoryRecord]:
        """该角色记忆（默认全量，向后兼容）。

        W5 新增分页：limit=None 且 offset=0 时行为与旧版完全一致；
        否则走 SQL LIMIT/OFFSET（不整表取回再切片）。limit/offset 为负按 0 处理。
        """
        self._check_open()
        with self._lock:
            if limit is None and offset <= 0 and session_id is None:
                rows = self.conn.execute(f'SELECT {self._RECORD_COLS} FROM memory_records WHERE character_id=?' + self._visibility_sql() + ' ORDER BY rowid', (character_id,)).fetchall()
                return [self._row_to_record(row) for row in rows]
            sql = (
                f"SELECT {self._RECORD_COLS} FROM memory_records "
                "WHERE character_id=?"
            )
            params: list = [character_id]
            sql += self._visibility_sql()
            if session_id is not None:
                sql += " AND session_id=?"
                params.append(session_id)
            sql += " ORDER BY rowid"
            if limit is None:
                sql += " LIMIT -1 OFFSET ?"  # SQLite: 负 LIMIT = 不限制条数
                params.append(max(0, int(offset)))
            else:
                sql += " LIMIT ? OFFSET ?"
                params.extend((max(0, int(limit)), max(0, int(offset))))
            rows = self.conn.execute(sql, params).fetchall()
            return [self._row_to_record(r) for r in rows]

    def _records_for_locked(self, character_id: str) -> list[MemoryRecord]:
        """该角色全量记忆（编辑器用）。"""
        rows = self.conn.execute(
            f"SELECT {self._RECORD_COLS} FROM memory_records "
            "WHERE character_id=? ORDER BY rowid",
            (character_id,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row: tuple) -> MemoryRecord:
        from datetime import datetime

        return MemoryRecord(
            id=row[0],
            character_id=row[1],
            session_id=row[2],
            turn_start=row[3],
            turn_end=row[4],
            kind=row[5],
            content=row[6],
            source_message_ids=json.loads(row[7]),
            created_at=datetime.fromisoformat(row[8]),
            participants=json.loads(row[9] or "[]"),
            keywords=json.loads(row[10] or "[]"),
            importance=int(row[11] or 3),
            scene_id=row[12] or "",
            effective_message_id=row[13] or None,
            commit_seq=int(row[14] or 0),
            inherited_from_id=row[15] or None,
            invalidated=bool(row[16]),
            **json.loads(row[17] or "{}"),
        )

    def _fts_query(
        self, query: str, character_id: str, limit: int,
        session_id: str | None = None,
    ) -> list[tuple]:
        if session_id is None:
            sql = (
                "SELECT memory_fts.rowid, bm25(memory_fts) FROM memory_fts JOIN memory_records AS mr ON mr.rowid=memory_fts.rowid "
                "WHERE memory_fts MATCH ? AND memory_fts.character_id = ? AND mr.invalidated=0 "
                + self._visibility_sql('mr') +
                "ORDER BY bm25(memory_fts) LIMIT ?"
            )
            params = (query, character_id, limit)
        else:
            # Filter by branch inside the candidate query, before LIMIT.
            sql = (
                "SELECT memory_fts.rowid, bm25(memory_fts) "
                "FROM memory_fts JOIN memory_records AS mr "
                "ON mr.rowid = memory_fts.rowid "
                "WHERE memory_fts MATCH ? AND memory_fts.character_id = ? "
                "AND mr.session_id = ? AND mr.invalidated=0 "
                + self._visibility_sql('mr') +
                "ORDER BY bm25(memory_fts) LIMIT ?"
            )
            params = (query, character_id, session_id, limit)
        try:
            return self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # 查询串含 FTS5 语法字符（引号/括号等）→ 整句按字面短语重试
            safe = '"' + query.replace('"', " ") + '"'
            return self.conn.execute(sql, (safe, *params[1:])).fetchall()

    def search(
        self, character_id: str, query: str, k: int = 4, *,
        current_turn: int | None = None, session_id: str | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        self._check_open()
        with self._lock:
            return self._search_locked(
                character_id, query, k,
                current_turn=current_turn, session_id=session_id,
            )

    async def search_async(
        self, character_id: str, query: str, k: int = 4, *,
        current_turn: int | None = None, session_id: str | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        """search 的异步包装（W5）：SQLite 与 embedding 均为同步阻塞调用，
        统一下沉到线程池，避免阻塞事件循环。

        语义与返回值与 search 完全一致；调用方（session.py 回合路径、app.py
        查看器端点）可逐步切到本方法（接线归主线程波 2）。
        """
        self._check_open()
        return await asyncio.to_thread(
            self.search, character_id, query, k,
            current_turn=current_turn, session_id=session_id,
        )

    def _fetch_records_by_rowids_locked(
        self, character_id: str, rowids: list[int],
    ) -> dict[int, MemoryRecord]:
        """按 rowid 精准回表（W5-C8）：一次 IN 查询，只取候选行。

        代替旧实现对该角色全表取行 + 全表 rowid→id 映射；
        character_id 条件为防御性过滤（FTS 候选已按角色过滤）。
        """
        if not rowids:
            return {}
        marks = ",".join("?" * len(rowids))
        rows = self.conn.execute(
            f"SELECT rowid, {self._RECORD_COLS} FROM memory_records "
            f"WHERE rowid IN ({marks}) AND character_id=? AND invalidated=0" + self._visibility_sql(),
            (*rowids, character_id),
        ).fetchall()
        return {r[0]: self._row_to_record(r[1:]) for r in rows}

    def _fetch_records_by_ids_locked(
        self, character_id: str, record_ids: list[str],
        session_id: str | None = None,
    ) -> dict[str, MemoryRecord]:
        """按主键 id 精准回表（vec 分支用；vec0 存的是 record_id 文本）。"""
        if not record_ids:
            return {}
        marks = ",".join("?" * len(record_ids))
        scope_sql = " AND session_id=?" if session_id is not None else ""
        params = (*record_ids, character_id, session_id) if session_id is not None else (*record_ids, character_id)
        rows = self.conn.execute(
            f"SELECT {self._RECORD_COLS} FROM memory_records "
            f"WHERE id IN ({marks}) AND character_id=? AND invalidated=0{scope_sql}" + self._visibility_sql(),
            params,
        ).fetchall()
        return {r[0]: self._row_to_record(r) for r in rows}

    def _search_locked(
        self, character_id: str, query: str, k: int = 4, *,
        current_turn: int | None = None, session_id: str | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        """混合检索：0.6×向量 + 0.4×BM25（各自 min-max 归一化后加权）。

        降级（无 sqlite-vec 或未注入 embedding）时纯 BM25，权重 1.0。
        R36.2：提供 current_turn+session_id 时启用三元加权
        （相关度 + 新近度 + 重要性，design/v3/m9-r36 §3）。
        W5-C8：先 FTS/vec 取候选（各限 k*3）再按 rowid/id 精准回表，
        不再随记录数线性全表取行；打分与归一化逻辑未动。
        """
        # vec0's existing schema has no branch metadata. A scoped prompt uses
        # the FTS path until M17 can rebuild a branch-filterable vector table;
        # filtering KNN results after LIMIT would allow sibling rows to crowd
        # out this branch's candidates.
        use_vector = self.vector_enabled and (session_id is None or self.branch_vector_enabled)

        # 1) FTS 候选 rowid → 精准回表
        fts_hits = self._fts_query(query, character_id, k * 3, session_id)
        recs: dict[str, MemoryRecord] = {}
        fts_scores: dict[str, float] = {}
        if fts_hits:
            by_rowid = self._fetch_records_by_rowids_locked(
                character_id, [rowid for rowid, _ in fts_hits]
            )
            for rowid, bm25_score in fts_hits:
                rec = by_rowid.get(rowid)
                if rec is not None:
                    recs[rec.id] = rec
                    fts_scores[rec.id] = -float(bm25_score)  # bm25 越负越好

        # 2) 向量候选 record_id → 精准回表
        vec_scores: dict[str, float] = {}
        if use_vector:
            qv = self.embedding.embed([query])[0]
            if len(qv) == self.vec_dim:
                if session_id is None:
                    knn = self.conn.execute(
                        "SELECT record_id, distance FROM memory_vec "
                        "WHERE embedding MATCH ? AND k = ? AND character_id = ?",
                        (_pack_f32(qv), max(k * 3, 1), character_id),
                    ).fetchall()
                else:
                    knn = self.conn.execute(
                        "SELECT record_id, distance FROM memory_vec_branch "
                        "WHERE embedding MATCH ? AND k = ? AND character_id = ? "
                        "AND session_id = ?",
                        (_pack_f32(qv), max(k * 3, 1), character_id, session_id),
                    ).fetchall()
                by_id = self._fetch_records_by_ids_locked(
                    character_id, [rid for rid, _ in knn], session_id
                )
                for rid, dist in knn:
                    rec = by_id.get(rid)
                    if rec is not None:
                        recs[rid] = rec
                        vec_scores[rid] = 1.0 / (1.0 + float(dist))  # 距离→相似度

        vec_norm = self._minmax_norm(vec_scores)
        fts_norm = self._minmax_norm(fts_scores)

        w_vec, w_fts = (0.6, 0.4) if use_vector else (0.0, 1.0)
        combined: dict[str, float] = {}
        for rid in set(vec_norm) | set(fts_norm):
            # recs 由候选回表构建，故此处 rid 必在 recs（旧版的全表一致性保护已前置）
            relevance = w_vec * vec_norm.get(rid, 0.0) + w_fts * fts_norm.get(rid, 0.0)
            if current_turn is None:
                combined[rid] = relevance
                continue
            # R36.2 三元加权：新近度（跨会话固定 floor）+ 重要性
            rec = recs[rid]
            if session_id and rec.session_id == session_id:
                recency = 0.5 ** (
                    max(0, current_turn - rec.turn_end) / self.MEMORY_RECENCY_HALFLIFE
                )
            else:
                recency = self.MEMORY_CROSS_SESSION_RECENCY
            importance = (rec.importance - 1) / 4  # 1..5 → 0..1
            combined[rid] = (
                self.MEMORY_W_REL * relevance
                + self.MEMORY_W_REC * recency
                + self.MEMORY_W_IMP * importance
            )

        top = sorted(combined.items(), key=lambda x: (-x[1], x[0]))[:k]
        return [(recs[rid], score) for rid, score in top]

    @staticmethod
    def _minmax_norm(scores: dict[str, float]) -> dict[str, float]:
        if not scores:
            return {}
        vals = list(scores.values())
        lo, hi = min(vals), max(vals)
        if hi - lo <= 1e-12:
            return {key: 1.0 for key in scores}  # 单点/全同 → 满分
        return {key: (v - lo) / (hi - lo) for key, v in scores.items()}


# ---------------------------------------------------------------- 巩固


def _fallback_summarize(text: str) -> str:
    """本地兜底摘要：拼接截断前 500 字（无 LLM 也可运行）。"""
    return text[:500]


class MemoryConsolidator:
    """对话 → 角色记忆（R5.3 防误写核心：只收该角色可见且 final 的消息）。"""

    def __init__(
        self,
        store: MemoryStore,
        summarize: Callable[[str], str] | None = None,
    ):
        self.store = store
        self.summarize = summarize

    def consolidate(self, session: SessionState, character_id: str) -> MemoryRecord | None:
        msgs = [
            m
            for m in session.visible_messages_for(character_id)
            if m.status == "final"  # pending/retracted 一律不进（防误写）
        ]
        if not msgs:
            return None
        transcript = "\n".join(f"{m.actor}: {m.content}" for m in msgs)
        if self.summarize is not None:
            summary = self.summarize(transcript)
        else:
            summary = _fallback_summarize(transcript)
        record = MemoryRecord(
            character_id=character_id,
            session_id=session.meta.id,
            turn_start=min(m.turn for m in msgs),
            turn_end=max(m.turn for m in msgs),
            kind="summary",
            content=summary,
            source_message_ids=[m.id for m in msgs],
        )
        self.store.add(record)
        return record


# ---------------------------------------------------------------- LLM 摘要工厂


def default_summarizer(
    base_url: str,
    api_key_env: str,
    model: str,
    *,
    provider: str = "",
    provider_allow_fallbacks: bool = True,
) -> Callable[[str], str]:
    """工厂：返回用 httpx 调 OpenAI 兼容 chat/completions 的摘要函数。"""

    def summarize(text: str) -> str:
        api_key = os.environ[api_key_env]
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是记忆整理器。把给定的多角色对话压缩成第三人称的记忆摘要，"
                        "200 字以内，只记录稳定的事实与关系变化，只输出摘要正文。"
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        if provider:
            body["provider"] = {
                "order": [provider],
                "allow_fallbacks": provider_allow_fallbacks,
            }
        resp = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    return summarize
