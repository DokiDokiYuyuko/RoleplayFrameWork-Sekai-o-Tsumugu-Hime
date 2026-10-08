"""Synthetic atomic memory commands, historical restore and durable model tasks."""
import asyncio
import threading

import pytest

from mrp.shared.models import MemoryRecord, TokenUsage
from mrp.tests.conftest import make_test_container
from mrp.tests.test_command_replay import client_for
from mrp.tests.test_ordinary_turn_runs import rig, req


async def memory_rig(tmp_path):
    c, runner, *_ = await rig(tmp_path)
    runner.memory = c.memory_store
    await c.turn_runs.send(runner, req(mentions=['a']))
    c.settings.memory_consolidation_enabled = True
    return c, runner


def remember_body(runner, actors=None):
    source = runner.state.messages[0]
    return {'character_ids':actors or ['a'],'source_message_ids':[source.id],
        'source_fingerprints':{source.id:source.fingerprint},'content':'synthetic memory'}


@pytest.mark.asyncio
async def test_multi_actor_failure_rolls_back_story_memory_receipt_and_events(tmp_path, monkeypatch):
    from mrp.storage import memory_transactions
    c, r = await memory_rig(tmp_path)
    before = r.state.meta.branch_revision
    insert = memory_transactions._insert
    def fail_second(db, branch, prepared, tables):
        if prepared['record']['character_id'] == 'b':
            raise OSError('synthetic second actor failure')
        return insert(db, branch, prepared, tables)
    monkeypatch.setattr(memory_transactions, '_insert', fail_second)
    try:
        async with client_for(c) as client:
            url = f'/api/v1/sessions/{r.state.meta.id}/memory/records'
            response = await client.post(url, json=remember_body(r, ['a','b']), headers={'X-Operation-ID':'synthetic-multi-memory'})
            assert response.status_code >= 500
            assert c.memory_store.records_for('a', session_id=r.state.meta.id) == []
            assert c.memory_store.records_for('b', session_id=r.state.meta.id) == []
            assert r.state.meta.branch_revision == before
            assert await c.sessions.lookup_command(r.state.meta.id, 'synthetic-multi-memory') is None
            monkeypatch.setattr(memory_transactions, '_insert', insert)
            successful = await client.post(url, json=remember_body(r, ['a','b']), headers={'X-Operation-ID':'synthetic-multi-memory'})
            assert successful.status_code == 200, successful.text
            assert len(successful.json()['records']) == 2
            assert all(record['commit_seq'] > 0 for record in successful.json()['records'])
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_memory_receipts_restart_and_no_session_target_deleted(tmp_path):
    c, r = await memory_rig(tmp_path)
    root = f'/api/v1/sessions/{r.state.meta.id}'
    async with client_for(c) as client:
        created = await client.post(root + '/memory/records', json=remember_body(r), headers={'X-Operation-ID':'synthetic-create-memory'})
        assert created.status_code == 200, created.text
        record = created.json()['records'][0]
        target = f'/api/v1/characters/a/memories/{record["id"]}'
        body = {'content':'synthetic revised','expected_revision':record['revision']}
        edited = await client.patch(target, json=body, headers={'X-Operation-ID':'synthetic-revise-memory'})
        assert edited.status_code == 200, edited.text
        from mrp.tests.test_phase2_acceptance import client_for as boundary_client
        async with boundary_client(c, lose_response=True) as disconnected:
            deleted = await disconnected.delete(target, headers={'X-Operation-ID':'synthetic-delete-memory'})
            assert deleted.status_code >= 500
        deleted_receipt = await c.sessions.lookup_command(r.state.meta.id, 'synthetic-delete-memory')
        assert deleted_receipt is not None
    sid, revision = r.state.meta.id, r.state.meta.branch_revision
    await c.aclose()
    c = make_test_container(tmp_path)
    try:
        async with client_for(c) as client:
            edit_replay = await client.patch(target, json=body, headers={'X-Operation-ID':'synthetic-revise-memory'})
            assert edit_replay.status_code == 200, edit_replay.text
            assert edit_replay.json() == edited.json()
            assert edit_replay.headers['X-Branch-Revision'] == edited.headers['X-Branch-Revision']
            delete_replay = await client.delete(target, headers={'X-Operation-ID':'synthetic-delete-memory'})
            assert delete_replay.status_code == 200, delete_replay.text
            assert delete_replay.json() == deleted_receipt['result']
        assert (await c.sessions.load_state(sid)).meta.branch_revision == revision
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_complete_save_restore_keeps_history_and_new_record_revision(tmp_path):
    from mrp.tests.test_phase2_acceptance import client_for
    c, r = await memory_rig(tmp_path)
    root = f'/api/v1/sessions/{r.state.meta.id}'
    try:
        async with client_for(c) as client:
            created = await client.post(root + '/memory/records', json=remember_body(r))
            record = created.json()['records'][0]
            old_clock = c.memory_store.current_watermark(r.state.meta.id)
            save = await client.post(root + '/save')
            assert save.status_code == 200, save.text
            target = f'/api/v1/characters/a/memories/{record["id"]}?session_id={r.state.meta.id}'
            changed = await client.patch(target, json={'content':'later synthetic content','expected_revision':1})
            assert changed.status_code == 200, changed.text
            await client.delete(target)
            restored = await client.post('/api/v1/saves/' + save.json()['id'] + '/restore', headers={'X-Operation-ID':'synthetic-restore-memory'})
            assert restored.status_code == 200, restored.text
            assert restored.json()['memory_restore_status'] == 'complete'
            current = c.memory_store.record_by_id(record['id'])
            assert current.content == record['content']
            assert current.revision > changed.json()['revision']
            assert c.memory_store.current_watermark(r.state.meta.id) > old_clock
            assert c.memory_store.history_at(r.state.meta.id, old_clock)[0].content == record['content']
            stale = await client.patch(target, json={'content':'stale CAS','expected_revision':1})
            assert stale.status_code == 409
    finally:
        await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('complete', [False, True])
async def test_empty_and_missing_memory_snapshot_are_different(tmp_path, complete):
    c, r = await memory_rig(tmp_path)
    _, save = await c.saves.create(r.state, 'synthetic save', [] if complete else None)
    try:
        async with client_for(c) as client:
            await client.post(f'/api/v1/sessions/{r.state.meta.id}/memory/records', json=remember_body(r))
            response = await client.post(f'/api/v1/saves/{save.id}/restore', headers={'X-Operation-ID':'synthetic-empty-restore'})
            assert response.status_code == 200, response.text
            assert response.json()['memory_restore_status'] == ('complete' if complete else 'unavailable')
            assert bool(c.memory_store.records_for('a', session_id=r.state.meta.id)) is not complete
    finally:
        await c.aclose()


class SyntheticConsolidator:
    usage = TokenUsage()
    def __init__(self, *, entered=None, release=None):
        self.calls = 0
        self.entered, self.release = entered, release
    def consolidate_records(self, state, actor, after, end, **kwargs):
        self.calls += 1
        if self.entered:
            self.entered.set()
            assert self.release.wait(5)
        source = next(message for message in state.messages if message.actor == 'player')
        return [MemoryRecord(character_id=actor, session_id=state.meta.id, kind='episodic', content='synthetic extraction',
            turn_start=after+1, turn_end=end, source_message_ids=[source.id], source_fingerprints={source.id:source.fingerprint})]


@pytest.mark.asyncio
async def test_consolidation_commit_failure_and_restart_never_repeats_model(tmp_path, monkeypatch):
    c, r = await memory_rig(tmp_path)
    model = SyntheticConsolidator()
    r.episodic_consolidator = model
    commit = c.sessions.commit_state
    async def fail_receipt(*args, **kwargs):
        if kwargs.get('command_receipt'):
            raise OSError('synthetic receipt/story failure after extraction')
        return await commit(*args, **kwargs)
    monkeypatch.setattr(c.sessions, 'commit_state', fail_receipt)
    url = f'/api/v1/sessions/{r.state.meta.id}/memory/consolidate?character_id=a&turn_start=1&turn_end=1'
    headers = {'X-Operation-ID':'synthetic-memory-model'}
    async with client_for(c) as client:
        first = await client.post(url, headers=headers)
        assert first.status_code >= 500, first.text
    assert model.calls == 1
    assert c.memory_store.records_for('a', session_id=r.state.meta.id) == []
    sid = r.state.meta.id
    await c.aclose()
    c = make_test_container(tmp_path)
    try:
        r = await c.load_session(sid)
        r.episodic_consolidator = model
        async with client_for(c) as client:
            repeat = await client.post(url, headers=headers)
            assert repeat.status_code == 409, repeat.text
            assert repeat.json()['detail']['code'] == 'operation_incomplete'
            status = await client.get(f'/api/v1/sessions/{sid}/memory/jobs/synthetic-memory-model')
            assert status.status_code == 200 and status.json()['status'] == 'failed'
        assert model.calls == 1
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_consolidation_source_change_rejects_late_result(tmp_path):
    c, r = await memory_rig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    model = SyntheticConsolidator(entered=entered, release=release)
    r.episodic_consolidator = model
    root = f'/api/v1/sessions/{r.state.meta.id}'
    try:
        async with client_for(c) as client:
            task = asyncio.create_task(client.post(root + '/memory/consolidate?character_id=a&turn_start=1&turn_end=1',
                headers={'X-Operation-ID':'synthetic-stale-extraction'}))
            assert await asyncio.to_thread(entered.wait, 3)
            changed = await client.patch(root + '/messages/' + r.state.messages[0].id, json={'content':'synthetic changed evidence'})
            assert changed.status_code == 200, changed.text
            release.set()
            response = await task
            assert response.status_code == 409, response.text
            assert response.json()['detail']['code'] == 'memory_source_conflict'
        assert c.memory_store.records_for('a', session_id=r.state.meta.id) == []
        assert model.calls == 1
    finally:
        release.set()
        await c.aclose()


@pytest.mark.asyncio
async def test_notification_failure_keeps_memory_success(tmp_path, monkeypatch):
    c, r = await memory_rig(tmp_path)
    async def failed_notice(**kwargs):
        raise OSError('synthetic notification failure')
    monkeypatch.setattr(c, 'dispatch_story_outbox', failed_notice)
    try:
        async with client_for(c) as client:
            response = await client.post(f'/api/v1/sessions/{r.state.meta.id}/memory/records',
                json=remember_body(r), headers={'X-Operation-ID':'synthetic-memory-notice'})
            assert response.status_code == 200, response.text
            assert c.memory_store.record_by_id(response.json()['records'][0]['id']) is not None
            assert await c.sessions.lookup_command(r.state.meta.id, 'synthetic-memory-notice')
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_async_memory_job_runs_once_and_terminal_status_is_durable(tmp_path):
    c, r = await memory_rig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    model = SyntheticConsolidator(entered=entered, release=release)
    r.episodic_consolidator = model
    url = f'/api/v1/sessions/{r.state.meta.id}/memory/consolidate?character_id=a&async_mode=true&turn_start=1&turn_end=1'
    status_url = f'/api/v1/sessions/{r.state.meta.id}/memory/jobs/synthetic-async-memory'
    headers = {'X-Operation-ID':'synthetic-async-memory'}
    try:
        async with client_for(c) as client:
            first = await client.post(url, headers=headers)
            assert first.status_code == 200 and first.json()['status'] == 'pending', first.text
            assert await asyncio.to_thread(entered.wait, 3)
            assert c.branch_runtimes.leased(r.state.meta.id)
            pending = await client.get(status_url)
            assert pending.json()['status'] == 'pending'
            repeat = await client.post(url, headers=headers)
            assert repeat.status_code == 200 and repeat.json()['status'] == 'pending'
            task = c.memory_commands._jobs[(r.state.meta.id, 'synthetic-async-memory')]
            release.set()
            await asyncio.wait_for(task, 5)
            terminal = await client.get(status_url)
            assert terminal.status_code == 200 and terminal.json()['status'] == 'committed', terminal.text
            assert terminal.json()['result']['records'][0]['commit_seq'] > 0
            completed = await client.post(url, headers=headers)
            assert completed.json() == terminal.json()['result']
        assert model.calls == 1
    finally:
        release.set()
        await c.aclose()


@pytest.mark.asyncio
async def test_pending_memory_job_restart_becomes_failed_without_model(tmp_path):
    c, r = await memory_rig(tmp_path)
    sid = r.state.meta.id
    await c.sessions.claim_memory_job(sid, 'synthetic-interrupted-memory', 'synthetic-fingerprint', {'synthetic':True})
    await c.aclose()
    c = make_test_container(tmp_path)
    try:
        async with client_for(c) as client:
            status = await client.get(f'/api/v1/sessions/{sid}/memory/jobs/synthetic-interrupted-memory')
            assert status.status_code == 200, status.text
            assert status.json()['status'] == 'failed'
            assert status.json()['error']
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_prepared_embedding_runs_outside_sql_transaction(tmp_path):
    from mrp.orchestrator.memory import HashEmbedding
    c, r = await memory_rig(tmp_path)
    embedding = HashEmbedding(c.memory_store.vec_dim)
    calls = []
    class CheckedEmbedding:
        def embed(self, texts):
            assert not c.memory_store.conn.in_transaction
            calls.append(texts)
            return embedding.embed(texts)
    c.memory_store.embedding = CheckedEmbedding()
    if not c.memory_store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='memory_vec_branch'").fetchone():
        await c.aclose()
        pytest.skip('sqlite vector extension unavailable')
    c.memory_store.vector_enabled = True
    c.memory_store.branch_vector_enabled = True
    try:
        async with client_for(c) as client:
            created = await client.post(f'/api/v1/sessions/{r.state.meta.id}/memory/records', json=remember_body(r))
            assert created.status_code == 200, created.text
            record = created.json()['records'][0]
            assert len(calls) == 1
            with c.memory_store._lock:
                assert c.memory_store.conn.execute('SELECT COUNT(*) FROM memory_vec WHERE record_id=?', (record['id'],)).fetchone()[0] == 1
            deleted = await client.delete(f'/api/v1/characters/a/memories/{record["id"]}?session_id={r.state.meta.id}')
            assert deleted.status_code == 200, deleted.text
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_hidden_branch_is_excluded_from_global_memory_reads_but_history_remains(tmp_path):
    c, r = await memory_rig(tmp_path)
    record = MemoryRecord(character_id='a', session_id=r.state.meta.id, content='synthetic hidden memory')
    c.memory_store.add(record)
    watermark = c.memory_store.current_watermark(r.state.meta.id)
    try:
        with c.sessions.story_db.transaction() as db:
            db.execute('UPDATE branches SET active=-1 WHERE id=?', (r.state.meta.id,))
        assert c.memory_store.records_for('a') == []
        assert c.memory_store.search('a', 'synthetic') == []
        assert c.memory_store.history_at(r.state.meta.id, watermark)[0].id == record.id
        with c.sessions.story_db.transaction() as db:
            db.execute('UPDATE branches SET active=1 WHERE id=?', (r.state.meta.id,))
        assert c.memory_store.records_for('a')[0].id == record.id
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_automatic_window_and_scene_summary_share_durable_commit_port(tmp_path):
    c, r = await memory_rig(tmp_path)
    model = SyntheticConsolidator()
    r.episodic_consolidator = model
    class SceneModel:
        usage = TokenUsage()
        calls = 0
        def summarize_scene(self, state, actor, scene):
            self.calls += 1
            source = state.messages[0]
            return MemoryRecord(character_id=actor, session_id=state.meta.id, kind='scene',
                content='synthetic scene summary', scene_id=scene.id, turn_start=1, turn_end=1,
                source_message_ids=[source.id], source_fingerprints={source.id:source.fingerprint})
    summary = SceneModel()
    r.scene_summarizer = summary
    try:
        first = await r.memory_pipeline._consolidate_character('a', 1, min_interval=0, retries=2, reason='auto')
        assert len(first) == 1 and first[0].commit_seq > 0
        assert await r.memory_pipeline._consolidate_character('a', 1, min_interval=0, retries=2, reason='auto') == []
        assert model.calls == 1
        scene = r.state.scenes[0].model_copy(deep=True)
        scene.member_ids, scene.turn_start, scene.turn_end = ['a'], 1, 1
        await r.memory_pipeline._scene_summary_worker(scene)
        await r.memory_pipeline._scene_summary_worker(scene)
        assert summary.calls == 1
        records = c.memory_store.records_for('a', session_id=r.state.meta.id)
        assert {record.kind for record in records} == {'episodic','scene'}
        with c.sessions.story_db.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM memory_jobs WHERE branch_id=? AND status='committed'", (r.state.meta.id,)).fetchone()[0] == 2
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_legacy_story_memory_write_normalizes_before_atomic_command(tmp_path):
    c, r = await memory_rig(tmp_path)
    state = r.state.model_copy(deep=True)
    await c.aclose()
    c = make_test_container(tmp_path / 'legacy')
    c.sessions.sqlite_new_stories = False
    await c.sessions.save_state(state)
    assert not c.sessions.story_db.active(state.meta.id)
    try:
        async with client_for(c) as client:
            result = await client.post(f'/api/v1/sessions/{state.meta.id}/memory/records',
                json=remember_body(type('Runner', (), {'state':state})()), headers={'X-Operation-ID':'synthetic-legacy-memory-cutover'})
            assert result.status_code == 200, result.text
            assert c.sessions.story_db.active(state.meta.id)
            assert c.sessions.path_for(state.meta.id).exists()  # frozen source retained
            assert c.memory_store.record_by_id(result.json()['records'][0]['id']) is not None
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_window_replacement_preserves_old_history_and_records_new_invalidation(tmp_path):
    c, r = await memory_rig(tmp_path)
    old = MemoryRecord(character_id='a', session_id=r.state.meta.id, kind='episodic', content='old synthetic extraction',
        turn_start=1, turn_end=1, source_message_ids=[r.state.messages[0].id],
        source_fingerprints={r.state.messages[0].id:r.state.messages[0].fingerprint})
    c.memory_store.add(old)
    before = c.memory_store.current_watermark(r.state.meta.id)
    r.episodic_consolidator = SyntheticConsolidator()
    try:
        async with client_for(c) as client:
            response = await client.post(f'/api/v1/sessions/{r.state.meta.id}/memory/consolidate?character_id=a&turn_start=1&turn_end=1',
                headers={'X-Operation-ID':'synthetic-replace-window'})
            assert response.status_code == 200, response.text
        after = c.memory_store.current_watermark(r.state.meta.id)
        assert c.memory_store.history_at(r.state.meta.id, before)[0].id == old.id
        latest = c.memory_store.history_at(r.state.meta.id, after)
        assert all(record.id != old.id for record in latest)
        invalidated = c.memory_store.record_by_id(old.id)
        assert invalidated.invalidated and invalidated.revision > old.revision
        async with client_for(c) as client:
            stale = await client.patch(f'/api/v1/characters/a/memories/{old.id}?session_id={r.state.meta.id}',
                json={'important':True,'expected_revision':old.revision}, headers={'X-Operation-ID':'synthetic-stale-auto-edit'})
            assert stale.status_code == 409, stale.text
        with c.memory_store._lock:
            historical = c.memory_store.conn.execute('SELECT invalidated,valid_from,commit_seq FROM memory_history WHERE session_id=? AND id=? ORDER BY valid_from',
                (r.state.meta.id, old.id)).fetchall()
        assert historical[-1][0] == 1
        assert historical[-1][1] == historical[-1][2] > before
        with c.sessions.story_db.connect() as db:
            events = [__import__('json').loads(row[0]) for row in db.execute('SELECT event FROM outbox WHERE branch_id=? AND revision=?',
                (r.state.meta.id, r.state.meta.branch_revision))]
        final_event = next(event for event in events if event['event'] == 'memory.consolidated')
        assert final_event['data']['records'] == response.json()['records']
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_receipt_replay_does_not_wait_for_blocked_story_mutation_and_checks_visibility(tmp_path):
    from mrp.application.branch_commit import BranchCommitConflict
    c, r = await memory_rig(tmp_path)
    root = f'/api/v1/sessions/{r.state.meta.id}/memory/records'
    body = remember_body(r)
    headers = {'X-Operation-ID':'synthetic-fast-memory-replay'}
    entered, release = asyncio.Event(), asyncio.Event()
    async def blocked_model():
        async with c.story_lifecycle.branch_gate(r.state.meta.id), r.runtime.turn_lock:
            entered.set()
            await release.wait()
    try:
        async with client_for(c) as client:
            first = await client.post(root, json=body, headers=headers)
            assert first.status_code == 200, first.text
            blocker = asyncio.create_task(blocked_model())
            await asyncio.wait_for(entered.wait(), 2)
            # The router's normalization can skip an already-SQL branch; replay
            # must never queue behind the running model's story gate.
            replay = await asyncio.wait_for(client.post(root, json=body, headers=headers), 1)
            assert replay.status_code == 200 and replay.json() == first.json(), replay.text
            with c.sessions.story_db.transaction() as db:
                db.execute('UPDATE branches SET active=-1 WHERE id=?', (r.state.meta.id,))
            with pytest.raises(BranchCommitConflict) as error:
                await asyncio.wait_for(c.branch_commit.execute(r, 'memory.remember', body, lambda: None,
                    operation_id=headers['X-Operation-ID']), 1)
            assert error.value.status_code == 404
            release.set()
            await blocker
    finally:
        release.set()
        await c.aclose()


@pytest.mark.asyncio
async def test_completed_async_task_registry_releases_tasks_and_locks(tmp_path):
    c, r = await memory_rig(tmp_path)
    r.episodic_consolidator = SyntheticConsolidator()
    url = f'/api/v1/sessions/{r.state.meta.id}/memory/consolidate?character_id=a&async_mode=true&turn_start=1&turn_end=1'
    try:
        async with client_for(c) as client:
            for index in range(4):
                operation = f'synthetic-memory-batch-{index}'
                response = await client.post(url, headers={'X-Operation-ID':operation})
                assert response.status_code == 200, response.text
                task = c.memory_commands._jobs.get((r.state.meta.id,operation))
                if task is not None:
                    await asyncio.wait_for(task, 5)
                await asyncio.sleep(0)
                assert not c.memory_commands._jobs
                assert not c.memory_commands._job_locks
        assert r.episodic_consolidator.calls == 4
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_archived_history_import_is_atomic_and_keeps_new_restore_head(tmp_path):
    from mrp.application.branch_commit import stage_memory_actions
    c, r = await memory_rig(tmp_path)
    record = MemoryRecord(character_id='a', session_id=r.state.meta.id, content='synthetic archived memory')
    c.memory_store.add(record)
    history = c.memory_store.export_history(r.state.meta.id)
    c.memory_store.purge_branch(r.state.meta.id)
    prepared = c.memory_store.prepare_records([record])
    try:
        async with c.branch_commit.edit(r):
            stage_memory_actions(r, [{'kind':'replace_snapshot','records':prepared},
                {'kind':'import_history','payload':history}])
        current = c.memory_store.record_by_id(record.id)
        assert current.revision > record.revision
        assert current.commit_seq > history['clock']
        assert c.memory_store.history_at(r.state.meta.id, history['clock'])[0].revision == record.revision
        assert c.memory_store.history_at(r.state.meta.id, current.commit_seq)[0].revision == current.revision
    finally:
        await c.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['manual','episodic'])
async def test_source_invalidation_advances_record_cas_and_live_history_sequence(tmp_path, kind):
    from mrp.application.branch_commit import stage_memory_actions
    c, r = await memory_rig(tmp_path)
    source = r.state.messages[0]
    record = MemoryRecord(character_id='a', session_id=r.state.meta.id, kind=kind,
        content='synthetic source-bound memory', source_message_ids=[source.id],
        source_fingerprints={source.id:source.fingerprint}, turn_start=1, turn_end=1)
    c.memory_store.add(record)
    before = c.memory_store.current_watermark(r.state.meta.id)
    try:
        async with c.branch_commit.edit(r):
            stage_memory_actions(r, [{'kind':'invalidate_sources','message_ids':[source.id]}])
        actual = c.memory_store.record_by_id(record.id)
        assert actual.source_changed and actual.revision == record.revision + 1
        assert actual.invalidated == (kind != 'manual')
        assert actual.commit_seq > before
        assert c.memory_store.history_at(r.state.meta.id, before)[0].revision == record.revision
        with c.memory_store._lock:
            row = c.memory_store.conn.execute('SELECT valid_from,commit_seq FROM memory_history WHERE id=? AND valid_until IS NULL', (record.id,)).fetchone()
        assert row == (actual.commit_seq, actual.commit_seq)
        async with client_for(c) as client:
            stale = await client.patch(f'/api/v1/characters/a/memories/{record.id}?session_id={r.state.meta.id}',
                json={'important':True,'expected_revision':record.revision}, headers={'X-Operation-ID':'synthetic-source-stale'})
            assert stale.status_code == 409, stale.text
    finally:
        await c.aclose()


@pytest.mark.asyncio
async def test_inferred_memory_owner_never_falls_back_for_hidden_story(tmp_path):
    c, r = await memory_rig(tmp_path)
    record = MemoryRecord(character_id='a', session_id=r.state.meta.id, content='synthetic hidden memory')
    c.memory_store.add(record)
    try:
        with c.sessions.story_db.transaction() as db:
            db.execute('UPDATE branches SET active=-1 WHERE id=?', (r.state.meta.id,))
        async with client_for(c) as client:
            target = f'/api/v1/characters/a/memories/{record.id}'
            patch = await client.patch(target, json={'content':'must not change'})
            delete = await client.delete(target)
            assert patch.status_code == delete.status_code == 404
        assert c.memory_store.record_by_id(record.id).content == record.content
    finally:
        await c.aclose()
