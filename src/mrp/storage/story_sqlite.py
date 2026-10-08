"""Transactional branch storage. JSON remains the portable interchange format.

SQLite owns only branches whose active flag is set. Inactive rows are migration
shadows and never override an existing JSON branch. No production data migrates
implicitly. Connections are short lived and transactions never span model calls.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mrp.shared.models import SessionState
from .command_receipts import (RECEIPT_KEY, CommandConflict, CommandAlreadyCommitted,
                               portable_receipts, put_portable_receipts,
                               public_receipt, validate_receipt)
from .command_receipts import JOB_KEY, portable_jobs, put_portable_jobs
from .command_receipts import CLAIM_KEY, portable_claims, put_portable_claims, GenerationIncomplete


class BranchRevisionConflict(ValueError):
    pass


class OperationConflict(ValueError):
    pass


class _Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


class StorySqlite:
    COLLECTIONS = ('messages', 'state_revisions', 'turn_runs', 'conversation_runs',
                   'generation_operations', 'usage_records', 'director_log',
                   'story_events', 'bookmarks', 'characters', 'lorebooks', 'scenes',
                   'groups', 'player_identities', 'pinned_facts', 'material_blobs')
    DICT_COLLECTIONS = {'generation_operations', 'material_blobs'}

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, factory=_Connection) as preflight:
                if preflight.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='format_version'").fetchone():
                    known = preflight.execute('SELECT version FROM format_version').fetchall()
                    if known not in ([(1,)], [(2,)], [(3,)], [(4,)]):
                        raise ValueError('Unsupported story storage format; refusing to write')
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='format_version'").fetchone():
                if db.execute('SELECT version FROM format_version').fetchall() not in ([(1,)], [(2,)], [(3,)], [(4,)]):
                    raise ValueError('Unsupported story storage format; refusing to write')
            schema = '''
            CREATE TABLE IF NOT EXISTS format_version(version INTEGER NOT NULL);
            INSERT INTO format_version SELECT 4 WHERE NOT EXISTS(SELECT 1 FROM format_version);
            CREATE TABLE IF NOT EXISTS branch_purges(branch_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS memory_jobs(branch_id TEXT NOT NULL,operation_id TEXT NOT NULL,
              fingerprint TEXT NOT NULL,status TEXT NOT NULL,plan TEXT NOT NULL,result TEXT,
              version INTEGER NOT NULL,PRIMARY KEY(branch_id,operation_id));
            CREATE TABLE IF NOT EXISTS lifecycle_operations(operation_id TEXT PRIMARY KEY,action TEXT NOT NULL,
              scope TEXT NOT NULL,fingerprint TEXT NOT NULL,status TEXT NOT NULL,plan TEXT,result TEXT,error TEXT);
            CREATE TABLE IF NOT EXISTS lifecycle_locks(story_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trash_entries(generation_id TEXT PRIMARY KEY,story_id TEXT NOT NULL,
              status TEXT NOT NULL,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS online_cutovers(story_id TEXT PRIMARY KEY,status TEXT NOT NULL,manifest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS creation_operations(operation_id TEXT PRIMARY KEY,action TEXT NOT NULL,
              fingerprint TEXT NOT NULL,status TEXT NOT NULL,result TEXT,version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS creation_entities(operation_id TEXT NOT NULL,kind TEXT NOT NULL,
              identity TEXT NOT NULL,PRIMARY KEY(kind,identity));
            CREATE TABLE IF NOT EXISTS creation_imported_receipts(operation_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS creation_adoptions(operation_id TEXT NOT NULL,branch_id TEXT NOT NULL,
              revision INTEGER NOT NULL,PRIMARY KEY(operation_id,branch_id));
            CREATE TABLE IF NOT EXISTS creation_failure_details(operation_id TEXT PRIMARY KEY,reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS generation_claims(branch_id TEXT NOT NULL,operation_id TEXT NOT NULL,
              fingerprint TEXT NOT NULL,version INTEGER NOT NULL,PRIMARY KEY(branch_id,operation_id));
            CREATE TABLE IF NOT EXISTS materials(hash TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS branches(id TEXT PRIMARY KEY,story_id TEXT NOT NULL,
              revision INTEGER NOT NULL,active INTEGER NOT NULL,header_hash TEXT NOT NULL,
              summary TEXT NOT NULL,committed_at REAL NOT NULL,source_hash TEXT);
            CREATE INDEX IF NOT EXISTS branch_story ON branches(story_id,active);
            CREATE TABLE IF NOT EXISTS components(branch_id TEXT NOT NULL,kind TEXT NOT NULL,
              item_key TEXT NOT NULL,position INTEGER NOT NULL,material_hash TEXT NOT NULL,
              PRIMARY KEY(branch_id,kind,item_key),FOREIGN KEY(branch_id) REFERENCES branches(id) ON DELETE CASCADE,
              FOREIGN KEY(material_hash) REFERENCES materials(hash));
            CREATE INDEX IF NOT EXISTS component_page ON components(branch_id,kind,position);
            CREATE TABLE IF NOT EXISTS commits(branch_id TEXT NOT NULL,revision INTEGER NOT NULL,
              operation_id TEXT,content_hash TEXT NOT NULL,committed_at REAL NOT NULL,
              PRIMARY KEY(branch_id,revision),UNIQUE(branch_id,operation_id));
            CREATE TABLE IF NOT EXISTS commit_memory_effects(branch_id TEXT NOT NULL,
              revision INTEGER NOT NULL,actions TEXT NOT NULL,PRIMARY KEY(branch_id,revision));
            CREATE TABLE IF NOT EXISTS command_receipts(branch_id TEXT NOT NULL,operation_id TEXT NOT NULL,
              version INTEGER NOT NULL,fingerprint TEXT NOT NULL,result TEXT NOT NULL,
              revision INTEGER NOT NULL,PRIMARY KEY(branch_id,operation_id));
            CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,
              branch_id TEXT NOT NULL,revision INTEGER NOT NULL,event TEXT NOT NULL,
              delivered INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS migrations(id TEXT PRIMARY KEY,story_id TEXT NOT NULL,
              status TEXT NOT NULL,manifest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS story_saves(id TEXT PRIMARY KEY,branch_id TEXT NOT NULL,
              material_hash TEXT NOT NULL,summary TEXT NOT NULL,active INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS run_lookup(branch_id TEXT NOT NULL,kind TEXT NOT NULL,run_id TEXT NOT NULL,
              operation_id TEXT NOT NULL,status TEXT NOT NULL,position INTEGER NOT NULL,material_hash TEXT NOT NULL,
              PRIMARY KEY(branch_id,kind,run_id));
            CREATE INDEX IF NOT EXISTS run_operation ON run_lookup(branch_id,operation_id);
            CREATE INDEX IF NOT EXISTS run_recover ON run_lookup(branch_id,kind,status,position);
            CREATE TABLE IF NOT EXISTS run_messages(branch_id TEXT NOT NULL,kind TEXT NOT NULL,run_id TEXT NOT NULL,message_id TEXT NOT NULL,
              PRIMARY KEY(branch_id,kind,run_id,message_id));
            CREATE INDEX IF NOT EXISTS run_message_page ON run_messages(branch_id,message_id);
            CREATE TABLE IF NOT EXISTS message_lookup(branch_id TEXT NOT NULL,message_id TEXT NOT NULL,
              seq INTEGER NOT NULL,turn INTEGER NOT NULL,input_group_id TEXT,
              PRIMARY KEY(branch_id,message_id));
            CREATE INDEX IF NOT EXISTS message_sequence ON message_lookup(branch_id,seq);
            '''
            for statement in schema.split(';'):
                if statement.strip():
                    db.execute(statement)
            db.execute('UPDATE format_version SET version=4 WHERE version IN (1,2,3)')
            # Derived indexes are backfilled only for missing rows, without
            # material hydration or rewriting durable story content.
            missing=db.execute("SELECT c.branch_id,c.kind,c.item_key,c.position,c.material_hash,m.payload FROM components c JOIN materials m ON m.hash=c.material_hash LEFT JOIN run_lookup r ON r.branch_id=c.branch_id AND r.kind=c.kind AND r.run_id=c.item_key WHERE c.kind IN ('turn_runs','conversation_runs') AND r.run_id IS NULL").fetchall()
            for branch,kind,identity,position,material,payload in missing:
                self._index_run(db,branch,kind,identity,position,material,json.loads(payload))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20, factory=_Connection)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        return db

    @staticmethod
    def require_lifecycle_access(db, story_id):
        from .lifecycle_repo import writer, LifecycleConflict
        locked = db.execute('SELECT operation_id FROM lifecycle_locks WHERE story_id=?', (story_id,)).fetchone()
        if locked and locked[0] != writer.get():
            raise LifecycleConflict('故事正在完成生命周期操作，请稍后再试', 'operation_incomplete')

    @contextmanager
    def transaction(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def active(self, branch_id: str) -> bool:
        with self.connect() as db:
            row = db.execute('SELECT active FROM branches WHERE id=?', (branch_id,)).fetchone()
            return bool(row and row[0] == 1)

    def owns(self, branch_id: str) -> bool:
        """Active or deleted SQLite route; a frozen JSON source cannot resurrect."""
        with self.connect() as db:
            row = db.execute('SELECT active FROM branches WHERE id=?', (branch_id,)).fetchone()
            return bool(row and row[0] != 0)

    def owned_ids(self):
        with self.connect() as db:
            return {row[0] for row in db.execute('SELECT id FROM branches WHERE active!=0')}

    def revision(self, branch_id: str) -> int | None:
        with self.connect() as db:
            row = db.execute('SELECT revision FROM branches WHERE id=? AND active=1', (branch_id,)).fetchone()
            return int(row[0]) if row else None

    @staticmethod
    def _put(db, value):
        payload = canonical(value)
        key = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        db.execute('INSERT OR IGNORE INTO materials VALUES (?,?)', (key, payload))
        return key

    def _encode(self, db, value):
        if isinstance(value, list):
            return [self._encode(db, item) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key in {'snapshot', 'baseline_state', 'parallel_context', 'world_archive_records', 'prompt_preset_snapshot'} and isinstance(item, (dict, list)) and item:
                    result[key] = {'__mrp_material_v1__': self._put(db, self._encode(db, item))}
                else:
                    result[key] = self._encode(db, item)
            return result
        return value

    def _decode(self, db, value):
        if isinstance(value, list):
            return [self._decode(db, item) for item in value]
        if isinstance(value, dict):
            if set(value) == {'__mrp_material_v1__'}:
                key = value['__mrp_material_v1__']
                row = db.execute('SELECT payload FROM materials WHERE hash=?', (key,)).fetchone()
                if row is None or hashlib.sha256(row[0].encode('utf-8')).hexdigest() != key:
                    raise ValueError('Missing or corrupt story material')
                return self._decode(db, json.loads(row[0]))
            return {key: self._decode(db, item) for key, item in value.items()}
        return value

    def _view_material(self, db, material):
        row = db.execute('SELECT payload FROM materials WHERE hash=?', (material,)).fetchone()
        if row is None or hashlib.sha256(row[0].encode('utf-8')).hexdigest() != material:
            raise ValueError('Missing or corrupt story material')
        excluded = {'snapshot', 'baseline_state', 'parallel_context', 'world_archive_records', 'prompt_preset_snapshot', 'material_blobs', 'steps', 'scheduling_trace'}
        def prune(item):
            if isinstance(item, dict):
                return {key: prune(value) for key, value in item.items() if key not in excluded}
            if isinstance(item, list):
                return [prune(value) for value in item]
            return item
        return self._decode(db, prune(json.loads(row[0])))

    def write(self, state: SessionState, summary: dict, *, expected_revision=None,
              operation_id=None, outbox_events=(), active=True, exclusive=False,
              source_hash=None, memory_actions=(), command_receipt=None, db=None):
        if db is None:
            with self.transaction() as connection:
                return self.write(state, summary, expected_revision=expected_revision,
                                  operation_id=operation_id, outbox_events=outbox_events,
                                  active=active, exclusive=exclusive, source_hash=source_hash,
                                  memory_actions=memory_actions, command_receipt=command_receipt, db=connection)
        branch = state.meta.id
        self.require_lifecycle_access(db, state.meta.story_id or branch)
        from .creation_operations import scope_for, claim
        if scope_for(self):
            claim(self, 'branch', branch, db=db)
            active = False
        previous = db.execute('SELECT revision,active FROM branches WHERE id=?', (branch,)).fetchone()
        if not previous and state.meta.story_id and state.meta.story_id != branch:
            if not db.execute('SELECT 1 FROM branches WHERE story_id=? AND active=1',(state.meta.story_id,)).fetchone():
                # Legacy sources remain authoritative during explicit shadow normalization.
                from .lifecycle_repo import writer
                scope = scope_for(self)
                owned_root = scope and db.execute('SELECT 1 FROM creation_entities WHERE operation_id=? AND kind=? AND identity=?',(scope['operation_id'],'branch',state.meta.story_id)).fetchone()
                if not owned_root and not (writer.get() or '').startswith('cutover:'):
                    raise BranchRevisionConflict('父故事已删除，拒绝迟到创建')
        if state.meta.parent_branch_id:
            parent=db.execute('SELECT active FROM branches WHERE id=?',(state.meta.parent_branch_id,)).fetchone()
            if parent and parent[0] == -1:
                raise BranchRevisionConflict('父路线已删除，拒绝迟到创建')
        if previous and previous[1] == 0 and active:
            raise BranchRevisionConflict('存储路径已切换，请重新加载世界线')
        if previous and previous[1] == -1:
            raise BranchRevisionConflict('世界线已删除，迟到提交不能恢复已删除内容')
        if command_receipt is not None:
            validate_receipt(command_receipt)
            job=db.execute('SELECT fingerprint FROM memory_jobs WHERE branch_id=? AND operation_id=?',(branch,command_receipt['operation_id'])).fetchone()
            if job and job[0] != command_receipt['fingerprint']:
                raise CommandConflict('操作ID已被另一记忆任务占用')
            claim = db.execute('SELECT fingerprint,version FROM generation_claims WHERE branch_id=? AND operation_id=?', (branch, command_receipt['operation_id'])).fetchone()
            if claim is not None and claim != (command_receipt['fingerprint'], 1):
                raise CommandConflict('操作 ID 已被另一生成请求占用')
            recorded = self.lookup_command(branch, command_receipt['operation_id'], db=db)
            if recorded is not None:
                if recorded['fingerprint'] != command_receipt['fingerprint']:
                    raise CommandConflict('同一命令ID已用于不同请求')
                raise CommandAlreadyCommitted(recorded)
        entries = dict(portable_receipts(state))
        for operation, saved in self._receipt_entries(db, branch).items():
            if operation in entries and entries[operation] != saved:
                raise CommandConflict('保存不能改写已提交命令结果')
            entries[operation] = saved
        if command_receipt is not None:
            operation = command_receipt['operation_id']
            proposed = {**public_receipt(command_receipt), 'revision': state.meta.branch_revision}
            if operation in entries and entries[operation] != proposed:
                raise CommandConflict('命令凭据与已有状态不一致')
            entries[operation] = proposed
        put_portable_receipts(state, entries)
        claims = dict(portable_claims(state))
        for operation, fingerprint, version in db.execute('SELECT operation_id,fingerprint,version FROM generation_claims WHERE branch_id=?', (branch,)):
            if version != 1 or (operation in claims and claims[operation] != fingerprint):
                raise CommandConflict('不能改写既有生成尝试身份')
            claims[operation] = fingerprint
        put_portable_claims(state, claims)
        jobs = dict(portable_jobs(state))
        for operation,fp,status,plan,result,version in db.execute('SELECT operation_id,fingerprint,status,plan,result,version FROM memory_jobs WHERE branch_id=?',(branch,)):
            if version != 1 or (operation in jobs and jobs[operation]['fingerprint'] != fp):
                raise CommandConflict('不能改写记忆任务身份')
            jobs[operation]={'fingerprint':fp,'status':status,'plan':json.loads(plan),'result':json.loads(result) if result else None}
        put_portable_jobs(state,jobs)
        content_hash = digest(state.model_dump(mode='json'))
        if operation_id:
            existing = db.execute('SELECT content_hash,revision FROM commits WHERE branch_id=? AND operation_id=?', (branch, operation_id)).fetchone()
            if existing:
                if existing[0] != content_hash:
                    raise OperationConflict('Operation id was already committed with different state')
                return existing[1]
        if exclusive and previous:
            raise FileExistsError(branch)
        if expected_revision is not None and (previous[0] if previous else 0) != expected_revision:
            raise BranchRevisionConflict('世界线已更新，请刷新后重试')
        if memory_actions:
            from mrp.storage.memory_transactions import apply_memory_actions, hydrate_memory_result
            from mrp.orchestrator.worldline_state import record_persisted_head
            applied = apply_memory_actions(db, branch, memory_actions)
            record_persisted_head(state, applied['watermark'])
            if command_receipt is not None:
                hydrated = hydrate_memory_result(command_receipt['result'], applied['records'])
                if isinstance(command_receipt['result'], dict) and isinstance(hydrated, dict):
                    command_receipt['result'].clear()
                    command_receipt['result'].update(hydrated)
                else:
                    command_receipt['result'] = hydrated
                entries[command_receipt['operation_id']]['result'] = hydrated
                put_portable_receipts(state, entries)
            outbox_events = hydrate_memory_result(list(outbox_events), applied['records'])
            jobs = {op:{'fingerprint':fp,'status':status,'plan':json.loads(plan),'result':json.loads(result) if result else None} for op,fp,status,plan,result in db.execute('SELECT operation_id,fingerprint,status,plan,result FROM memory_jobs WHERE branch_id=?',(branch,))}
            put_portable_jobs(state,jobs)
            content_hash = digest(state.model_dump(mode='json'))
        raw = state.model_dump(mode='json')
        header = {key: value for key, value in raw.items() if key not in self.COLLECTIONS}
        now = time.time()
        header_hash = self._put(db, self._encode(db, header))
        db.execute('INSERT INTO branches VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET story_id=excluded.story_id,revision=excluded.revision,active=excluded.active,header_hash=excluded.header_hash,summary=excluded.summary,committed_at=excluded.committed_at,source_hash=COALESCE(excluded.source_hash,branches.source_hash)',
                   (branch, state.meta.story_id or branch, state.meta.branch_revision, int(active), header_hash, canonical(summary), now, source_hash))
        keys = set()
        for kind in self.COLLECTIONS:
            collection = raw.get(kind, {}) if kind in self.DICT_COLLECTIONS else raw.get(kind, [])
            iterator = collection.items() if isinstance(collection, dict) else enumerate(collection)
            for position, (key, item) in enumerate(iterator):
                if kind == 'generation_operations' and key in (RECEIPT_KEY, CLAIM_KEY, JOB_KEY):
                    continue  # SQL table is authoritative; JSON is interchange only.
                item_key = str(key) if isinstance(collection, dict) else str(item.get('id', key))
                if (kind, item_key) in keys:
                    raise ValueError(f'Duplicate {kind} identity; refusing lossy persistence')
                material = self._put(db, self._encode(db, item))
                keys.add((kind, item_key))
                db.execute('INSERT INTO components VALUES(?,?,?,?,?) ON CONFLICT(branch_id,kind,item_key) DO UPDATE SET position=excluded.position,material_hash=excluded.material_hash WHERE position!=excluded.position OR material_hash!=excluded.material_hash',
                           (branch, kind, item_key, position, material))
                if kind in ('turn_runs','conversation_runs'):
                    self._index_run(db,branch,kind,item_key,position,material,item)
                if kind == 'messages':
                    db.execute('INSERT INTO message_lookup VALUES(?,?,?,?,?) ON CONFLICT(branch_id,message_id) DO UPDATE SET seq=excluded.seq,turn=excluded.turn,input_group_id=excluded.input_group_id WHERE seq!=excluded.seq OR turn!=excluded.turn OR input_group_id IS NOT excluded.input_group_id',
                               (branch, item_key, item['seq'], item['turn'], item.get('input_group_id')))
        for kind, key in db.execute('SELECT kind,item_key FROM components WHERE branch_id=?', (branch,)).fetchall():
            if (kind, key) not in keys:
                db.execute('DELETE FROM components WHERE branch_id=? AND kind=? AND item_key=?', (branch, kind, key))
                if kind in ('turn_runs','conversation_runs'):
                    db.execute('DELETE FROM run_lookup WHERE branch_id=? AND kind=? AND run_id=?',(branch,kind,key))
                    db.execute('DELETE FROM run_messages WHERE branch_id=? AND kind=? AND run_id=?',(branch,kind,key))
                if kind == 'messages':
                    db.execute('DELETE FROM message_lookup WHERE branch_id=? AND message_id=?', (branch, key))
        db.execute('INSERT INTO commits VALUES(?,?,?,?,?) ON CONFLICT(branch_id,revision) DO UPDATE SET content_hash=excluded.content_hash',
                   (branch, state.meta.branch_revision, operation_id, content_hash, now))
        if memory_actions:
            db.execute('INSERT INTO commit_memory_effects VALUES(?,?,?)',
                       (branch, state.meta.branch_revision, canonical(list(memory_actions))))
        for event in outbox_events:
            db.execute('INSERT INTO outbox(branch_id,revision,event) VALUES(?,?,?)', (branch, state.meta.branch_revision, canonical(event)))
        for operation, receipt in entries.items():
            recorded = self.lookup_command(branch, operation, db=db)
            if recorded is not None:
                if recorded != public_receipt(receipt):
                    raise CommandConflict('导入或保存不能改写已提交命令结果')
            else:
                db.execute('INSERT INTO command_receipts VALUES(?,?,?,?,?,?)',
                           (branch, operation, 1, receipt['fingerprint'], canonical(receipt['result']), receipt['revision']))
        for operation, fingerprint in claims.items():
            db.execute('INSERT INTO generation_claims VALUES(?,?,?,1) ON CONFLICT(branch_id,operation_id) DO NOTHING', (branch, operation, fingerprint))
        for operation,job in jobs.items():
            db.execute('INSERT INTO memory_jobs VALUES(?,?,?,?,?,?,1) ON CONFLICT(branch_id,operation_id) DO NOTHING',
                (branch,operation,job['fingerprint'],job['status'],canonical(job.get('plan',{})),canonical(job.get('result')) if job.get('result') is not None else None))
        return state.meta.branch_revision

    def load(self, branch_id: str, *, active_only=True, db=None) -> SessionState | None:
        if db is None:
            with self.connect() as connection:
                connection.execute('BEGIN')
                return self.load(branch_id, active_only=active_only, db=connection)
        row = db.execute('SELECT header_hash,active FROM branches WHERE id=?', (branch_id,)).fetchone()
        if row is None or (active_only and row[1] != 1):
            return None
        raw = self._decode(db, {'__mrp_material_v1__': row[0]})
        for kind in self.COLLECTIONS:
            raw[kind] = {} if kind in self.DICT_COLLECTIONS else []
        for kind, key, material in db.execute('SELECT kind,item_key,material_hash FROM components WHERE branch_id=? ORDER BY kind,position', (branch_id,)):
            item = self._decode(db, {'__mrp_material_v1__': material})
            if kind in self.DICT_COLLECTIONS:
                raw[kind][key] = item
            else:
                raw[kind].append(item)
        state = SessionState.model_validate(raw)
        receipts = self._receipt_entries(db, branch_id)
        put_portable_receipts(state, receipts)
        claims = {}
        for operation, fingerprint, version in db.execute('SELECT operation_id,fingerprint,version FROM generation_claims WHERE branch_id=?', (branch_id,)):
            if version != 1:
                raise CommandConflict('生成尝试凭据版本不支持')
            claims[operation] = fingerprint
        put_portable_claims(state, claims)
        jobs={operation:{'fingerprint':fp,'status':status,'plan':json.loads(plan),'result':json.loads(result) if result else None}
              for operation,fp,status,plan,result in db.execute('SELECT operation_id,fingerprint,status,plan,result FROM memory_jobs WHERE branch_id=?',(branch_id,))}
        put_portable_jobs(state,jobs)
        return state

    def summaries(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT summary FROM branches WHERE active=1')]

    def delete(self, branch_id):
        with self.transaction() as db:
            story=db.execute('SELECT story_id FROM branches WHERE id=?',(branch_id,)).fetchone()
            if story: self.require_lifecycle_access(db,story[0])
            existed = bool(db.execute('SELECT 1 FROM branches WHERE id=? AND active=1', (branch_id,)).fetchone())
            db.execute('UPDATE branches SET active=-1 WHERE id=?', (branch_id,))
            db.execute('DELETE FROM components WHERE branch_id=?', (branch_id,))
            db.execute('DELETE FROM message_lookup WHERE branch_id=?', (branch_id,))
            db.execute('DELETE FROM commits WHERE branch_id=?', (branch_id,))
            db.execute('DELETE FROM commit_memory_effects WHERE branch_id=?', (branch_id,))
            db.execute('DELETE FROM command_receipts WHERE branch_id=?', (branch_id,))
            db.execute('DELETE FROM outbox WHERE branch_id=?', (branch_id,))
            return existed

    def restore(self, state, summary):
        """Unobserved failed-write compensation, fenced to the direct successor.

        Never retain events or idempotency keys from the withdrawn head. Once
        events were acknowledged, callers need a new compensating revision.
        """
        with self.transaction() as db:
            revision = db.execute('SELECT revision FROM branches WHERE id=? AND active=1', (state.meta.id,)).fetchone()
            expected = state.meta.branch_revision + 1
            if not revision or revision[0] != expected:
                raise BranchRevisionConflict('不能覆盖已推进的世界线')
            if db.execute('SELECT 1 FROM commit_memory_effects WHERE branch_id=? AND revision>? LIMIT 1', (state.meta.id, state.meta.branch_revision)).fetchone():
                raise BranchRevisionConflict('包含已提交记忆变化的提交不能只回退故事；需要新补偿修订')
            if db.execute('SELECT 1 FROM outbox WHERE branch_id=? AND revision>? AND delivered=1 LIMIT 1', (state.meta.id, state.meta.branch_revision)).fetchone():
                raise BranchRevisionConflict('已通知的提交不能回退；需要新补偿修订')
            db.execute('DELETE FROM outbox WHERE branch_id=? AND revision>?', (state.meta.id, state.meta.branch_revision))
            db.execute('DELETE FROM commits WHERE branch_id=? AND revision>?', (state.meta.id, state.meta.branch_revision))
            db.execute('DELETE FROM command_receipts WHERE branch_id=? AND revision>?', (state.meta.id, state.meta.branch_revision))
            self.write(state, summary, expected_revision=expected, db=db)

    def lookup_command(self, branch_id, operation_id, *, db=None):
        if db is None:
            with self.connect() as connection:
                if not connection.execute('SELECT 1 FROM branches WHERE id=? AND active=1', (branch_id,)).fetchone():
                    return None
                return self.lookup_command(branch_id, operation_id, db=connection)
        row = db.execute('SELECT version,fingerprint,result,revision FROM command_receipts WHERE branch_id=? AND operation_id=?', (branch_id, operation_id)).fetchone()
        if row is None:
            return None
        if row[0] != 1:
            raise CommandConflict('命令凭据版本不支持')
        return {'fingerprint': row[1], 'result': json.loads(row[2]), 'revision': row[3]}

    def reserve_generation(self, branch_id, operation_id, fingerprint):
        with self.transaction() as db:
            story = db.execute('SELECT story_id FROM branches WHERE id=?', (branch_id,)).fetchone()
            if story:
                self.require_lifecycle_access(db, story[0])
            if not db.execute('SELECT 1 FROM branches WHERE id=? AND active=1', (branch_id,)).fetchone():
                raise BranchRevisionConflict('生成目标未发布或已删除')
            if db.execute('SELECT 1 FROM memory_jobs WHERE branch_id=? AND operation_id=?',(branch_id,operation_id)).fetchone():
                raise CommandConflict('操作ID已被记忆任务协议占用')
            old = db.execute('SELECT fingerprint,version FROM generation_claims WHERE branch_id=? AND operation_id=?', (branch_id, operation_id)).fetchone()
            if old:
                if old != (fingerprint, 1):
                    raise CommandConflict('生成操作 ID 已用于不同请求')
                raise GenerationIncomplete('生成尝试已经启动；完整结果未确认，不能自动重复模型调用')
            db.execute('INSERT INTO generation_claims VALUES(?,?,?,1)', (branch_id, operation_id, fingerprint))

    def lookup_generation(self, branch_id, operation_id):
        with self.connect() as db:
            row = db.execute('SELECT g.fingerprint,g.version FROM generation_claims g JOIN branches b ON b.id=g.branch_id WHERE g.branch_id=? AND g.operation_id=? AND b.active=1', (branch_id, operation_id)).fetchone()
            if row is None:
                return None
            if row[1] != 1:
                raise CommandConflict('生成尝试凭据版本不支持')
            return row[0]

    @staticmethod
    def _receipt_entries(db, branch_id):
        entries = {}
        for operation, version, fingerprint, result, revision in db.execute('SELECT operation_id,version,fingerprint,result,revision FROM command_receipts WHERE branch_id=?', (branch_id,)):
            if version != 1:
                raise CommandConflict('命令凭据版本不支持')
            entries[operation] = {'fingerprint': fingerprint, 'result': json.loads(result), 'revision': revision}
        return entries

    def pending_events(self, limit=100):
        with self.connect() as db:
            return [{'id': row[0], 'branch_id': row[1], 'revision': row[2], 'event': json.loads(row[3])}
                    for row in db.execute('SELECT id,branch_id,revision,event FROM outbox WHERE delivered=0 ORDER BY id LIMIT ?', (limit,))]

    def acknowledge_event(self, event_id):
        with self.transaction() as db:
            db.execute('UPDATE outbox SET delivered=1 WHERE id=?', (event_id,))

    def save_exists(self, save_id):
        with self.connect() as db:
            return bool(db.execute('SELECT 1 FROM story_saves WHERE id=? AND active=1', (save_id,)).fetchone())

    def save_owns(self, save_id):
        with self.connect() as db:
            return bool(db.execute('SELECT 1 FROM story_saves WHERE id=? AND active!=0', (save_id,)).fetchone())

    def owned_save_ids(self):
        with self.connect() as db:
            return {row[0] for row in db.execute('SELECT id FROM story_saves WHERE active!=0')}

    def put_save(self, save_id, save, summary, *, active=True, db=None):
        if db is None:
            with self.transaction() as connection:
                return self.put_save(save_id, save, summary, active=active, db=connection)
        self.require_lifecycle_access(db, save.state.meta.story_id or save.state.meta.id)
        from .creation_operations import scope_for, claim
        if scope_for(self):
            claim(self, 'save', save_id, db=db)
            active = False
        parent=db.execute('SELECT active FROM branches WHERE id=?',(save.state.meta.id,)).fetchone()
        if parent and parent[0] == -1:
            raise BranchRevisionConflict('路线已删除，拒绝迟到存档')
        key = self._put(db, self._encode(db, save.model_dump(mode='json')))
        db.execute('INSERT INTO story_saves VALUES(?,?,?,?,?)', (save_id, save.state.meta.id, key, canonical(summary), int(active)))

    def load_save(self, save_id):
        from mrp.shared.models import SaveFile
        with self.connect() as db:
            row = db.execute('SELECT material_hash FROM story_saves WHERE id=? AND active=1', (save_id,)).fetchone()
            return SaveFile.model_validate(self._decode(db, {'__mrp_material_v1__': row[0]})) if row else None

    def save_summaries(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT summary FROM story_saves WHERE active=1')]

    def delete_save(self, save_id):
        with self.transaction() as db:
            story=db.execute('SELECT b.story_id FROM story_saves s JOIN branches b ON b.id=s.branch_id WHERE s.id=?',(save_id,)).fetchone()
            if story: self.require_lifecycle_access(db,story[0])
            return bool(db.execute('UPDATE story_saves SET active=-1 WHERE id=? AND active=1', (save_id,)).rowcount)

    def read_window(self, branch_id, *, limit=100, before_seq=None, around=None):
        """Indexed bounded read; it never resolves historical material collections."""
        limit = max(1, min(int(limit), 100))
        with self.connect() as db:
            db.execute('BEGIN')
            head = db.execute('SELECT header_hash FROM branches WHERE id=? AND active=1', (branch_id,)).fetchone()
            if not head:
                return None
            raw = self._view_material(db, head[0])
            global_turn, latest_seq = db.execute('SELECT COALESCE(MAX(turn),0),MAX(seq) FROM message_lookup WHERE branch_id=?', (branch_id,)).fetchone()
            boundary = before_seq
            if around:
                anchor = db.execute('SELECT seq FROM message_lookup WHERE branch_id=? AND message_id=?', (branch_id, around)).fetchone()
                if anchor is None:
                    raise ValueError('定位消息不存在')
                following = db.execute('SELECT seq FROM message_lookup WHERE branch_id=? AND seq>=? ORDER BY seq LIMIT ?', (branch_id, anchor[0], max(1, limit // 2))).fetchall()
                boundary = following[-1][0] + 1 if following else anchor[0] + 1
            query = 'SELECT message_id,seq,input_group_id FROM message_lookup WHERE branch_id=?'
            args = [branch_id]
            if boundary is not None:
                query += ' AND seq<?'
                args.append(boundary)
            selected = db.execute(query + ' ORDER BY seq DESC LIMIT ?', (*args, limit)).fetchall()
            ids = {row[0] for row in selected}
            if selected:
                for group in {selected[0][2], selected[-1][2]} - {None, ''}:
                    ids.update(row[0] for row in db.execute('SELECT message_id FROM message_lookup WHERE branch_id=? AND input_group_id=?', (branch_id, group)))
            raw['messages'] = []
            if ids:
                placeholders = ','.join('?' for _ in ids)
                rows = db.execute(f"SELECT c.material_hash FROM components c JOIN message_lookup m ON m.branch_id=c.branch_id AND m.message_id=c.item_key WHERE c.branch_id=? AND c.kind='messages' AND c.item_key IN ({placeholders}) ORDER BY m.seq", (branch_id, *ids))
                raw['messages'] = [self._view_material(db, row[0]) for row in rows]
            allowed = {'characters', 'groups', 'player_identities', 'pinned_facts', 'scenes'}
            for kind in self.COLLECTIONS:
                if kind != 'messages':
                    raw[kind] = {} if kind in self.DICT_COLLECTIONS else []
            marks = ','.join('?' for _ in allowed)
            for kind, key in db.execute(f'SELECT kind,material_hash FROM components WHERE branch_id=? AND kind IN ({marks}) ORDER BY kind,position', (branch_id, *allowed)):
                raw[kind].append(self._view_material(db, key))
            page_ids=[message['id'] for message in raw['messages']]
            operations=list({message.get('operation_id') for message in raw['messages']} - {None})
            for kind in ('turn_runs','conversation_runs'):
                selected=set()
                if page_ids:
                    marks=','.join('?' for _ in page_ids)
                    selected.update(row[0] for row in db.execute(f'SELECT DISTINCT run_id FROM run_messages WHERE branch_id=? AND kind=? AND message_id IN ({marks})',(branch_id,kind,*page_ids)))
                if operations:
                    marks=','.join('?' for _ in operations)
                    selected.update(row[0] for row in db.execute(f'SELECT run_id FROM run_lookup WHERE branch_id=? AND kind=? AND operation_id IN ({marks})',(branch_id,kind,*operations)))
                latest=db.execute("SELECT run_id FROM run_lookup WHERE branch_id=? AND kind=? AND status IN ('queued','running','paused','awaiting_user','awaiting_director','interrupted','failed') ORDER BY position DESC LIMIT 1",(branch_id,kind)).fetchone()
                if latest: selected.add(latest[0])
                if selected:
                    marks=','.join('?' for _ in selected)
                    raw[kind]=[self._view_material(db,row[0]) for row in db.execute(f'SELECT material_hash FROM run_lookup WHERE branch_id=? AND kind=? AND run_id IN ({marks}) ORDER BY position',(branch_id,kind,*selected))]
            first = min((row['seq'] for row in raw['messages']), default=None)
            has_older = first is not None and db.execute('SELECT 1 FROM message_lookup WHERE branch_id=? AND seq<? LIMIT 1', (branch_id, first)).fetchone()
            return SessionState.model_validate(raw), int(global_turn), latest_seq, first if has_older else None

    @staticmethod
    def _index_run(db,branch,kind,identity,position,material,item):
        prior=db.execute('SELECT material_hash,position FROM run_lookup WHERE branch_id=? AND kind=? AND run_id=?',(branch,kind,identity)).fetchone()
        if prior == (material,position): return
        db.execute('INSERT INTO run_lookup VALUES(?,?,?,?,?,?,?) ON CONFLICT(branch_id,kind,run_id) DO UPDATE SET operation_id=excluded.operation_id,status=excluded.status,position=excluded.position,material_hash=excluded.material_hash',
            (branch,kind,identity,item['operation_id'],item['status'],position,material))
        db.execute('DELETE FROM run_messages WHERE branch_id=? AND kind=? AND run_id=?',(branch,kind,identity))
        from mrp.shared.run_windows import run_message_ids
        ids=run_message_ids(item)
        for message in ids-{None}:
            db.execute('INSERT OR IGNORE INTO run_messages VALUES(?,?,?,?)',(branch,kind,identity,message))

    def read_setup(self, branch_id):
        with self.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT header_hash FROM branches WHERE id=? AND active=1', (branch_id,)).fetchone()
            if row is None:
                return None
            header = self._view_material(db, row[0])
            result = {'meta': header['meta'], 'characters': [], 'lorebooks': []}
            for kind, key in db.execute("SELECT kind,material_hash FROM components WHERE branch_id=? AND kind IN ('characters','lorebooks') ORDER BY kind,position", (branch_id,)):
                if kind == 'characters':
                    result[kind].append(self._view_material(db, key))
                else:
                    # SQLite parses only this one book payload, never historical
                    # state or message materials. Body does not leave the DB.
                    book = db.execute("SELECT json_extract(payload,'$.id'),json_extract(payload,'$.name'),json_extract(payload,'$.source_format'),json_array_length(json_extract(payload,'$.entries')) FROM materials WHERE hash=?", (key,)).fetchone()
                    result[kind].append({'id': book[0], 'name': book[1], 'source_format': book[2], 'entry_count': book[3] or 0, 'entries': []})
            return result
