import json
from types import SimpleNamespace
import pytest
from mrp.storage.paths import AppPaths
from mrp.storage.session_repo import SessionRepo
from mrp.storage.save_repo import SaveRepo
from mrp.storage.command_receipts import (GenerationIncomplete, CommandConflict,
    portable_claims, inherit_receipt_audit, AUDIT_KEY)
from mrp.orchestrator.memory import MemoryStore
from mrp.tests.test_command_receipts import value


@pytest.mark.parametrize('sql', [False, True])
@pytest.mark.asyncio
async def test_generation_claim_restart_failed_commit_restore_and_new_identity(tmp_path, sql, monkeypatch):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=sql)
    state = value()
    await repo.save_state(state, 0)
    before = state.model_copy(deep=True)
    await repo.reserve_generation(state.meta.id, 'generation-once', 'input-fingerprint')
    restarted = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=sql)
    assert (await restarted.load_state(state.meta.id)).meta.branch_revision == before.meta.branch_revision
    with pytest.raises(GenerationIncomplete) as incomplete:
        await restarted.reserve_generation(state.meta.id, 'generation-once', 'input-fingerprint')
    assert incomplete.value.code == 'operation_incomplete'
    with pytest.raises(CommandConflict):
        await restarted.reserve_generation(state.meta.id, 'generation-once', 'changed')
    if sql:
        original_write = restarted.story_db._put
        monkeypatch.setattr(restarted.story_db, '_put', lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic failed story commit')))
    else:
        import mrp.storage.session_repo as module
        original_write = module.write_json_atomic
        monkeypatch.setattr(module, 'write_json_atomic', lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic failed story commit')))
    with pytest.raises(OSError):
        await restarted.save_state(state, 0)
    if sql:
        monkeypatch.setattr(restarted.story_db, '_put', original_write)
    else:
        monkeypatch.setattr(module, 'write_json_atomic', original_write)
    with pytest.raises(GenerationIncomplete):
        await restarted.reserve_generation(state.meta.id, 'generation-once', 'input-fingerprint')
    if not sql:
        raw = json.loads(repo.path_for(state.meta.id).read_text())
        assert raw['schema_version'] == 4 and raw['storage_version'] == 3
        # The previously supported envelope decoder accepts only storage v2.
        assert raw['storage_version'] != 2
        from mrp.storage.command_receipts import unwrap_legacy_document
        assert unwrap_legacy_document(raw)['schema_version'] == 3
        with pytest.raises(CommandConflict):
            unwrap_legacy_document({**raw, 'storage_version':99})
    # Old cached state and an old save must not remove the independent attempt.
    await restarted.save_state(state, 0)
    await restarted.restore_state(before)
    restored = await restarted.load_state(state.meta.id)
    assert portable_claims(restored) == {'generation-once':'input-fingerprint'}
    child = restored.model_copy(deep=True)
    child.meta.id = 'sess-new-identity'
    inherit_receipt_audit(child, restored)
    assert not portable_claims(child)
    assert child.generation_operations[AUDIT_KEY]['executable'] is False


@pytest.mark.asyncio
async def test_claim_survives_migration_rollback_and_original_archive_restore(tmp_path):
    from mrp.storage.story_migration import StoryMigration
    from mrp.orchestrator.story_archive import export_story, import_story
    source = tmp_path / 'source'
    repo = SessionRepo(AppPaths(source))
    state = value()
    await repo.save_state(state, 0)
    await repo.reserve_generation(state.meta.id, 'attempt', 'fingerprint')
    migration = StoryMigration(source)
    prepared = migration.prepare(state.meta.id)
    migration.activate(prepared['id'])
    with pytest.raises(GenerationIncomplete):
        await repo.reserve_generation(state.meta.id, 'attempt', 'fingerprint')
    migration.rollback(prepared['id'])
    memory = MemoryStore(repo.story_db.path, source / 'mirrors', None)
    container = SimpleNamespace(paths=AppPaths(source), data_root=source, sessions=repo,
        memory_store=memory, saves=SaveRepo(AppPaths(source)))
    data = await export_story(container, state.meta.id)
    target = tmp_path / 'target'
    target_repo = SessionRepo(AppPaths(target), sqlite_new_stories=True)
    target_memory = MemoryStore(target_repo.story_db.path, target / 'mirrors', None)
    other = SimpleNamespace(paths=AppPaths(target), data_root=target, sessions=target_repo,
        memory_store=target_memory, saves=SaveRepo(AppPaths(target)))
    await import_story(other, data, preserve_ids=True)
    with pytest.raises(GenerationIncomplete):
        await target_repo.reserve_generation(state.meta.id, 'attempt', 'fingerprint')
    new = await import_story(other, data)
    assert not portable_claims(await target_repo.load_state(new['story_id']))
    memory.close()
    target_memory.close()


@pytest.mark.parametrize('sql', [False, True])
@pytest.mark.asyncio
async def test_claim_blocks_other_command_but_allows_matching_final_receipt(tmp_path, sql):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=sql)
    state = value()
    await repo.save_state(state, 0)
    await repo.reserve_generation(state.meta.id, 'shared-operation', 'original-fingerprint')
    assert await repo.lookup_generation(state.meta.id, 'shared-operation') == 'original-fingerprint'
    state.meta.title = 'different command'
    with pytest.raises(CommandConflict):
        await repo.commit_state(state, 0, command_receipt={'operation_id':'shared-operation',
            'fingerprint':'different-fingerprint', 'result':{'title':'different command'}})
    assert (await repo.load_state(state.meta.id)).meta.title == 'synthetic'
    await repo.commit_state(state, 0, command_receipt={'operation_id':'shared-operation',
        'fingerprint':'original-fingerprint', 'result':{'title':'completed'}})
    assert (await repo.lookup_command(state.meta.id, 'shared-operation'))['result'] == {'title':'completed'}
