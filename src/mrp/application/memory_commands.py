"""Manual memory use cases; SQL and model preparation are injected ports."""
import asyncio
import hashlib
import json
import logging
from weakref import WeakValueDictionary

from mrp.application.branch_commit import BranchCommitConflict, stage_memory_actions
from mrp.shared.memory_visibility import visible_memory_messages
from mrp.application.ports import MemoryRepositoryPort, MemoryGenerationPort, BackgroundRuntimePort, BranchRunner
from mrp.application.branch_commit import BranchCommit
from mrp.shared.models import MemoryRecord, SessionState, Scene, utcnow, new_id
from mrp.shared.player_identity import message_person


class MemoryCommandError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def actor_exists(runner, actor):
    return actor in {c.id for c in runner.state.characters} | {g.id for g in runner.state.groups} | set(runner.state.player_people)


class MemoryCommands:
    def __init__(self, repository: MemoryRepositoryPort, generation: MemoryGenerationPort, background: BackgroundRuntimePort, commits: BranchCommit):
        self.repository, self.generation, self.background, self.commits = repository, generation, background, commits
        self._jobs = {}
        self._job_locks = WeakValueDictionary()

    async def _prepare(self, records):
        return await self.repository.prepare_records(records)

    async def remember(self, runner: BranchRunner, payload):
        if not payload['content'].strip():
            raise MemoryCommandError('记忆正文不能为空')
        known = {'player', *(c.id for c in runner.state.characters), *(g.id for g in runner.state.groups), *runner.state.player_people}
        if not set(payload.get('participant_ids', [])) <= known:
            raise MemoryCommandError('相关人物不属于当前故事')
        records = []
        for actor in dict.fromkeys(payload['character_ids']):
            if not actor_exists(runner, actor):
                raise MemoryCommandError('角色不属于当前故事分支', 404)
            visible = {m.id: m for m in visible_memory_messages(runner.state, actor)}
            if not set(payload['source_message_ids']) <= visible.keys():
                raise MemoryCommandError('所选角色不可见这些来源消息')
            sources = [visible[mid] for mid in dict.fromkeys(payload['source_message_ids'])]
            if any(payload.get('source_fingerprints', {}).get(m.id, m.fingerprint) != m.fingerprint for m in sources):
                raise BranchCommitConflict('来源已改变，请重新打开记忆编辑器', 'memory_source_conflict')
            participants = [actor for actor in payload.get('participant_ids', []) if actor != 'player']
            if 'player' in payload.get('participant_ids', []) or not payload.get('participant_ids'):
                participants.extend(message_person(runner.state, m) for m in sources if message_person(runner.state, m))
            records.append(MemoryRecord(character_id=actor, session_id=runner.state.meta.id, kind='manual',
                content=payload['content'].strip(), category=payload.get('category', 'experience'),
                important=payload.get('important', True), manually_revised=True,
                participant_ids=list(dict.fromkeys([actor, *participants])), source_message_ids=[m.id for m in sources],
                source_fingerprints={m.id:m.fingerprint for m in sources}, turn_start=min(m.turn for m in sources),
                turn_end=max(m.turn for m in sources), matter_status=payload.get('matter_status', 'unknown')))
        stage_memory_actions(runner, [{'kind':'insert_records', 'records':await self._prepare(records),
            'touched_actors':list(dict.fromkeys(record.character_id for record in records))}])
        result = {'records':[record.model_dump(mode='json') for record in records]}
        await runner._emit('memory.consolidated', {**result, 'reason':'manual-record'})
        return result

    async def revise(self, runner: BranchRunner, actor, record_id, payload):
        record = await self.repository.record_by_id(record_id)
        if record is None or record.session_id != runner.state.meta.id or record.character_id != actor:
            raise MemoryCommandError('记忆记录不存在', 404)
        expected = payload.get('expected_revision')
        if expected is not None and record.revision != expected:
            raise BranchCommitConflict('记忆已更新，请刷新后重试', 'memory_revision_conflict')
        if payload.get('content') is not None and not payload['content'].strip():
            raise MemoryCommandError('记忆正文不能为空')
        previous = record.model_dump(mode='json', include={'content','category','important','matter_status','revision'})
        previous['changed_at'] = utcnow().isoformat()
        record.revisions.append(previous)
        old_revision = record.revision
        for key, value in payload.items():
            if key not in {'expected_revision','expected_branch_revision'} and value is not None:
                setattr(record, key, value)
        record.importance = max(1, min(5, record.importance))
        record.revision += 1
        if any(key in payload for key in {'content','category','matter_status'}):
            record.manually_revised = True
            visible = {m.id:m for m in visible_memory_messages(runner.state, actor)}
            record.source_message_ids = [mid for mid in record.source_message_ids if mid in visible]
            record.source_fingerprints = {mid:visible[mid].fingerprint for mid in record.source_message_ids}
            record.source_changed = False
            record.invalidated = False
        stage_memory_actions(runner, [{'kind':'revise_record','record_id':record_id,'character_id':actor,
            'expected_revision':old_revision,'records':await self._prepare([record]),'touched_actors':[actor]}])
        result = record.model_dump(mode='json')
        await runner._emit('memory.updated', {'record':result})
        return result

    async def delete(self, runner: BranchRunner, actor, record_id, expected_revision=None):
        record = await self.repository.record_by_id(record_id)
        if record is None or record.session_id != runner.state.meta.id or record.character_id != actor:
            raise MemoryCommandError('记忆记录不存在', 404)
        stage_memory_actions(runner, [{'kind':'delete_record','record_id':record_id,'character_id':actor,
            'expected_revision':expected_revision,'touched_actors':[actor]}])
        await runner._emit('memory.deleted', {'record_id':record_id,'character_id':actor})
        return {'ok':True}

    async def restore_snapshot(self, runner: BranchRunner, save):
        if not save.memory_snapshot_complete:
            return 'unavailable'
        existing = await self.repository.branch_actor_ids(runner.state.meta.id)
        records = [record.model_copy(deep=True) for record in save.memory_snapshot]
        known = {c.id for c in runner.state.characters} | {g.id for g in runner.state.groups} | set(runner.state.player_people)
        if any(record.session_id != runner.state.meta.id or record.character_id not in known for record in records):
            raise MemoryCommandError('存档记忆归属无效')
        prepared = await self._prepare(records)
        stage_memory_actions(runner, [{'kind':'replace_snapshot','records':prepared,
            'touched_actors':list(set(existing) | {record.character_id for record in records})}])
        await runner._emit('memory.restored', {'status':'complete','record_count':len(records)})
        return 'complete'

    async def job_status(self, runner: BranchRunner, operation_id):
        job = await self.repository.lookup_memory_job(runner.state.meta.id, operation_id)
        if job is None:
            raise MemoryCommandError('整理任务不存在', 404)
        result = job.get('result')
        status = {'operation_id':operation_id,'status':job['status'],'fingerprint':job['fingerprint']}
        if job['status'] == 'committed' and result:
            status.update(result=result, branch_revision=result['branch_revision'])
        if job['status'] == 'failed' and result:
            status['error'] = result.get('message', result.get('code'))
        return status

    async def consolidate_window(self, runner: BranchRunner, actor, turn, *, min_interval, reason, turn_start=None, retry_failed=False):
        start, end, repair = await self.repository.plan_next_window(actor, runner.state.meta.id, turn, retry_failed=retry_failed)
        if turn_start is not None:
            start, end, repair = turn_start, turn, True
        if start > end or (not repair and end - start + 1 < min_interval):
            return []
        end = min(end, start + max(1, int(runner.state.meta.memory_interval_turns or 1)) - 1)
        hashes = {m.id:m.fingerprint for m in visible_memory_messages(runner.state, actor) if start <= m.turn <= end}
        identity = {'actor':actor,'start':start,'end':end,'fingerprints':hashes,
            'player_identity_id':runner.state.meta.player_identity_id}
        operation = 'memory-auto-' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        payload = {'character_id':actor,'async_mode':False,'turn_start':start,'turn_end':end,'reason':reason}
        try:
            await self.repository.ensure_transactional(runner.state.meta.id)
            result = await self.consolidate(runner, payload, operation_id=operation)
            return [MemoryRecord.model_validate(record) for record in result.get('records', [])]
        except Exception:
            logging.getLogger(__name__).warning('automatic memory window not committed; no repeated model attempt')
            return []

    async def summarize_scene(self, runner: BranchRunner, scene):
        for actor in list(scene.member_ids):
            hashes = {m.id:m.fingerprint for m in visible_memory_messages(runner.state, actor)
                if scene.turn_start <= m.turn <= scene.turn_end}
            identity = {'actor':actor,'scene':scene.model_dump(mode='json', exclude={'builtin_image_id'}),'fingerprints':hashes,
                'player_identity_id':runner.state.meta.player_identity_id}
            operation = 'memory-scene-' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            payload = {'character_id':actor,'async_mode':False,'turn_start':scene.turn_start,'turn_end':scene.turn_end,
                'reason':'scene','scene':scene.model_dump(mode='json', exclude={'builtin_image_id'})}
            try:
                await self.repository.ensure_transactional(runner.state.meta.id)
                await self.consolidate(runner, payload, operation_id=operation)
            except Exception:
                logging.getLogger(__name__).warning('scene summary not committed; no repeated model attempt')

    async def consolidate(self, runner: BranchRunner, payload, *, operation_id=None, response_meta=None):
        operation_id = operation_id or new_id('memory')
        branch = runner.state.meta.id
        fingerprint = hashlib.sha256(json.dumps({'action':'memory.consolidate','payload':payload},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        async with self._job_locks.setdefault((branch, operation_id), asyncio.Lock()):
            receipt = await self.repository.lookup_command(branch, operation_id)
            if receipt is not None:
                if receipt['fingerprint'] != fingerprint:
                    raise BranchCommitConflict('操作 ID 已用于不同请求', 'operation_conflict')
                if response_meta is not None:
                    response_meta.update(operation_id=operation_id, branch_revision=receipt['revision'])
                return receipt['result']
            job = await self.repository.lookup_memory_job(branch, operation_id)
            if job is not None:
                if job['fingerprint'] != fingerprint:
                    raise BranchCommitConflict('操作 ID 已用于不同请求', 'operation_conflict')
                task = self._jobs.get((branch, operation_id))
                if payload['async_mode'] and task is not None and not task.done():
                    return {'scheduled':True,'operation_id':operation_id,'status':'pending'}
                raise BranchCommitConflict('整理尝试已启动；结果未确认，不能自动重复模型调用', 'operation_incomplete')
            if not self.repository.consolidation_enabled():
                raise MemoryCommandError('记忆整理已关闭。到设置的高级选项打开后再整理。', 400)
            if (await self.repository.lookup_generation(branch, operation_id) is not None or
                operation_id in runner.state.generation_operations or
                any(run.operation_id == operation_id for run in runner.state.turn_runs + runner.state.conversation_runs) or
                operation_id in runner.state.generation_operations.get('__mrp_pending_commands_v1__', {})):
                raise BranchCommitConflict('操作 ID 已用于另一生成请求', 'operation_conflict')
            async with self.repository.branch_gate(branch), runner.runtime.turn_lock:
                snapshot = runner.state.model_copy(deep=True)
                snapshot.generation_operations = {}
                snapshot.state_revisions = []
                end = payload['turn_end'] if payload['turn_end'] is not None else snapshot.current_turn()
                start = payload['turn_start']
                if end > snapshot.current_turn() or (start is not None and start > end):
                    raise MemoryCommandError('整理轮次超出当前故事范围')
                actors = [payload['character_id']] if payload['character_id'] else [actor.id for actor in [*snapshot.characters,*snapshot.groups]]
                windows = []
                cap = max(1, int(snapshot.meta.memory_interval_turns or 1))
                for actor in actors:
                    if not actor_exists(runner, actor):
                        raise MemoryCommandError('角色不属于当前故事分支', 404)
                    visible = visible_memory_messages(snapshot, actor)
                    actor_start = start
                    if actor_start is None:
                        rows = await self.repository.window_rows(actor, branch)
                        repair = next((row for row in rows if row['status'] != 'complete' and row['end'] <= end), None)
                        actor_start = repair['start'] if repair else await self.repository.last_consolidated_turn(actor, branch) + 1
                    while actor_start <= end:
                        piece = min(end, actor_start + cap - 1)
                        if payload.get('scene'):
                            piece = end
                        windows.append({'character_id':actor,'start':actor_start,'end':piece,
                            'fingerprints':{m.id:m.fingerprint for m in visible if actor_start <= m.turn <= piece}})
                        actor_start = piece + 1
                plan = {'snapshot':snapshot.model_dump(mode='json'),'windows':windows,'payload':payload}
                job = await self.repository.claim_memory_job(branch, operation_id, fingerprint, plan)
                if not job['new']:
                    raise BranchCommitConflict('整理尝试已启动，不能自动重复模型调用', 'operation_incomplete')
            if payload['async_mode']:
                task = self.background.spawn(runner, self._run_job(runner, operation_id, fingerprint, plan))
                if task is None:
                    raise BranchCommitConflict('整理任务未启动，不能自动重复模型调用', 'operation_incomplete')
                self._jobs[(branch, operation_id)] = task
                key = (branch, operation_id)
                def release_job(done):
                    if self._jobs.get(key) is done:
                        self._jobs.pop(key, None)
                    if not done.cancelled():
                        done.exception()
                task.add_done_callback(release_job)
                return {'scheduled':True,'operation_id':operation_id,'status':'pending'}
            return await self._run_job(runner, operation_id, fingerprint, plan, response_meta=response_meta)

    async def _run_job(self, runner: BranchRunner, operation_id, fingerprint, plan, response_meta=None):
        with self.background.branch_lease(runner.state.meta.id):
            return await self._execute_job(runner, operation_id, fingerprint, plan, response_meta)

    async def _execute_job(self, runner: BranchRunner, operation_id, fingerprint, plan, response_meta=None):
        branch = runner.state.meta.id
        try:
            async with runner.runtime.consolidation_lock:
                snapshot = SessionState.model_validate(plan['snapshot'])
                generated = []
                for window in plan['windows']:
                    if plan['payload'].get('scene'):
                        record = await self.generation.generate_scene(runner, snapshot, window['character_id'],
                            Scene.model_validate(plan['payload']['scene']))
                        records = [record] if record else []
                    else:
                        records = await self.generation.generate_window(runner, snapshot, window['character_id'],
                            window['start'], window['end'])
                    generated.append((window, records))
                async def commit():
                    if runner.state.meta.player_identity_id != snapshot.meta.player_identity_id:
                        raise BranchCommitConflict('整理期间玩家身份已改变', 'memory_source_conflict')
                    watermark = await self.repository.current_watermark(branch)
                    actions, all_records = [], []
                    for window, records in generated:
                        actor = window['character_id']
                        current = {m.id:m.fingerprint for m in visible_memory_messages(runner.state, actor)
                            if window['start'] <= m.turn <= window['end']}
                        if current != window['fingerprints']:
                            raise BranchCommitConflict('整理期间来源已改变', 'memory_source_conflict')
                        old = await self.repository.records_for(actor, branch)
                        accepted = []
                        for record in records:
                            if (record.session_id != branch or record.character_id != actor or
                                not set(record.source_message_ids) <= current.keys() or
                                any(record.source_fingerprints.get(mid, current[mid]) != current[mid] for mid in record.source_message_ids)):
                                raise MemoryCommandError('整理结果的来源或角色无效')
                            if any(item.manually_revised and set(item.source_message_ids) == set(record.source_message_ids) and item.category == record.category for item in old):
                                continue
                            if any(not item.invalidated and item.content == record.content and item.category == record.category and item.source_fingerprints == record.source_fingerprints for item in [*old,*accepted]):
                                continue
                            accepted.append(record)
                        actions.append({'kind':'insert_records' if plan['payload'].get('scene') else 'complete_window',**window,'records':await self._prepare(accepted),
                            'touched_actors':[actor]})
                        all_records.extend(record.model_dump(mode='json') for record in accepted)
                    if actions:
                        actions[0]['expected_memory_watermark'] = watermark
                    result = {'records':all_records,'operation_id':operation_id,'status':'committed',
                        'branch_revision':runner.state.meta.branch_revision + 1}
                    actions.append({'kind':'finish_memory_job','operation_id':operation_id,'fingerprint':fingerprint,'result':result})
                    stage_memory_actions(runner, actions)
                    await runner._emit('memory.consolidated', {**result,'reason':plan['payload'].get('reason','manual')})
                    return result
                return await self.commits.execute(runner, 'memory.consolidate', plan['payload'], commit,
                    operation_id=operation_id, response_meta=response_meta)
        except BaseException as exc:
            try:
                async with self.commits.edit(runner):
                    actions = [{'kind':'fail_memory_job','operation_id':operation_id,
                        'fingerprint':fingerprint,'result':{'code':'operation_incomplete','message':'整理结果未提交；需要显式新意图'}}]
                    if not plan['payload'].get('scene'):
                        actions.extend({'kind':'fail_window',**window,'status':'dirty' if getattr(exc,'code','') == 'memory_source_conflict' else 'failed',
                            'error':'整理结果未提交；需要显式新意图'} for window in plan['windows'])
                    stage_memory_actions(runner, actions)
            except BaseException:
                logging.getLogger(__name__).warning('memory task terminal status deferred to restart recovery')
            if plan['payload']['async_mode'] and isinstance(exc, Exception):
                logging.getLogger(__name__).warning('memory task failed without automatic model retry')
                return None
            raise
