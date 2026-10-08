"""Durable publication journal for operations creating isolated story entities.

Running operations are never repeated after interruption. Only a complete staged
result may be published during recovery; external work is not replayed.
"""
from contextvars import ContextVar
import asyncio
import json
import hashlib
from copy import deepcopy

active_creation = ContextVar('active_creation', default=None)


class CreationConflict(ValueError):
    command_conflict = True
    def __init__(self, message, code='operation_conflict'):
        super().__init__(message)
        self.code = code


def scope_for(database):
    scope = active_creation.get()
    return scope if scope and scope['path'] == str(database.path.resolve()) else None


def reserve(database, operation_id, action, payload):
    fingerprint = hashlib.sha256(json.dumps({'action':action, 'payload':payload},
        sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    with database.transaction() as db:
        old = db.execute('SELECT fingerprint,status,result FROM creation_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if old:
            if old[0] != fingerprint:
                raise CreationConflict('操作 ID 已用于不同的创建请求')
            if old[1] == 'result_purged':
                exc = CreationConflict('操作结果已永久清除', 'operation_result_purged')
                exc.status_code = 410
                raise exc
            if old[1] == 'published':
                return json.loads(old[2])
            raise CreationConflict('创建操作已中断或仍在执行；请先检查已有结果，再发起新操作', 'operation_incomplete')
        db.execute('INSERT INTO creation_operations VALUES(?,?,?,?,?,?)',
            (operation_id, action, fingerprint, 'running', None, 1))
    return None


def claim(database, kind, identity, *, db=None):
    scope = scope_for(database)
    if not scope:
        return
    if db is None:
        with database.transaction() as connection:
            return claim(database, kind, identity, db=connection)
    operation = scope['operation_id']
    if db.execute('SELECT status FROM creation_operations WHERE operation_id=?', (operation,)).fetchone() != ('running',):
        raise CreationConflict('创建操作已结束，拒绝迟到写入', 'operation_incomplete')
    old = db.execute('SELECT operation_id FROM creation_entities WHERE kind=? AND identity=?', (kind, identity)).fetchone()
    if old:
        if old[0] != operation:
            raise CreationConflict('实体身份已被另一创建操作占用')
        return
    table = 'branches' if kind == 'branch' else 'story_saves'
    if db.execute(f'SELECT 1 FROM {table} WHERE id=?', (identity,)).fetchone():
        raise CreationConflict('创建操作不能覆盖既有实体')
    db.execute('INSERT INTO creation_entities VALUES(?,?,?)', (operation, kind, identity))


def adopt(database, branch_id, revision):
    scope = scope_for(database)
    if not scope:
        return
    with database.transaction() as db:
        if db.execute('SELECT revision,active FROM branches WHERE id=?', (branch_id,)).fetchone() != (revision, 1):
            raise CreationConflict('既有目标已变化或需先迁移到事务存储')
        db.execute('INSERT INTO creation_adoptions VALUES(?,?,?)', (scope['operation_id'], branch_id, revision))


def finish(database, operation_id, result):
    serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
    with database.transaction() as db:
        if not db.execute("UPDATE creation_operations SET status='staged',result=? WHERE operation_id=? AND status='running'", (serialized, operation_id)).rowcount:
            raise CreationConflict('创建操作状态已变化')
    publish(database, operation_id)


def publish(database, operation_id):
    with database.transaction() as db:
        row = db.execute('SELECT status,result FROM creation_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if not row or row[0] != 'staged' or row[1] is None:
            raise CreationConflict('创建操作尚无完整发布结果', 'operation_incomplete')
        entities = db.execute('SELECT kind,identity FROM creation_entities WHERE operation_id=?', (operation_id,)).fetchall()
        adoptions = db.execute('SELECT branch_id,revision FROM creation_adoptions WHERE operation_id=?', (operation_id,)).fetchall()
        for identity, revision in adoptions:
            if db.execute('SELECT revision,active FROM branches WHERE id=?', (identity,)).fetchone() != (revision, 1):
                raise CreationConflict('既有目标已变化，拒绝伪装创建成功')
        if not entities and not adoptions:
            raise CreationConflict('创建操作没有持久实体', 'operation_incomplete')
        for kind, identity in entities:
            if kind == 'branch':
                story = db.execute('SELECT story_id FROM branches WHERE id=?',(identity,)).fetchone()
            else:
                story = db.execute('SELECT b.story_id FROM story_saves s JOIN branches b ON b.id=s.branch_id WHERE s.id=?',(identity,)).fetchone()
            if story: database.require_lifecycle_access(db,story[0])
            table = 'branches' if kind == 'branch' else 'story_saves'
            if not db.execute(f'UPDATE {table} SET active=1 WHERE id=? AND active=0', (identity,)).rowcount:
                raise CreationConflict('创建材料不完整，拒绝部分发布', 'operation_incomplete')
        imported = db.execute('SELECT payload FROM creation_imported_receipts WHERE operation_id=?', (operation_id,)).fetchone()
        if imported:
            restore_receipts(database, json.loads(imported[0]), db=db)
            db.execute('DELETE FROM creation_imported_receipts WHERE operation_id=?', (operation_id,))
        db.execute("UPDATE creation_operations SET status='published' WHERE operation_id=?", (operation_id,))


def export_receipts(database, identities):
    def references(value):
        if isinstance(value, str):
            return value in identities
        if isinstance(value, dict):
            return any(references(item) for item in value.values())
        if isinstance(value, list):
            return any(references(item) for item in value)
        return False
    with database.connect() as db:
        return [{'operation_id':operation, 'action':action, 'fingerprint':fingerprint,
                 'result':json.loads(result), 'version':version}
                for operation, action, fingerprint, result, version in db.execute(
                    "SELECT operation_id,action,fingerprint,result,version FROM creation_operations WHERE status='published'")
                if references(json.loads(result))]


def restore_receipts(database, receipts, *, db=None):
    if not isinstance(receipts, list) or len(receipts) > 10000:
        raise CreationConflict('创建凭据集合格式不支持')
    for receipt in receipts:
        if (not isinstance(receipt, dict) or receipt.get('version') != 1
            or not all(isinstance(receipt.get(key), str) and receipt[key]
                for key in ('operation_id', 'action', 'fingerprint')) or 'result' not in receipt):
            raise CreationConflict('创建凭据格式不支持')
    if db is None:
        with database.transaction() as connection:
            scope = scope_for(database)
            if scope:
                connection.execute('INSERT INTO creation_imported_receipts VALUES(?,?)',
                    (scope['operation_id'], json.dumps(receipts, ensure_ascii=False, allow_nan=False)))
                return
            return restore_receipts(database, receipts, db=connection)
    for receipt in receipts:
        old = db.execute('SELECT action,fingerprint,status,result FROM creation_operations WHERE operation_id=?', (receipt['operation_id'],)).fetchone()
        proposed = (receipt['action'], receipt['fingerprint'], 'published', json.dumps(receipt['result'], ensure_ascii=False, allow_nan=False))
        if old:
            if old[:3] != proposed[:3] or json.loads(old[3]) != receipt['result']:
                raise CreationConflict('导入不能改写既有创建操作身份')
            continue
        db.execute('INSERT INTO creation_operations VALUES(?,?,?,?,?,?)', (receipt['operation_id'], *proposed, 1))


def recover(database, memory_store):
    with database.connect() as db:
        pending = db.execute("SELECT operation_id,status FROM creation_operations WHERE status IN ('running','staged')").fetchall()
    for operation, status in pending:
        if status == 'staged':
            try:
                publish(database, operation)
            except CreationConflict as exc:
                abort(database, operation, memory_store, staged_conflict=str(exc))
            continue
        abort(database, operation, memory_store)


def abort(database, operation_id, memory_store, *, staged_conflict=None):
    with database.transaction() as db:
        status = db.execute('SELECT status FROM creation_operations WHERE operation_id=?', (operation_id,)).fetchone()
        if staged_conflict and status == ('staged',):
            db.execute("UPDATE creation_operations SET status='running' WHERE operation_id=?", (operation_id,))
            db.execute('INSERT INTO creation_failure_details VALUES(?,?) ON CONFLICT(operation_id) DO UPDATE SET reason=excluded.reason', (operation_id, staged_conflict))
            status = ('running',)
        if not status or status[0] in ('published', 'staged'):
            return  # A durable complete result is recoverable, never compensated.
        entities = db.execute('SELECT kind,identity FROM creation_entities WHERE operation_id=?', (operation_id,)).fetchall()
        db.execute('DELETE FROM creation_imported_receipts WHERE operation_id=?', (operation_id,))
        db.execute('DELETE FROM creation_adoptions WHERE operation_id=?', (operation_id,))
        for kind, identity in entities:
            if kind == 'branch':
                if db.execute('SELECT active FROM branches WHERE id=?', (identity,)).fetchone() not in (None, (0,)):
                    raise CreationConflict('恢复拒绝删除已发布身份')
                db.execute('DELETE FROM components WHERE branch_id=?', (identity,))
                for table in ('message_lookup', 'commits', 'outbox', 'command_receipts', 'generation_claims', 'commit_memory_effects'):
                    db.execute(f'DELETE FROM {table} WHERE branch_id=?', (identity,))
                db.execute('DELETE FROM branches WHERE id=? AND active=0', (identity,))
            else:
                db.execute('DELETE FROM story_saves WHERE id=? AND active=0', (identity,))
        # Keep running until owned memory cleanup completes: crash retries cleanup.
    for kind, identity in entities:
        if kind == 'branch':
            memory_store.purge_branch(identity)
    with database.transaction() as db:
        db.execute("UPDATE creation_operations SET status='aborted' WHERE operation_id=? AND status='running'", (operation_id,))
        db.execute('DELETE FROM creation_entities WHERE operation_id=?', (operation_id,))


async def execute(repo, memory_store, action, payload, operation_id, create, response_meta=None, publication_gate=None):
    if operation_id is None:
        from mrp.shared.models import new_id
        operation_id = new_id('creation')
    replay = await asyncio.to_thread(reserve, repo.story_db, operation_id, action, payload)
    if replay is not None:
        if response_meta is not None:
            response_meta.update(operation_id=operation_id, replayed=True)
        result = deepcopy(replay)
        if isinstance(result, dict) and 'repeated' in result:
            result['repeated'] = True
        return result
    token = active_creation.set({'operation_id':operation_id, 'path':str(repo.story_db.path.resolve())})
    try:
        async def complete():
            from contextlib import asynccontextmanager
            @asynccontextmanager
            async def no_gate():
                yield
            async with (publication_gate() if publication_gate else no_gate()):
                result = await create()
                await asyncio.to_thread(finish, repo.story_db, operation_id, result)
                return result
        task = asyncio.create_task(complete())
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                interrupted = True
        result = task.result()
        if interrupted:
            raise asyncio.CancelledError
        if response_meta is not None:
            response_meta.update(operation_id=operation_id, replayed=False)
        return result
    except BaseException as exc:
        await asyncio.to_thread(abort, repo.story_db, operation_id, memory_store,
            staged_conflict=str(exc) if isinstance(exc, CreationConflict) else None)
        raise
    finally:
        active_creation.reset(token)
