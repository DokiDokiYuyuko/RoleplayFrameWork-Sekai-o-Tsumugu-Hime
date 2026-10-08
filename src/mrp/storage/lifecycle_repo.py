"""Repository protocol for existing-entity lifecycle and online legacy cutover.

Called by the owning server under a story gate. Offline migration still requires
the exclusive data-root lease; this protocol never starts another lease holder.
"""
from contextvars import ContextVar
import hashlib
import json
import time
from pathlib import Path
import os

from mrp.shared.models import SessionState, SaveFile
from mrp.orchestrator.migration import migrate_state_to_v3_dict, migrate_save_to_v3_dict
from mrp.storage.command_receipts import unwrap_legacy_document
from mrp.storage.atomic import write_json_atomic
from mrp.storage.session_repo import summary_from_state
from mrp.storage.save_repo import summary_from_save
from mrp.storage.json_store import FileStamp
from mrp.storage.story_sqlite import canonical, digest

writer = ContextVar('lifecycle_writer', default=None)


class LifecycleConflict(ValueError):
    command_conflict = True
    def __init__(self, message, code='lifecycle_conflict', status_code=409):
        super().__init__(message)
        self.code, self.status_code = code, status_code


class LifecycleRepo:
    conflict = LifecycleConflict

    def __init__(self, sessions, saves):
        self.sessions, self.saves, self.db = sessions, saves, sessions.story_db

    def story_for(self, branch_id):
        with self.db.connect() as connection:
            row = connection.execute('SELECT story_id FROM branches WHERE id=?', (branch_id,)).fetchone()
            if row:
                return row[0]
        state = self.sessions.load_state_readonly_sync(branch_id)
        return (state.meta.story_id or branch_id) if state is not None else branch_id

    def membership(self, story_id):
        branches = [row for row in self.sessions.list_summaries_sync() if (row.story_id or row.id) == story_id]
        ids = {row.id for row in branches}
        saves = [row for row in self.saves.list_sync() if row.session_id in ids]
        return digest({'branches':sorted((row.id, row.branch_revision) for row in branches),
                       'saves':sorted((row.id, row.session_id) for row in saves)})

    @staticmethod
    def retain_source(path, raw):
        if path.exists():
            if path.read_bytes() != raw:
                raise LifecycleConflict('冻结源文件身份冲突')
            return
        with path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def require_unlocked(self, story_id, *, connection=None):
        if connection is None:
            with self.db.connect() as db:
                return self.require_unlocked(story_id, connection=db)
        locked = connection.execute('SELECT operation_id FROM lifecycle_locks WHERE story_id=?', (story_id,)).fetchone()
        if locked and locked[0] != writer.get():
            raise LifecycleConflict('故事正在完成生命周期操作，请稍后再试', 'operation_incomplete')

    def normalize_story(self, story_id):
        rows = [row for row in self.sessions.list_summaries_sync() if (row.story_id or row.id) == story_id]
        legacy = [row for row in rows if not self.db.owns(row.id)]
        if not legacy:
            return
        operation = 'cutover:' + story_id
        token = writer.set(operation)
        from .creation_operations import active_creation
        creation_token=active_creation.set(None)
        try:
            with self.db.transaction() as connection:
                self.require_unlocked(story_id, connection=connection)
                connection.execute('INSERT INTO lifecycle_locks VALUES(?,?)', (story_id, operation))
            source = {}
            frozen = self.sessions.paths.data_root / 'online_cutovers' / story_id
            frozen.mkdir(parents=True, exist_ok=True)
            states = {}
            for row in legacy:
                path = self.sessions.path_for(row.id)
                raw = path.read_bytes()
                state = SessionState.model_validate(migrate_state_to_v3_dict(unwrap_legacy_document(json.loads(raw)))[0])
                source[str(path)] = hashlib.sha256(raw).hexdigest()
                retained = frozen / (row.id + '-' + source[str(path)] + '.json')
                self.retain_source(retained, raw)
                states[row.id] = state
            branch_ids = {row.id for row in rows}
            saved = {}
            for row in self.saves.list_sync():
                if row.session_id not in branch_ids or self.saves.story_db.save_owns(row.id):
                    continue
                path = self.saves.path_for(row.id)
                raw = path.read_bytes()
                source[str(path)] = hashlib.sha256(raw).hexdigest()
                retained = frozen / (row.id + '-' + source[str(path)] + '.json')
                self.retain_source(retained, raw)
                saved[row.id] = SaveFile.model_validate(migrate_save_to_v3_dict(json.loads(raw))[0])
            manifest = {'story_id':story_id, 'sources':source, 'branches':list(states), 'saves':list(saved),
                        'membership':self.membership(story_id)}
            with self.db.transaction() as connection:
                connection.execute('INSERT INTO online_cutovers VALUES(?,?,?) ON CONFLICT(story_id) DO UPDATE SET status=excluded.status,manifest=excluded.manifest',
                    (story_id, 'prepared', canonical(manifest)))
                for state in states.values():
                    self.db.write(state, summary_from_state(state, FileStamp(time.time(), time.time_ns(), 0)).model_dump(mode='json'), active=False, db=connection)
                for identity, save in saved.items():
                    connection.execute('DELETE FROM story_saves WHERE id=? AND active=0',(identity,))
                    self.db.put_save(identity, save, summary_from_save(identity, save, FileStamp(time.time(), time.time_ns(), 0)).model_dump(mode='json'), active=False, db=connection)
            with self.db.transaction() as connection:
                if self.membership(story_id) != manifest['membership'] or any(
                    hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected for path, expected in source.items()):
                    raise LifecycleConflict('故事在影子切换准备期间已变化')
                for identity in states:
                    connection.execute('UPDATE branches SET active=1 WHERE id=? AND active=0', (identity,))
                for identity in saved:
                    connection.execute('UPDATE story_saves SET active=1 WHERE id=? AND active=0', (identity,))
                connection.execute("UPDATE online_cutovers SET status='committed' WHERE story_id=?", (story_id,))
            self.finish_cutover(manifest)
        except BaseException:
            with self.db.transaction() as connection:
                status = connection.execute('SELECT status FROM online_cutovers WHERE story_id=?', (story_id,)).fetchone()
                if status != ('committed',):
                    connection.execute('DELETE FROM lifecycle_locks WHERE story_id=? AND operation_id=?', (story_id, operation))
            raise
        finally:
            active_creation.reset(creation_token)
            writer.reset(token)

    def finish_cutover(self, manifest):
        for path in manifest['sources']:
            write_json_atomic(Path(path), {'schema_version':4, 'storage':'sqlite', 'minimum_reader':4,
                'online_story_cutover':manifest['story_id']}, fsync=True)
        with self.db.transaction() as connection:
            connection.execute("UPDATE online_cutovers SET status='done' WHERE story_id=?", (manifest['story_id'],))
            connection.execute('DELETE FROM lifecycle_locks WHERE story_id=? AND operation_id=?',
                (manifest['story_id'], 'cutover:' + manifest['story_id']))

    def recover_cutovers(self):
        with self.db.connect() as connection:
            pending = connection.execute("SELECT story_id,status,manifest FROM online_cutovers WHERE status IN ('prepared','committed')").fetchall()
        for story, status, serialized in pending:
            manifest = json.loads(serialized)
            if status == 'committed':
                self.finish_cutover(manifest)
            else:
                # Shadow rows remain frozen/inactive; old JSON still owns them.
                with self.db.transaction() as connection:
                    connection.execute("UPDATE online_cutovers SET status='aborted' WHERE story_id=?", (story,))
                    connection.execute('DELETE FROM lifecycle_locks WHERE story_id=? AND operation_id=?', (story, 'cutover:' + story))
        with self.db.transaction() as connection:
            for story, operation in connection.execute('SELECT story_id,operation_id FROM lifecycle_locks').fetchall():
                if operation == 'cutover:' + story and not connection.execute(
                    "SELECT 1 FROM online_cutovers WHERE story_id=? AND status IN ('prepared','committed')", (story,)).fetchone():
                    connection.execute('DELETE FROM lifecycle_locks WHERE story_id=? AND operation_id=?', (story, operation))

    def reserve(self, operation_id, action, scope, payload):
        fingerprint = digest({'action':action, 'scope':scope, 'payload':payload})
        with self.db.transaction() as connection:
            old = connection.execute('SELECT fingerprint,status,result FROM lifecycle_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise LifecycleConflict('操作ID已用于不同请求', 'operation_conflict')
                if old[1] == 'rejected':
                    connection.execute("UPDATE lifecycle_operations SET status='running',error=NULL WHERE operation_id=?",(operation_id,))
                    return None
                if old[1] == 'result_purged':
                    raise LifecycleConflict('操作结果已永久清除', 'operation_result_purged', 410)
                if old[1] in ('committed', 'cleanup'):
                    return json.loads(old[2])
                raise LifecycleConflict('生命周期操作已中断或仍在执行', 'operation_incomplete')
            connection.execute('INSERT INTO lifecycle_operations VALUES(?,?,?,?,?,?,?,?)',
                (operation_id, action, scope, fingerprint, 'running', None, None, None))
        return None

    def trash(self, story_id=None, generation_id=None):
        with self.db.connect() as connection:
            rows = connection.execute("SELECT generation_id,story_id,payload FROM trash_entries WHERE status='trashed'").fetchall()
        selected = [{**json.loads(raw), 'generation_id':generation} for generation,story,raw in rows
                    if (story_id is None or story == story_id) and (generation_id is None or generation == generation_id)]
        if story_id is not None:
            if not selected:
                if generation_id is not None and any(story == story_id for generation,story,raw in rows):
                    raise LifecycleConflict('删除代次已变化')
                raise LifecycleConflict('回收区中没有该故事或删除代次已变化', 'not_found', 404)
            if len(selected) != 1:
                raise LifecycleConflict('存在多个删除代次，请明确generation_id')
            return selected[0]
        return selected

    def commit(self, operation, action, story_id, *, branch_id=None, membership=None, entry=None, generation=None, preset_id=None, preset=None):
        token = writer.set(operation)
        try:
            with self.db.transaction() as connection:
                self.require_unlocked(story_id, connection=connection)
                connection.execute('INSERT INTO lifecycle_locks VALUES(?,?)', (story_id, operation))
                revisions = []
                targets = []
                if action in ('delete', 'branch_delete', 'preset'):
                    rows = connection.execute('SELECT id,revision FROM branches WHERE story_id=? AND active=1 ORDER BY id', (story_id,)).fetchall()
                    if not rows:
                        raise LifecycleConflict('故事不存在', 'not_found', 404)
                    current = self.membership(story_id)
                    if membership is not None and membership != current:
                        raise LifecycleConflict('故事成员或修订已变化')
                    targets = [identity for identity,_ in rows if branch_id is None or identity == branch_id]
                    if not targets:
                        raise LifecycleConflict('路线不存在', 'not_found', 404)
                    saved = [identity for identity,session in connection.execute('SELECT id,branch_id FROM story_saves WHERE active=1') if session in targets]
                    if action == 'branch_delete':
                        if saved:
                            raise LifecycleConflict('路线仍有存档', 'has_saves')
                        if any(row.parent_branch_id == branch_id for row in self.sessions.list_summaries_sync()):
                            raise LifecycleConflict('路线仍有子线', 'has_children')
                    for identity,revision in rows:
                        if identity not in targets:
                            continue
                        state = self.db.load(identity, db=connection)
                        state.meta.branch_revision = revision + 1
                        if action == 'preset':
                            state.meta.prompt_preset_id = preset_id
                            state.meta.prompt_preset_snapshot = preset
                        from mrp.orchestrator.worldline_state import record_persisted_head
                        record_persisted_head(state)
                        summary = summary_from_state(state, FileStamp(time.time(),time.time_ns(),0)).model_dump(mode='json')
                        self.db.write(state, summary, expected_revision=revision, db=connection)
                        revisions.append(state.meta.branch_revision)
                    if action != 'preset':
                        for identity in targets:
                            connection.execute('UPDATE branches SET active=-1 WHERE id=?', (identity,))
                            connection.execute('DELETE FROM outbox WHERE branch_id=? AND delivered=0', (identity,))
                        for identity in saved:
                            connection.execute('UPDATE story_saves SET active=-1 WHERE id=?', (identity,))
                        generation = operation
                        payload = {**entry, 'story_id':story_id, 'generation_id':generation, 'branch_ids':targets,
                                   'save_ids':saved, 'deleted_revisions':dict((identity,revision+1) for identity,revision in rows if identity in targets)}
                        connection.execute('INSERT INTO trash_entries VALUES(?,?,?,?)', (generation,story_id,'trashed',canonical(payload)))
                        result = {'ok':True,'story_id':story_id,'branch_ids':targets,'saves_deleted':len(saved),
                            'recoverable':True,'backup_sha256':entry['sha256'],'generation_id':generation,'branch_revision':max(revisions)}
                    else:
                        result = {'story_id':story_id,'preset_id':preset_id,'branches_updated':len(targets),
                                  'branch_revision':max(revisions),'membership_revision':self._membership_sql(connection,story_id)}
                else:
                    row = connection.execute("SELECT status,payload FROM trash_entries WHERE generation_id=? AND story_id=?", (generation,story_id)).fetchone()
                    if not row or row[0] != 'trashed':
                        raise LifecycleConflict('删除代次已变化')
                    trash = json.loads(row[1]); targets = trash['branch_ids']; saved = trash['save_ids']
                    for identity in targets:
                        expected = trash['deleted_revisions'][identity]
                        if connection.execute('SELECT revision,active FROM branches WHERE id=?', (identity,)).fetchone() != (expected,-1):
                            raise LifecycleConflict('删除身份或修订已变化')
                        revisions.append(expected+1 if action == 'restore' else expected)
                    if action == 'restore':
                        for identity in targets:
                            connection.execute('UPDATE branches SET active=1 WHERE id=?',(identity,))
                        for identity in targets:
                            state = self.db.load(identity, active_only=False, db=connection)
                            state.meta.branch_revision += 1
                            from mrp.orchestrator.worldline_state import record_persisted_head
                            record_persisted_head(state)
                            # Explicit identity restoration is the sole tombstone exception.
                            connection.execute('UPDATE branches SET active=1 WHERE id=?', (identity,))
                            self.db.write(state, summary_from_state(state,FileStamp(time.time(),time.time_ns(),0)).model_dump(mode='json'), expected_revision=state.meta.branch_revision-1, db=connection)
                        for identity in saved:
                            if not connection.execute('UPDATE story_saves SET active=1 WHERE id=? AND active=-1', (identity,)).rowcount:
                                raise LifecycleConflict('存档恢复身份已变化')
                        result = {'story_id':story_id,'source_story_id':story_id,'branches':[{'old_id':x,'new_id':x} for x in targets],
                                  'saves':[{'old_id':x,'new_id':x} for x in saved], 'branch_revision':max(revisions)}
                        connection.execute("UPDATE trash_entries SET status='restored' WHERE generation_id=?", (generation,))
                    elif action == 'purge':
                        from .memory_transactions import apply_memory_actions
                        actors = []
                        for identity in targets:
                            connection.execute('INSERT INTO branch_purges VALUES(?,?)',(identity,operation))
                            applied = apply_memory_actions(connection,identity,[{'kind':'purge_branch_rows','mode':'destroy'}])
                            actors.extend(applied['touched_actors'])
                            for table in ('components','message_lookup','commits','commit_memory_effects','outbox','memory_jobs','run_lookup','run_messages'):
                                connection.execute(f'DELETE FROM {table} WHERE branch_id=?', (identity,))
                            connection.execute("UPDATE command_receipts SET result=? WHERE branch_id=?", (canonical({'code':'operation_result_purged'}),identity))
                            connection.execute('UPDATE branches SET header_hash=?,summary=? WHERE id=?', (self.db._put(connection,{}),canonical({'id':identity,'story_id':story_id}),identity))
                        for identity in saved:
                            connection.execute('DELETE FROM story_saves WHERE id=? AND active=-1', (identity,))
                        owned = connection.execute('SELECT operation_id,kind,identity FROM creation_entities').fetchall()
                        purged = set(targets) | set(saved)
                        for creator in {op for op,kind,identity in owned if identity in purged}:
                            all_targets = {identity for op,kind,identity in owned if op == creator}
                            if all_targets <= purged:
                                connection.execute("UPDATE creation_operations SET status='result_purged',result=NULL WHERE operation_id=?", (creator,))
                        # Imported receipts carry explicit product targets, never source references.
                        for op,result in connection.execute("SELECT operation_id,result FROM creation_operations WHERE status='published'").fetchall():
                            product = self.creation_targets(json.loads(result))
                            if product and product <= purged:
                                connection.execute("UPDATE creation_operations SET status='result_purged',result=NULL WHERE operation_id=?", (op,))
                        for op,raw in connection.execute("SELECT operation_id,plan FROM lifecycle_operations WHERE scope=? AND status IN ('committed','cleanup')", (story_id,)).fetchall():
                            plan = json.loads(raw) if raw else {}
                            if set(plan.get('targets',[])) & purged:
                                connection.execute("UPDATE lifecycle_operations SET status='result_purged',plan=NULL,result=NULL WHERE operation_id=?", (op,))
                        connection.execute("UPDATE trash_entries SET status='purged',payload=? WHERE generation_id=?", (canonical({'story_id':story_id,'generation_id':generation}),generation))
                        # Reachability collection must recurse through encoded material references.
                        self.collect_materials(connection)
                        result = {'ok':True,'story_id':story_id,'generation_id':generation,'branch_revision':max(revisions)}
                        entry = {'actors':sorted(set(actors)), 'targets':targets, 'saves':saved,'trash_filename':trash.get('filename')}
                    else:
                        raise LifecycleConflict('未知生命周期命令')
                plan = {'targets':targets, 'generation_id':generation, 'cleanup':entry if action == 'purge' else None}
                connection.execute("UPDATE lifecycle_operations SET status=?,plan=?,result=? WHERE operation_id=? AND status='running'",
                    ('cleanup' if action == 'purge' else 'committed',canonical(plan),canonical(result),operation))
                connection.execute('DELETE FROM lifecycle_locks WHERE story_id=? AND operation_id=?', (story_id,operation))
                return result
        finally:
            writer.reset(token)

    @staticmethod
    def creation_targets(result):
        if not isinstance(result,dict): return set()
        if isinstance(result.get('branches'),list):
            targets = {row['new_id'] for row in result['branches'] if isinstance(row,dict) and isinstance(row.get('new_id'),str)}
            targets.update(row['new_id'] for row in result.get('saves',[]) if isinstance(row,dict) and isinstance(row.get('new_id'),str))
            return targets
        # API outputs put new products in these explicit fields.
        if isinstance(result.get('branch_id'),str): return {result['branch_id']}
        if isinstance(result.get('meta'),dict) and isinstance(result['meta'].get('id'),str): return {result['meta']['id']}
        if isinstance(result.get('save_id'),str): return {result['save_id']}
        if isinstance(result.get('id'),str) and isinstance(result.get('session_id'),str): return {result['id']}
        return set()

    @staticmethod
    def collect_materials(connection):
        roots = {row[0] for row in connection.execute('SELECT header_hash FROM branches')}
        roots.update(row[0] for row in connection.execute('SELECT material_hash FROM components'))
        # Saves may contain encoded references or literal JSON; inspect all to preserve sharing.
        for (raw,) in connection.execute('SELECT material_hash FROM story_saves'):
            def refs(value):
                if isinstance(value,dict):
                    if '__mrp_material_v1__' in value: roots.add(value['__mrp_material_v1__'])
                    for item in value.values(): refs(item)
                elif isinstance(value,list):
                    for item in value: refs(item)
            roots.add(raw)
        pending = list(roots)
        while pending:
            key = pending.pop()
            row = connection.execute('SELECT payload FROM materials WHERE hash=?',(key,)).fetchone()
            if row:
                def walk(value):
                    if isinstance(value,dict):
                        child=value.get('__mrp_material_v1__')
                        if isinstance(child,str) and child not in roots:
                            roots.add(child); pending.append(child)
                        for item in value.values(): walk(item)
                    elif isinstance(value,list):
                        for item in value: walk(item)
                walk(json.loads(row[0]))
        for (key,) in connection.execute('SELECT hash FROM materials').fetchall():
            if key not in roots: connection.execute('DELETE FROM materials WHERE hash=?',(key,))

    def _membership_sql(self, connection, story_id):
        branches = connection.execute('SELECT id,revision FROM branches WHERE story_id=? AND active=1 ORDER BY id',(story_id,)).fetchall()
        ids={x[0] for x in branches}
        saves=sorted((identity,branch) for identity,branch in connection.execute('SELECT id,branch_id FROM story_saves WHERE active=1') if branch in ids)
        return digest({'branches':branches,'saves':saves})

    def recover_operations(self):
        # A running operation never published body changes. Never redo external work.
        with self.db.transaction() as connection:
            connection.execute("UPDATE lifecycle_operations SET status='interrupted',error='operation_incomplete' WHERE status='running'")
            connection.execute("DELETE FROM lifecycle_locks WHERE operation_id IN (SELECT operation_id FROM lifecycle_operations WHERE status='interrupted')")

    def cleanup_purges(self, container):
        import shutil
        with self.db.connect() as connection:
            rows = connection.execute("SELECT operation_id,scope,plan FROM lifecycle_operations WHERE status='cleanup'").fetchall()
        for operation,story,raw in rows:
            plan=json.loads(raw); cleanup=plan['cleanup']
            # Only this deletion generation's explicitly owned archive is
            # removed. Independent snapshots and frozen migration sources stay.
            filename=cleanup.get('trash_filename')
            if filename:
                from .paths import is_safe_name
                if not is_safe_name(filename): raise ValueError('Unsafe trash artifact')
                artifact=container.data_root/'backups'/'stories'/story/filename
                artifact.unlink(missing_ok=True)
            path=container.data_root/'trash'/'stories'/(story+'.json')
            path.unlink(missing_ok=True)
            for identity in cleanup['targets']:
                source=self.sessions.path_for(identity)
                source.unlink(missing_ok=True)
                container.memory_store.refresh_branch_mirrors(identity)
                # Refresh removes current memory; purge stale actor mirrors too.
                for actor in cleanup['actors']:
                    container.memory_store.refresh_actor_mirror(actor)
                container.story_search.delete_branch(identity)
            for identity in cleanup['saves']:
                source=self.saves.path_for(identity); source.unlink(missing_ok=True)
            with self.db.transaction() as connection:
                connection.execute("UPDATE lifecycle_operations SET status='committed',plan=? WHERE operation_id=? AND status='cleanup'",(canonical({'targets':plan['targets'],'generation_id':plan['generation_id']}),operation))

    def reject_operation(self, operation_id, error):
        with self.db.transaction() as connection:
            connection.execute("UPDATE lifecycle_operations SET status='rejected',error=? WHERE operation_id=? AND status='running'",(str(error),operation_id))

    def known_trash_stories(self):
        with self.db.connect() as db: return {row[0] for row in db.execute('SELECT story_id FROM trash_entries')}

    def adopt_legacy_trash(self, story_id, entry, states, saves, records, histories, creation_receipts=()):
        """Convert a verified old ZIP restore point to retained original identities.

        Body, current memories, history, immutable saves and trash ownership enter
        the same transaction; no restored identity is visible before publication.
        """
        from .memory_transactions import apply_memory_actions
        from .creation_operations import restore_receipts
        operation='cutover:legacy-trash:'+story_id; token=writer.set(operation)
        try:
            with self.db.transaction() as db:
                if db.execute('SELECT 1 FROM trash_entries WHERE story_id=?',(story_id,)).fetchone(): return
                self.require_unlocked(story_id,connection=db)
                db.execute('INSERT INTO lifecycle_locks VALUES(?,?)',(story_id,operation))
                for identity in states:
                    if db.execute('SELECT 1 FROM branch_purges WHERE branch_id=?',(identity,)).fetchone():
                        raise LifecycleConflict('原故事已永久清除','operation_result_purged',410)
                    old=db.execute('SELECT revision,active FROM branches WHERE id=?',(identity,)).fetchone()
                    if old and old[1] == 1: raise LifecycleConflict('原路线身份已被活动故事占用')
                    if self.sessions.path_for(identity).exists() and (old is None or old[1]==0):
                        raw=json.loads(self.sessions.path_for(identity).read_text(encoding='utf-8'))
                        if raw.get('storage')!='sqlite': raise LifecycleConflict('原路线身份已有JSON故事')
                    if old: db.execute('UPDATE branches SET active=0 WHERE id=?',(identity,))
                    if old: states[identity].meta.branch_revision=max(states[identity].meta.branch_revision,old[0])
                revisions={}
                for identity,state in states.items():
                    fresh=[{'record':record.model_dump(mode='json'),'embedding':None} for record in records if record.session_id==identity]
                    actions=[{'kind':'replace_snapshot','records':fresh}]
                    if identity in histories: actions.append({'kind':'import_history','payload':histories[identity]})
                    else:
                        source_watermark=max([revision.memory_watermark or 0 for revision in state.state_revisions]+[record.commit_seq for record in records if record.session_id==identity]+[0])
                        verified={'floor':source_watermark,'clock':source_watermark,'versions':[{'record':record.model_dump(mode='json'),'valid_from':source_watermark,'valid_until':None} for record in records if record.session_id==identity]}
                        actions.append({'kind':'import_history','payload':verified})
                    applied=apply_memory_actions(db,identity,actions)
                    from mrp.orchestrator.worldline_state import record_persisted_head
                    record_persisted_head(state,applied['watermark'])
                    self.db.write(state,summary_from_state(state,FileStamp(time.time(),time.time_ns(),0)).model_dump(mode='json'),active=False,db=db)
                    revisions[identity]=state.meta.branch_revision
                for identity,save in saves.items():
                    old=db.execute('SELECT active FROM story_saves WHERE id=?',(identity,)).fetchone()
                    if old and old[0]==1: raise LifecycleConflict('原存档身份已被占用')
                    db.execute('DELETE FROM story_saves WHERE id=?',(identity,))
                    self.db.put_save(identity,save,summary_from_save(identity,save,FileStamp(time.time(),time.time_ns(),0)).model_dump(mode='json'),active=False,db=db)
                for identity in states: db.execute('UPDATE branches SET active=-1 WHERE id=?',(identity,))
                for identity in saves: db.execute('UPDATE story_saves SET active=-1 WHERE id=?',(identity,))
                restore_receipts(self.db,list(creation_receipts),db=db)
                generation='legacy-'+entry['sha256'][:32]
                payload={**entry,'story_id':story_id,'generation_id':generation,'branch_ids':list(states),'save_ids':list(saves),'deleted_revisions':revisions}
                db.execute('INSERT INTO trash_entries VALUES(?,?,?,?)',(generation,story_id,'trashed',canonical(payload)))
                db.execute('DELETE FROM lifecycle_locks WHERE story_id=? AND operation_id=?',(story_id,operation))
        finally: writer.reset(token)

    def report_delivery(self,operation_id,pending,error=None):
        with self.db.transaction() as db:
            row=db.execute('SELECT result FROM lifecycle_operations WHERE operation_id=?',(operation_id,)).fetchone()
            if not row or row[0] is None: raise LifecycleConflict('生命周期结果不存在')
            result=json.loads(row[0]); result['cleanup_pending']=pending
            db.execute('UPDATE lifecycle_operations SET result=?,error=? WHERE operation_id=?',(canonical(result),str(error) if error else None,operation_id))
            return result
