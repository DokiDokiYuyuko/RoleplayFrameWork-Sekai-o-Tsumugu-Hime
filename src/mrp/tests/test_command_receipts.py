import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from mrp.shared.models import SessionMeta, SessionState, Message, MemoryRecord
from mrp.orchestrator.memory import MemoryStore
from mrp.storage.command_receipts import (RECEIPT_KEY, AUDIT_KEY, CommandConflict,
                                         CommandAlreadyCommitted, portable_receipts)
from mrp.storage.paths import AppPaths
from mrp.storage.session_repo import SessionRepo
from mrp.storage.save_repo import SaveRepo
from mrp.storage.story_sqlite import StorySqlite, _Connection


def value():
    return SessionState(schema_version=3, meta=SessionMeta(id='sess-receipt', story_id='sess-receipt', title='synthetic'),
        messages=[Message(id='msg-receipt', session_id='sess-receipt', seq=0, turn=0, actor='player', content='before', status='final')])


def receipt(operation='edit-once', fingerprint='request-a'):
    return {'operation_id':operation, 'fingerprint':fingerprint,
            'result':{'content':'after', 'branch_revision':2}}


@pytest.mark.parametrize('sql', [False, True])
@pytest.mark.asyncio
async def test_committed_receipt_restart_replay_conflict_and_compensation(tmp_path, sql):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=sql)
    state = value()
    await repo.save_state(state, 0)
    before = state.model_copy(deep=True)
    state.messages[0].content = 'after'
    await repo.commit_state(state, 0, expected_revision=1, command_receipt=receipt(),
                            outbox_events=[{'event':'message.updated', 'data':{'content':'after'}}])
    restarted = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=sql)
    assert await restarted.lookup_command(state.meta.id, 'edit-once') == {
        'fingerprint':'request-a', 'result':receipt()['result'], 'revision':2}
    retry = before.model_copy(deep=True)
    with pytest.raises(CommandAlreadyCommitted) as replay:
        await restarted.commit_state(retry, 0, expected_revision=1, command_receipt=receipt())
    assert replay.value.receipt['result'] == receipt()['result']
    with pytest.raises(CommandConflict, match='不同请求'):
        await restarted.commit_state(retry, 0, command_receipt=receipt(fingerprint='request-b'))
    assert (await restarted.load_state(state.meta.id)).meta.branch_revision == 2
    if not sql:
        raw = json.loads(repo.path_for(state.meta.id).read_text(encoding='utf-8'))
        assert raw['schema_version'] == 4 and raw['storage_version'] == 2
        from mrp.orchestrator.migration import migrate_state_to_v3_dict
        migrated, changed = migrate_state_to_v3_dict(raw)
        assert not changed and migrated == raw
        with pytest.raises(Exception):
            SessionState.model_validate(migrated)  # Previous reader rejects before its write.
        assert (await restarted.list_summaries())[0].branch_revision == 2
    await restarted.restore_state(before)
    assert await restarted.lookup_command(state.meta.id, 'edit-once') is None


@pytest.mark.asyncio
async def test_commit_failure_after_receipt_insert_rolls_back_all_and_retry(tmp_path, monkeypatch):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    memories = MemoryStore(repo.story_db.path, tmp_path / 'mirrors', None)
    state = value()
    memories.add(MemoryRecord(id='memory', session_id=state.meta.id, character_id='guide',
                             source_message_ids=['msg-receipt'], content='evidence'))
    clock = memories.current_watermark(state.meta.id)
    await repo.save_state(state, clock)
    class FailedCommit(_Connection):
        def commit(self):
            raise OSError('synthetic commit failure after receipt/outbox writes')
    actual = repo.story_db.connect
    monkeypatch.setattr(repo.story_db, 'connect', lambda: sqlite3.connect(repo.story_db.path, factory=FailedCommit))
    with pytest.raises(OSError):
        await repo.commit_state(state, clock, expected_revision=1, command_receipt=receipt(),
            memory_actions=[{'kind':'invalidate_sources', 'message_ids':['msg-receipt']}],
            outbox_events=[{'event':'never', 'data':{}}])
    monkeypatch.setattr(repo.story_db, 'connect', actual)
    assert await repo.lookup_command(state.meta.id, 'edit-once') is None
    assert (await repo.load_state(state.meta.id)).meta.branch_revision == 1
    assert memories.current_watermark(state.meta.id) == clock
    assert not memories.records_for('guide', session_id=state.meta.id)[0].invalidated
    assert repo.story_db.pending_events() == []
    await repo.commit_state(state, clock, expected_revision=1, command_receipt=receipt())
    assert await repo.lookup_command(state.meta.id, 'edit-once')
    memories.close()


@pytest.mark.asyncio
async def test_independent_repositories_concurrent_receipt_gate(tmp_path):
    first = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    second = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    state = value()
    await first.save_state(state, 0)
    results = await asyncio.gather(*(repo.commit_state(state.model_copy(deep=True), 0,
        expected_revision=1, command_receipt=receipt(), outbox_events=[{'event':'once','data':{}}])
        for repo in (first, second)), return_exceptions=True)
    assert sum(isinstance(result, CommandAlreadyCommitted) for result in results) == 1
    assert len(first.story_db.pending_events()) == 1
    assert (await first.load_state(state.meta.id)).meta.branch_revision == 2


@pytest.mark.asyncio
async def test_receipts_survive_migration_rollback_but_import_is_audit_only(tmp_path):
    from mrp.storage.story_migration import StoryMigration
    from mrp.orchestrator.story_archive import export_story, import_story
    repo = SessionRepo(AppPaths(tmp_path))
    state = value()
    await repo.save_state(state, 0)
    await repo.commit_state(state, 0, expected_revision=1, command_receipt=receipt())
    migration = StoryMigration(tmp_path)
    prepared = migration.prepare(state.meta.story_id)
    migration.activate(prepared['id'])
    assert await repo.lookup_command(state.meta.id, 'edit-once')
    migration.rollback(prepared['id'])
    assert await repo.lookup_command(state.meta.id, 'edit-once')
    memories = MemoryStore(repo.story_db.path, tmp_path / 'mirrors', None)
    container = SimpleNamespace(paths=AppPaths(tmp_path), data_root=tmp_path, sessions=repo,
                                saves=SaveRepo(AppPaths(tmp_path)), memory_store=memories)
    imported = await import_story(container, await export_story(container, state.meta.story_id))
    new_state = await repo.load_state(imported['story_id'])
    assert not portable_receipts(new_state)
    assert new_state.generation_operations[AUDIT_KEY]['executable'] is False
    assert await repo.lookup_command(imported['story_id'], 'edit-once') is None
    memories.close()


def test_known_v1_upgrade_atomic_unknown_version_has_no_schema_mutation(tmp_path, monkeypatch):
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE format_version(version INTEGER NOT NULL)')
        db.execute('INSERT INTO format_version VALUES(1)')
    StorySqlite(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version FROM format_version').fetchone()[0] == 4
        db.execute('UPDATE format_version SET version=99')
        db.execute('DROP TABLE command_receipts')
        before = db.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall()
    with pytest.raises(ValueError, match='Unsupported'):
        StorySqlite(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT name,sql FROM sqlite_master ORDER BY name').fetchall() == before


def test_v1_upgrade_ddl_failure_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / 'upgrade.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE format_version(version INTEGER NOT NULL)')
        db.execute('INSERT INTO format_version VALUES(1)')
    class FailingDDL(_Connection):
        def execute(self, sql, *args):
            if 'CREATE TABLE IF NOT EXISTS outbox' in sql:
                raise OSError('synthetic DDL interruption')
            return super().execute(sql, *args)
    monkeypatch.setattr(StorySqlite, 'connect', lambda self: sqlite3.connect(self.path, factory=FailingDDL))
    with pytest.raises(OSError):
        StorySqlite(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version FROM format_version').fetchall() == [(1,)]
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('format_version',)]


def test_message_and_save_forks_retain_audit_without_replay_identity(mrp_client):
    from mrp.tests.test_v6_worldlines import _character, _story
    root, initial = _story(mrp_client, _character(mrp_client))
    container = mrp_client.app.state.container
    runner = container.runners[root]
    async def commit():
        await container.sessions.commit_state(runner.state, 0,
            expected_revision=runner.state.meta.branch_revision, command_receipt=receipt())
    mrp_client.portal.call(commit)
    anchor = initial['messages'][-1]['id']
    fork = mrp_client.post(f'/api/v1/branches/{root}/fork', json={
        'message_id':anchor, 'expected_revision':runner.state.meta.branch_revision,
        'title':'receipt child', 'idempotency_key':'receipt-fork'})
    assert fork.status_code == 200, fork.text
    saved = mrp_client.post(f'/api/v1/sessions/{root}/save?name=receipt')
    assert saved.status_code == 200, saved.text
    save_fork = mrp_client.post(f"/api/v1/saves/{saved.json()['id']}/fork", json={
        'title':'receipt save child', 'idempotency_key':'receipt-save-fork'})
    assert save_fork.status_code == 200, save_fork.text
    for result in (fork, save_fork):
        branch = result.json()['branch_id']
        async def check():
            child = await container.sessions.load_state(branch)
            assert not portable_receipts(child)
            assert child.generation_operations[AUDIT_KEY]['executable'] is False
            assert await container.sessions.lookup_command(branch, 'edit-once') is None
        mrp_client.portal.call(check)


@pytest.mark.parametrize('sql', [False, True])
@pytest.mark.asyncio
async def test_ordinary_restore_to_saved_content_preserves_newer_receipts(tmp_path, sql):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=sql)
    state = value()
    await repo.save_state(state, 0)
    old_save = state.model_copy(deep=True)
    await repo.commit_state(state, 0, command_receipt=receipt())
    old_save.meta.branch_revision = state.meta.branch_revision
    await repo.save_state(old_save, 0)
    assert (await repo.lookup_command(state.meta.id, 'edit-once'))['revision'] == 2
    assert (await repo.load_state(state.meta.id)).meta.branch_revision == 3


@pytest.mark.asyncio
async def test_legacy_failed_first_receipt_keeps_original_file_and_model(tmp_path, monkeypatch):
    import mrp.storage.session_repo as module
    repo = SessionRepo(AppPaths(tmp_path))
    state = value()
    await repo.save_state(state, 0)
    original = repo.path_for(state.meta.id).read_bytes()
    original_state = state.model_dump(mode='json')
    monkeypatch.setattr(module, 'write_json_atomic', lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic failed replace')))
    with pytest.raises(OSError):
        await repo.commit_state(state, 0, command_receipt=receipt())
    assert repo.path_for(state.meta.id).read_bytes() == original
    assert state.model_dump(mode='json') == original_state
    assert await repo.lookup_command(state.meta.id, 'edit-once') is None


@pytest.mark.asyncio
async def test_archive_restore_original_identity_keeps_executable_receipt(tmp_path):
    from mrp.orchestrator.story_archive import export_story, import_story
    def container(root):
        paths = AppPaths(root)
        repo = SessionRepo(paths, sqlite_new_stories=True)
        memory = MemoryStore(repo.story_db.path, root / 'mirrors', None)
        return SimpleNamespace(paths=paths, data_root=root, sessions=repo,
            saves=SaveRepo(paths), memory_store=memory)
    source, target = container(tmp_path / 'source'), container(tmp_path / 'target')
    state = value()
    await source.sessions.save_state(state, 0)
    await source.sessions.commit_state(state, 0, command_receipt=receipt())
    restored = await import_story(target, await export_story(source, state.meta.story_id), preserve_ids=True)
    assert restored['story_id'] == state.meta.id
    assert (await target.sessions.lookup_command(state.meta.id, 'edit-once'))['revision'] == 2
    assert (await target.sessions.load_state(state.meta.id)).meta.branch_revision >= 2
    source.memory_store.close()
    target.memory_store.close()
