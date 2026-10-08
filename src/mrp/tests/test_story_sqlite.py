import pytest
import io
import zipfile

from mrp.shared.models import Message, SessionMeta, SessionState
from mrp.shared.models import MemoryRecord
from mrp.orchestrator.memory import MemoryStore
from mrp.storage.paths import AppPaths
from mrp.storage.session_repo import SessionRepo
from mrp.storage.save_repo import SaveRepo
from mrp.storage.story_sqlite import BranchRevisionConflict


def state():
    return SessionState(schema_version=3, meta=SessionMeta(id='sess-synthetic', title='synthetic', story_id='sess-synthetic'),
                        messages=[Message(id='msg-1', session_id='sess-synthetic', seq=0, turn=0, actor='player', content='first', status='final')])


@pytest.mark.asyncio
async def test_sqlite_commit_cas_outbox_no_json_and_immutable_save(tmp_path):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    value = state()
    await repo.create_state_exclusive(value, 0)
    assert not repo.path_for(value.meta.id).exists()
    saved = value.model_copy(deep=True)
    value.messages[0].content = 'second'
    await repo.commit_state(value, 0, expected_revision=1, operation_id='edit-1',
                            outbox_events=[{'event': 'message.updated', 'data': {'message_id': 'msg-1'}}])
    assert (await repo.load_state(value.meta.id)).messages[0].content == 'second'
    assert len(repo.story_db.pending_events()) == 1
    with pytest.raises(BranchRevisionConflict):
        await repo.commit_state(saved, 0, expected_revision=1)
    assert saved.meta.branch_revision == 1
    assert (await repo.load_state(value.meta.id)).meta.branch_revision == 2
    saves = SaveRepo(AppPaths(tmp_path))
    save_id, _ = await saves.create(value, memory_snapshot=[])
    value.messages[0].content = 'third'
    await repo.save_state(value, 0)
    assert (await saves.load(save_id)).state.messages[0].content == 'second'
    assert len(await saves.list()) == 1
    assert not saves.path_for(save_id).exists()
    assert await repo.delete_state(value.meta.id)
    assert await repo.load_state(value.meta.id) is None
    with pytest.raises(BranchRevisionConflict):
        await repo.save_state(value, 0)
    assert await repo.load_state(value.meta.id) is None


@pytest.mark.asyncio
async def test_components_share_material_and_transaction_failure(tmp_path, monkeypatch):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    value = state()
    await repo.save_state(value, 0)
    old = (await repo.load_state(value.meta.id)).model_dump(mode='json')
    original = repo.story_db._put
    calls = 0
    def fail(db, item):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('synthetic failure after header write')
        return original(db, item)
    monkeypatch.setattr(repo.story_db, '_put', fail)
    with pytest.raises(OSError):
        await repo.save_state(value, 0, outbox_events=[{'event': 'never'}])
    assert (await repo.load_state(value.meta.id)).model_dump(mode='json') == old
    assert repo.story_db.pending_events() == []


@pytest.mark.asyncio
async def test_indexed_window_keeps_groups_and_does_not_resolve_history(tmp_path, monkeypatch):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    value = state()
    value.messages = [Message(id=f'msg-{index}', session_id=value.meta.id, seq=index, turn=index,
                              actor='player', content=str(index), status='final',
                              input_group_id='edge' if index in (2, 3, 4) else None)
                      for index in range(9)]
    await repo.save_state(value, 0)
    monkeypatch.setattr(repo.story_db, 'load', lambda *a, **kw: pytest.fail('whole-state load forbidden'))
    window, turn, latest, before = repo.story_db.read_window(value.meta.id, limit=2, before_seq=5)
    assert [m.seq for m in window.messages] == [2, 3, 4]
    assert (turn, latest, before) == (8, 8, 2)
    assert not window.state_revisions and not window.generation_operations and not window.lorebooks


@pytest.mark.asyncio
async def test_memory_story_outbox_commit_together_and_failure_rolls_back(tmp_path, monkeypatch):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    memories = MemoryStore(repo.story_db.path, tmp_path / 'mirrors', None)
    value = state()
    memories.add(MemoryRecord(id='mem-1', session_id=value.meta.id, character_id='guide',
                              source_message_ids=['msg-1'], content='original memory'))
    watermark = memories.current_watermark(value.meta.id)
    await repo.save_state(value, watermark)
    original = repo.story_db._put
    monkeypatch.setattr(repo.story_db, '_put', lambda *a: (_ for _ in ()).throw(OSError('synthetic after memory SQL')))
    with pytest.raises(OSError):
        await repo.commit_state(value, watermark, expected_revision=1,
                                memory_actions=[{'kind':'invalidate_sources', 'message_ids':['msg-1']}],
                                outbox_events=[{'event':'never'}])
    assert not memories.records_for('guide', session_id=value.meta.id)[0].invalidated
    assert memories.current_watermark(value.meta.id) == watermark
    assert repo.story_db.pending_events() == []
    monkeypatch.setattr(repo.story_db, '_put', original)
    await repo.commit_state(value, watermark, expected_revision=1,
                            memory_actions=[{'kind':'invalidate_sources', 'message_ids':['msg-1']}],
                            outbox_events=[{'event':'committed'}])
    assert memories.records_for('guide', session_id=value.meta.id)[0].invalidated
    current = await repo.load_state(value.meta.id)
    head = next(r for r in current.state_revisions if r.id == current.head_state_revision_id)
    assert head.memory_watermark == memories.current_watermark(value.meta.id)
    assert repo.story_db.pending_events()[0]['event']['event'] == 'committed'
    assert memories.history_at(value.meta.id, watermark)[0].content == 'original memory'
    memories.close()


@pytest.mark.asyncio
async def test_whole_story_shadow_activation_current_rollback(tmp_path):
    from mrp.storage.story_migration import StoryMigration
    legacy = SessionRepo(AppPaths(tmp_path))
    value = state()
    await legacy.save_state(value, 0)
    source_bytes = legacy.path_for(value.meta.id).read_bytes()
    save_id, _ = await SaveRepo(AppPaths(tmp_path)).create(value, memory_snapshot=[])
    migration = StoryMigration(tmp_path)
    manifest = migration.prepare(value.meta.story_id)
    assert legacy.path_for(value.meta.id).read_bytes() == source_bytes
    migration.activate(manifest['id'])
    active = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    current = await active.load_state(value.meta.id)
    current.messages[0].content = 'new after switch'
    await active.save_state(current, 0)
    child = current.model_copy(deep=True)
    child.meta.id = 'sess-new-child'
    child.meta.parent_branch_id = value.meta.id
    child.meta.branch_revision = 0
    await active.create_state_exclusive(child, 0)
    assert (await SaveRepo(AppPaths(tmp_path)).load(save_id)).state.messages[0].content == 'first'
    result = migration.rollback(manifest['id'])
    assert result['exported_current_branches'] == 2
    assert (await active.load_state(value.meta.id)).messages[0].content == 'new after switch'
    assert await active.load_state(child.meta.id) is not None
    assert (tmp_path / 'migrations' / manifest['id'] / f'{value.meta.id}.json').read_bytes() == source_bytes


@pytest.mark.asyncio
async def test_deleted_migrated_branch_rejects_late_write_preserves_guard(tmp_path):
    from mrp.storage.story_migration import StoryMigration
    legacy = SessionRepo(AppPaths(tmp_path))
    value = state()
    await legacy.save_state(value, 0)
    migration = StoryMigration(tmp_path)
    manifest = migration.prepare(value.meta.story_id)
    migration.activate(manifest['id'])
    guard = legacy.path_for(value.meta.id).read_bytes()
    assert await legacy.delete_state(value.meta.id)
    with pytest.raises(BranchRevisionConflict):
        await legacy.save_state(value, 0)
    with pytest.raises(BranchRevisionConflict):
        await legacy.restore_state(value)
    assert not await legacy.delete_state(value.meta.id)
    assert legacy.path_for(value.meta.id).read_bytes() == guard


def test_migration_backup_releases_file_handles(tmp_path):
    from mrp.storage.atomic import write_json_atomic
    from mrp.storage.story_migration import StoryMigration
    value = state()
    write_json_atomic(AppPaths(tmp_path).sessions_dir / f'{value.meta.id}.json', value)
    migration = StoryMigration(tmp_path)
    prepared = migration.prepare(value.meta.story_id)
    backup = tmp_path / 'migrations' / prepared['id'] / 'memory-consistent.sqlite'
    renamed = backup.with_name('renamed-backup.sqlite')
    backup.rename(renamed)
    renamed.unlink()
    assert not backup.exists() and not renamed.exists()
    # The source connection must also be released after the backup API returns.
    source = migration.db.path
    relocated = source.with_name('renamed-memory.sqlite')
    source.rename(relocated)
    relocated.rename(source)


def test_migration_lease_rejects_online_and_cutover_recovers(tmp_path, monkeypatch):
    from mrp.storage.data_lease import DataRootBusy, DataRootLease
    from mrp.storage.story_migration import StoryMigration
    from mrp.storage.atomic import write_json_atomic
    from mrp.storage import story_migration as migration_module
    with DataRootLease(tmp_path, purpose='server'):
        with pytest.raises(DataRootBusy):
            StoryMigration(tmp_path)
    value = state()
    write_json_atomic(AppPaths(tmp_path).sessions_dir / f'{value.meta.id}.json', value)
    migration = StoryMigration(tmp_path)
    prepared = migration.prepare(value.meta.story_id)
    actual = migration_module.write_json_atomic
    monkeypatch.setattr(migration_module, 'write_json_atomic', lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic marker crash')))
    with pytest.raises(OSError):
        migration.activate(prepared['id'])
    with pytest.raises(RuntimeError, match='recover'):
        with DataRootLease(tmp_path, purpose='server'):
            pass
    monkeypatch.setattr(migration_module, 'write_json_atomic', actual)
    assert migration.recover(prepared['id'])['status'] == 'active'
    with DataRootLease(tmp_path, purpose='server'):
        assert migration.db.active(value.meta.id)


@pytest.mark.asyncio
async def test_archive_retains_edited_historical_memory_and_partial_import_recovery(tmp_path):
    from types import SimpleNamespace
    from mrp.orchestrator.story_archive import export_story, import_story, recover_pending_imports
    from mrp.orchestrator.worldline_state import record_message_revision
    from mrp.storage.atomic import write_json_atomic
    paths = AppPaths(tmp_path)
    repo = SessionRepo(paths, sqlite_new_stories=True)
    memories = MemoryStore(repo.story_db.path, tmp_path / 'mirrors', None)
    container = SimpleNamespace(paths=paths, data_root=tmp_path, sessions=repo,
                                saves=SaveRepo(paths), memory_store=memories)
    value = state()
    secret = 'sk-or-v1-' + 'x' * 40
    record = MemoryRecord(id='mem-original', session_id=value.meta.id, character_id='guide',
                          source_message_ids=['msg-1'], content='old evidence ' + secret)
    memories.add(record)
    first = memories.current_watermark(value.meta.id)
    record_message_revision(value, value.messages[0], first)
    await repo.save_state(value, first)
    record.content = 'new evidence'
    memories.update_record(record)
    await repo.save_state(value, memories.current_watermark(value.meta.id))
    archive_bytes = await export_story(container, value.meta.story_id)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert 'memory_history.json' in archive.namelist()
        assert all(secret.encode() not in archive.read(name) for name in archive.namelist())
    imported = await import_story(container, archive_bytes)
    branch = imported['story_id']
    assert memories.history_at(branch, first)[0].content.startswith('old evidence')
    assert secret not in memories.history_at(branch, first)[0].content
    assert memories.history_at(branch, memories.current_watermark(branch))[0].content == 'new evidence'
    partial = state()
    partial.meta.id = 'sess-partial'
    partial.generation_operations['import-publication'] = {'transaction_id': 'import-crash'}
    await repo.save_state(partial, 0)
    write_json_atomic(tmp_path / 'pending_imports' / 'import-crash.json',
                      {'transaction_id':'import-crash', 'branches':['sess-partial', 'sess-not-created', value.meta.id], 'saves':[]})
    recover_pending_imports(container)
    assert await repo.load_state('sess-partial') is None
    assert await repo.load_state(value.meta.id) is not None
    assert not (tmp_path / 'pending_imports' / 'import-crash.json').exists()
    memories.close()


@pytest.mark.asyncio
async def test_append_material_bytes_do_not_scale_with_history(tmp_path):
    growth = []
    for count in (10, 1000):
        repo = SessionRepo(AppPaths(tmp_path / str(count)), sqlite_new_stories=True)
        value = state()
        value.messages = [Message(id=f'msg-{i}', session_id=value.meta.id, seq=i, turn=i,
                                  actor='player', status='final', content='synthetic ' * 50)
                          for i in range(count)]
        await repo.save_state(value, 0)
        def material_bytes():
            with repo.story_db.connect() as db:
                return db.execute('SELECT SUM(length(payload)) FROM materials').fetchone()[0]
        before = material_bytes()
        value.messages.append(Message(id='msg-added', session_id=value.meta.id, seq=count, turn=count,
                                      actor='player', status='final', content='one new reply'))
        await repo.save_state(value, 0)
        growth.append(material_bytes() - before)
    assert growth[1] <= growth[0] + 100


@pytest.mark.asyncio
async def test_legacy_write_failure_preserves_caller_revision_and_head(tmp_path, monkeypatch):
    import mrp.storage.session_repo as module
    repo = SessionRepo(AppPaths(tmp_path))
    value = state()
    await repo.save_state(value, 0)
    before = value.model_dump(mode='json')
    monkeypatch.setattr(module, 'write_json_atomic', lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic failed replace')))
    with pytest.raises(OSError):
        await repo.save_state(value, 1)
    assert value.model_dump(mode='json') == before


@pytest.mark.asyncio
async def test_compensation_withdraws_pending_outbox_and_operation_ledger(tmp_path):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    value = state()
    await repo.save_state(value, 0)
    before = value.model_copy(deep=True)
    value.messages[0].content = 'withdrawn'
    await repo.commit_state(value, 0, expected_revision=1, operation_id='withdrawn-op',
                            outbox_events=[{'event':'message.final', 'data':{'content':'withdrawn'}}])
    await repo.restore_state(before)
    assert repo.story_db.pending_events() == []
    with repo.story_db.connect() as db:
        assert db.execute('SELECT operation_id FROM commits WHERE branch_id=? AND revision=2', (value.meta.id,)).fetchone() is None
    before.messages[0].content = 'replacement'
    await repo.commit_state(before, 0, expected_revision=1, operation_id='replacement-op')
    with repo.story_db.connect() as db:
        assert db.execute('SELECT operation_id FROM commits WHERE branch_id=? AND revision=2', (value.meta.id,)).fetchone()[0] == 'replacement-op'


@pytest.mark.asyncio
async def test_compensation_refuses_to_partially_undo_committed_memory(tmp_path):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    memories = MemoryStore(repo.story_db.path, tmp_path / 'mirrors', None)
    value = state()
    memories.add(MemoryRecord(id='mem-effect', session_id=value.meta.id, character_id='guide',
                             source_message_ids=['msg-1'], content='evidence'))
    await repo.save_state(value, memories.current_watermark(value.meta.id))
    before = value.model_copy(deep=True)
    value.messages[0].content = 'committed change'
    await repo.commit_state(value, memories.current_watermark(value.meta.id), expected_revision=1,
                            operation_id='memory-effect',
                            memory_actions=[{'kind':'invalidate_sources', 'message_ids':['msg-1']}],
                            outbox_events=[{'event':'message.updated', 'data':{'content':'committed change'}}])
    events = repo.story_db.pending_events()
    clock = memories.current_watermark(value.meta.id)
    with pytest.raises(BranchRevisionConflict, match='记忆变化'):
        await repo.restore_state(before)
    assert (await repo.load_state(value.meta.id)).messages[0].content == 'committed change'
    assert (await repo.load_state(value.meta.id)).meta.branch_revision == 2
    assert memories.records_for('guide', session_id=value.meta.id)[0].invalidated
    assert memories.current_watermark(value.meta.id) == clock
    assert repo.story_db.pending_events() == events
    with repo.story_db.connect() as db:
        assert db.execute('SELECT operation_id FROM commits WHERE branch_id=? AND revision=2', (value.meta.id,)).fetchone()[0] == 'memory-effect'
    memories.close()
