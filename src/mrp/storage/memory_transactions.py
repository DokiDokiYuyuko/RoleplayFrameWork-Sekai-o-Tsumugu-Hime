"""Memory SQL actions composed into a caller-owned story transaction.

No models, embeddings, files, commits or connection ownership live here.
"""
import json
import struct

from mrp.orchestrator.memory import MemoryStore, MEMORY_DETAILS
from mrp.shared.models import MemoryRecord


class MemoryConflict(ValueError):
    command_conflict = True
    code = "memory_revision_conflict"


def hydrate_memory_result(value, records):
    if isinstance(value, dict):
        if {"id", "character_id", "session_id", "content"} <= value.keys() and value["id"] in records:
            return dict(records[value["id"]])
        return {key: hydrate_memory_result(item, records) for key, item in value.items()}
    if isinstance(value, list):
        return [hydrate_memory_result(item, records) for item in value]
    return value


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _clock(db, branch):
    row = db.execute('SELECT last_seq FROM memory_branch_clock WHERE session_id=?', (branch,)).fetchone()
    return int(row[0]) if row else 0


def _next(db, branch):
    value = _clock(db, branch) + 1
    db.execute('INSERT INTO memory_branch_clock VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET last_seq=excluded.last_seq', (branch, value))
    return value


def _remove(db, branch, record_id, tables):
    row = db.execute('SELECT rowid,character_id FROM memory_records WHERE id=? AND session_id=?', (record_id, branch)).fetchone()
    if row is None:
        return None
    for table in ('memory_vec', 'memory_vec_branch', 'memory_fts'):
        if table in tables:
            db.execute(f'DELETE FROM {table} WHERE rowid=?', (row[0],))
    db.execute('DELETE FROM memory_records WHERE id=? AND session_id=?', (record_id, branch))
    return row[1]


def _insert(db, branch, prepared, tables):
    record = MemoryRecord.model_validate(prepared['record'])
    if record.session_id != branch:
        raise ValueError('记忆不属于目标路线')
    record.commit_seq = _next(db, branch)
    if record.effective_message_id is None and record.source_message_ids:
        record.effective_message_id = record.source_message_ids[-1]
    values = (record.id, record.character_id, branch, record.turn_start, record.turn_end, record.kind,
        record.content, json.dumps(record.source_message_ids), record.created_at.isoformat(),
        json.dumps(record.participants), json.dumps(record.keywords), record.importance, record.scene_id,
        record.effective_message_id or '', record.commit_seq, record.inherited_from_id or '', int(record.invalidated),
        json.dumps(record.model_dump(mode='json', include=MEMORY_DETAILS), ensure_ascii=False))
    rowid = db.execute(f"INSERT INTO memory_records ({MemoryStore._RECORD_COLS}) VALUES ({','.join('?' for _ in values)})", values).lastrowid
    db.execute('INSERT INTO memory_fts(rowid,content,character_id) VALUES(?,?,?)',
        (rowid, record.content + ' ' + ' '.join(record.keywords), record.character_id))
    vector = prepared.get('embedding')
    if vector is not None:
        packed = struct.pack(f'<{len(vector)}f', *vector)
        if 'memory_vec' in tables:
            db.execute('INSERT INTO memory_vec(rowid,record_id,character_id,embedding) VALUES(?,?,?,?)',
                (rowid, record.id, record.character_id, packed))
        if 'memory_vec_branch' in tables:
            db.execute('INSERT INTO memory_vec_branch(rowid,record_id,character_id,session_id,embedding) VALUES(?,?,?,?,?)',
                (rowid, record.id, record.character_id, branch, packed))
    return record.model_dump(mode='json')


def apply_memory_actions(db, branch, actions):
    tables = _tables(db)
    if {'memory_vec', 'memory_vec_branch'} & tables:
        # Loading a local SQLite extension has no network or model side effects.
        import sqlite_vec
        db.enable_load_extension(True)
        try:
            sqlite_vec.load(db)
        finally:
            db.enable_load_extension(False)
    touched, committed = set(), {}
    for action in actions:
        kind = action['kind']
        expected = action.get('expected_memory_watermark')
        if expected is not None and expected != _clock(db, branch):
            raise MemoryConflict('记忆已改变，请刷新后重试')
        if kind == 'invalidate_sources':
            ids = MemoryStore.invalidate_sources_transaction(db, branch, set(action['message_ids']))
            for record_id in ids:
                row = db.execute('SELECT character_id FROM memory_records WHERE id=?', (record_id,)).fetchone()
                if row:
                    touched.add(row[0])
        elif kind in {'insert_records', 'revise_record', 'delete_record'}:
            if kind != 'insert_records':
                row = db.execute(f'SELECT {MemoryStore._RECORD_COLS} FROM memory_records WHERE id=? AND session_id=?', (action['record_id'], branch)).fetchone()
                if row is None:
                    raise MemoryConflict('记忆记录已不存在')
                previous = MemoryStore._row_to_record(row)
                if previous.character_id != action['character_id']:
                    raise MemoryConflict('记忆角色归属已改变')
                if action.get('expected_revision') is not None and previous.revision != action['expected_revision']:
                    raise MemoryConflict('记忆已更新，请刷新后重试')
                touched.add(_remove(db, branch, previous.id, tables))
            for prepared in action.get('records', []):
                result = _insert(db, branch, prepared, tables)
                touched.add(result['character_id'])
                committed[result['id']] = result
        elif kind == 'replace_snapshot':
            for record_id, actor in db.execute('SELECT id,character_id FROM memory_records WHERE session_id=?', (branch,)).fetchall():
                _remove(db, branch, record_id, tables)
                touched.add(actor)
            db.execute('DELETE FROM memory_windows WHERE session_id=?', (branch,))
            # An empty complete snapshot is still a new historical checkpoint.
            _next(db, branch)
            for prepared in action.get('records', []):
                prepared = {**prepared, 'record':dict(prepared['record'])}
                maximum = db.execute("SELECT MAX(COALESCE(json_extract(details,'$.revision'),1)) FROM memory_history WHERE session_id=? AND id=?",
                    (branch, prepared['record']['id'])).fetchone()[0]
                prepared['record']['revision'] = max(prepared['record'].get('revision', 1), maximum or 0) + 1
                result = _insert(db, branch, prepared, tables)
                touched.add(result['character_id'])
                committed[result['id']] = result
        elif kind == 'complete_window':
            actor, start, end = action['character_id'], action['start'], action['end']
            old = [MemoryStore._row_to_record(row) for row in db.execute(f'SELECT {MemoryStore._RECORD_COLS} FROM memory_records WHERE session_id=? AND character_id=?', (branch, actor))]
            hashes = action['fingerprints']
            fresh = [MemoryRecord.model_validate(item['record']) for item in action['records']]
            fresh_keys = {(r.category, r.content, tuple(sorted(r.source_message_ids))) for r in fresh}
            for previous in old:
                if (previous.kind in {'episodic', 'summary', 'fact'} and not previous.manually_revised
                    and not previous.invalidated and previous.source_message_ids and set(previous.source_message_ids) <= hashes.keys()
                    and (previous.category, previous.content, tuple(sorted(previous.source_message_ids))) not in fresh_keys):
                    # The history UPDATE trigger closes the old version and
                    # advances the clock. Give the live record that same sequence.
                    previous.revision += 1
                    details = previous.model_dump(mode='json', include=MEMORY_DETAILS)
                    db.execute('UPDATE memory_records SET invalidated=1,commit_seq=?,details=? WHERE id=? AND session_id=?',
                        (_clock(db, branch) + 1, json.dumps(details, ensure_ascii=False), previous.id, branch))
            accepted = []
            for prepared, item in zip(action['records'], fresh):
                if item.character_id != actor or item.session_id != branch:
                    raise ValueError('整理记忆归属不匹配')
                if any(r.manually_revised and set(r.source_message_ids) == set(item.source_message_ids) and r.category == item.category for r in old):
                    continue
                if any(not r.invalidated and r.content == item.content and r.category == item.category and r.source_fingerprints == item.source_fingerprints for r in old):
                    continue
                result = _insert(db, branch, prepared, tables)
                accepted.append(result['id'])
                committed[result['id']] = result
            _next(db, branch)
            db.execute("INSERT INTO memory_windows VALUES(?,?,?,?,?,?,?,'') ON CONFLICT(session_id,character_id,turn_start,turn_end) DO UPDATE SET status='complete',fingerprints=excluded.fingerprints,record_ids=excluded.record_ids,error=''",
                (branch, actor, start, end, 'complete', json.dumps(hashes), json.dumps(accepted)))
            touched.add(actor)
        elif kind == 'purge_branch_rows':
            if action.get('mode') != 'destroy':
                raise ValueError('永久清理必须明确销毁历史')
            for record_id, actor in db.execute('SELECT id,character_id FROM memory_records WHERE session_id=?', (branch,)).fetchall():
                _remove(db, branch, record_id, tables)
                touched.add(actor)
            for table in ('memory_windows', 'memory_history', 'memory_history_floor', 'memory_branch_clock', 'memory_generation_recovery'):
                if table in tables:
                    db.execute(f'DELETE FROM {table} WHERE session_id=?', (branch,))
        elif kind == 'finish_memory_job':
            row = db.execute('SELECT fingerprint,status FROM memory_jobs WHERE branch_id=? AND operation_id=?',
                (branch, action['operation_id'])).fetchone()
            if row is None or row[0] != action['fingerprint'] or row[1] != 'pending':
                raise MemoryConflict('整理任务身份或状态已改变')
            result = hydrate_memory_result(action['result'], committed)
            db.execute("UPDATE memory_jobs SET status='committed',result=? WHERE branch_id=? AND operation_id=?",
                (json.dumps(result, ensure_ascii=False), branch, action['operation_id']))
        elif kind == 'fail_memory_job':
            db.execute("UPDATE memory_jobs SET status='failed',result=? WHERE branch_id=? AND operation_id=? AND fingerprint=? AND status='pending'",
                (json.dumps(action['result'], ensure_ascii=False), branch, action['operation_id'], action['fingerprint']))
        elif kind == 'fail_window':
            _next(db, branch)
            db.execute("INSERT INTO memory_windows(session_id,character_id,turn_start,turn_end,status,fingerprints,record_ids,error) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(session_id,character_id,turn_start,turn_end) DO UPDATE SET status=excluded.status,error=excluded.error",
                (branch, action['character_id'], action['start'], action['end'], action.get('status','failed'),
                    json.dumps(action['fingerprints']), '[]', action['error']))
        elif kind == 'import_history':
            payload = action['payload']
            floor, archive_clock = int(payload['floor']), int(payload.get('clock', payload['floor']))
            if floor < 0 or archive_clock < floor:
                raise ValueError('历史记忆水位无效')
            versions, intervals, maxima = [], {}, {}
            for version in payload['versions']:
                record = MemoryRecord.model_validate(version['record'])
                start, end = int(version['valid_from']), version['valid_until']
                end = int(end) if end is not None else None
                if record.session_id != branch or start < floor or start > archive_clock or (end is not None and (end <= start or end > archive_clock)):
                    raise ValueError('历史记忆区间无效')
                intervals.setdefault(record.id, []).append((start,end))
                maxima[record.id] = max(maxima.get(record.id, 0), record.revision)
                values = (record.id, record.character_id, branch, record.turn_start, record.turn_end, record.kind,
                    record.content, json.dumps(record.source_message_ids), record.created_at.isoformat(),
                    json.dumps(record.participants), json.dumps(record.keywords), record.importance, record.scene_id,
                    record.effective_message_id or '', record.commit_seq, record.inherited_from_id or '', int(record.invalidated),
                    json.dumps(record.model_dump(mode='json', include=MEMORY_DETAILS)))
                versions.append((values,start,end))
            for entries in intervals.values():
                ordered = sorted(entries)
                if any(left[1] is None or left[1] > right[0] for left,right in zip(ordered,ordered[1:])):
                    raise ValueError('历史记忆版本相互重叠')
            current = db.execute(f'SELECT {MemoryStore._RECORD_COLS} FROM memory_records WHERE session_id=?', (branch,)).fetchall()
            existing = db.execute('SELECT id,valid_until FROM memory_history WHERE session_id=?', (branch,)).fetchall()
            if len(existing) != len(current) or any(end is not None for _,end in existing):
                raise MemoryConflict('已有可靠记忆历史，不能用归档覆盖')
            restore_clock = max(_clock(db, branch), archive_clock) + 1
            db.execute('DELETE FROM memory_history WHERE session_id=?', (branch,))
            for values,start,end in versions:
                db.execute(f"INSERT INTO memory_history ({MemoryStore._RECORD_COLS},valid_from,valid_until) VALUES ({','.join('?' for _ in range(20))})",
                    (*values,start,end if end is not None else restore_clock))
            db.execute('INSERT INTO memory_history_floor VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET first_seq=excluded.first_seq', (branch,floor))
            db.execute('INSERT INTO memory_branch_clock VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET last_seq=excluded.last_seq', (branch,restore_clock-1))
            for row in current:
                record = MemoryStore._row_to_record(row)
                record.revision = max(record.revision,maxima.get(record.id,0)) + 1
                sequence = _clock(db,branch) + 1
                db.execute('UPDATE memory_records SET commit_seq=?,details=? WHERE id=? AND session_id=?',
                    (sequence,json.dumps(record.model_dump(mode='json',include=MEMORY_DETAILS)),record.id,branch))
                record.commit_seq = sequence
                committed[record.id] = record.model_dump(mode='json')
                touched.add(record.character_id)
            if not current:
                _next(db,branch)
        elif kind == 'declare_snapshot_history':
            floor = _clock(db,branch)
            db.execute('DELETE FROM memory_history WHERE session_id=?', (branch,))
            db.execute(f'INSERT INTO memory_history ({MemoryStore._RECORD_COLS},valid_from,valid_until) SELECT {MemoryStore._RECORD_COLS},?,NULL FROM memory_records WHERE session_id=?', (floor,branch))
            db.execute('INSERT INTO memory_history_floor VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET first_seq=excluded.first_seq', (branch,floor))
        else:
            raise ValueError('Unsupported transactional memory action: ' + kind)
    return {'watermark': _clock(db, branch), 'touched_actors': sorted(touched), 'records': committed}
