import asyncio
import json
import pytest
from mrp.storage.paths import AppPaths
from mrp.storage.session_repo import SessionRepo
from mrp.storage.save_repo import SaveRepo
from mrp.storage.creation_operations import recover, CreationConflict
from mrp.orchestrator.memory import MemoryStore
from mrp.tests.test_command_receipts import value


def services(tmp_path):
    repo = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    memory = MemoryStore(repo.story_db.path, tmp_path / 'mirrors', None)
    return repo, memory, SaveRepo(AppPaths(tmp_path))


@pytest.mark.asyncio
async def test_creation_entities_hidden_then_publish_together_and_replay_after_restart(tmp_path):
    repo, memory, saves = services(tmp_path)
    state = value()
    calls = []
    async def create():
        calls.append(True)
        await repo.save_state(state, 0)
        identity, _ = await saves.create(state)
        assert await repo.load_state(state.meta.id) is None
        assert await repo.list_summaries() == []
        assert await saves.load(identity) is None
        assert await saves.list() == []
        return {'branch_id':state.meta.id, 'save_id':identity, 'branch_revision':state.meta.branch_revision}
    result = await repo.execute_creation(memory, 'synthetic.create', {'input':'one'}, 'one', create)
    assert await repo.load_state(state.meta.id)
    assert await saves.load(result['save_id'])
    restarted = SessionRepo(AppPaths(tmp_path), sqlite_new_stories=True)
    assert await restarted.execute_creation(memory, 'synthetic.create', {'input':'one'}, 'one', create) == result
    assert len(calls) == 1
    with pytest.raises(CreationConflict):
        await restarted.execute_creation(memory, 'synthetic.create', {'input':'other'}, 'one', create)
    assert len(calls) == 1
    memory.close()


@pytest.mark.asyncio
async def test_running_failure_cleans_only_owned_shadow_never_reexecutes(tmp_path):
    repo, memory, saves = services(tmp_path)
    original = value()
    await repo.save_state(original, 0)
    child = original.model_copy(deep=True)
    child.meta.id = 'sess-interrupted'
    child.meta.branch_revision = 0
    async def create():
        await repo.save_state(child, 0)
        await saves.create(child)
        raise OSError('synthetic interruption before complete result')
    with pytest.raises(OSError):
        await repo.execute_creation(memory, 'create', {}, 'interrupted', create)
    assert await repo.load_state(original.meta.id)
    assert await repo.load_state(child.meta.id) is None
    assert await saves.list(child.meta.id) == []
    with pytest.raises(CreationConflict, match='中断'):
        await repo.execute_creation(memory, 'create', {}, 'interrupted', create)
    memory.close()


@pytest.mark.asyncio
async def test_staged_publication_failure_recovers_without_repeating_callback(tmp_path, monkeypatch):
    from mrp.storage import creation_operations as operations
    repo, memory, saves = services(tmp_path)
    calls = []
    async def create():
        calls.append(True)
        state = value()
        await repo.save_state(state, 0)
        identity, _ = await saves.create(state)
        return {'branch_id':state.meta.id, 'save_id':identity}
    actual = operations.publish
    monkeypatch.setattr(operations, 'publish', lambda *a: (_ for _ in ()).throw(OSError('synthetic final publication failure')))
    with pytest.raises(OSError):
        await repo.execute_creation(memory, 'create', {}, 'staged', create)
    assert await repo.list_summaries() == [] and await saves.list() == []
    monkeypatch.setattr(operations, 'publish', actual)
    recover(repo.story_db, memory)
    result = await repo.execute_creation(memory, 'create', {}, 'staged', create)
    assert len(calls) == 1
    assert await repo.load_state(result['branch_id']) and await saves.load(result['save_id'])
    memory.close()


def test_http_creation_fork_save_and_bookmark_replay(mrp_client):
    from mrp.tests.test_v6_worldlines import _character
    character = _character(mrp_client)
    headers = {'X-Operation-ID':'create-once'}
    payload = {'title':'durable', 'character_ids':[character]}
    first = mrp_client.post('/api/v1/sessions', json=payload, headers=headers)
    assert first.status_code == 200, first.text
    repeated = mrp_client.post('/api/v1/sessions', json=payload, headers=headers)
    assert repeated.json() == first.json()
    branch = first.json()['meta']['id']
    assert first.headers['X-Command-ID'] == 'create-once'
    assert mrp_client.post('/api/v1/sessions', json={**payload,'title':'changed'}, headers=headers).status_code == 409
    save = mrp_client.post(f'/api/v1/sessions/{branch}/save?name=once', headers={'X-Operation-ID':'save-once'})
    assert save.status_code == 200, save.text
    assert mrp_client.post(f'/api/v1/sessions/{branch}/save?name=once', headers={'X-Operation-ID':'save-once'}).json() == save.json()
    bookmark = {'message_id':first.json()['messages'][-1]['id'], 'title':'once'}
    marked = mrp_client.post(f'/api/v1/branches/{branch}/bookmarks', json=bookmark, headers={'X-Operation-ID':'bookmark-once'})
    assert marked.status_code == 200, marked.text
    assert mrp_client.post(f'/api/v1/branches/{branch}/bookmarks', json=bookmark, headers={'X-Operation-ID':'bookmark-once'}).json() == marked.json()


@pytest.mark.asyncio
async def test_cancellation_settles_publication_and_retry_never_recreates(tmp_path):
    repo, memory, saves = services(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def create():
        calls.append(True)
        state = value()
        await repo.save_state(state, 0)
        entered.set()
        await release.wait()
        return {'branch_id':state.meta.id}
    task = asyncio.create_task(repo.execute_creation(memory, 'create', {}, 'cancel', create))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done() and await repo.list_summaries() == []
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await repo.execute_creation(memory, 'create', {}, 'cancel', create))['branch_id'] == value().meta.id
    assert len(calls) == 1
    memory.close()


@pytest.mark.asyncio
async def test_running_crash_recovery_does_not_publish_or_retry(tmp_path):
    from mrp.storage.creation_operations import reserve, active_creation
    repo, memory, saves = services(tmp_path)
    reserve(repo.story_db, 'crashed', 'create', {})
    token = active_creation.set({'operation_id':'crashed','path':str(repo.story_db.path.resolve())})
    try:
        state = value()
        await repo.save_state(state, 0)
        await saves.create(state)
    finally:
        active_creation.reset(token)
    recover(repo.story_db, memory)
    assert await repo.list_summaries() == [] and await saves.list() == []
    async def forbidden():
        raise AssertionError('recovery must never repeat external work')
    with pytest.raises(CreationConflict):
        await repo.execute_creation(memory, 'create', {}, 'crashed', forbidden)
    memory.close()


@pytest.mark.asyncio
async def test_creation_receipt_survives_story_rollback_and_archive_original_identity(tmp_path):
    from types import SimpleNamespace
    from mrp.storage.story_migration import StoryMigration
    from mrp.orchestrator.story_archive import export_story, import_story
    root = tmp_path / 'source'
    repo = SessionRepo(AppPaths(root))
    memory = MemoryStore(repo.story_db.path, root / 'mirrors', None)
    parent = value()
    await repo.save_state(parent, 0)
    migration = StoryMigration(root)
    prepared = migration.prepare(parent.meta.id)
    migration.activate(prepared['id'])
    child = parent.model_copy(deep=True)
    child.meta.id = 'sess-created-child'
    child.meta.branch_revision = 0
    child.meta.parent_branch_id = parent.meta.id
    async def create():
        await repo.save_state(child, 0)
        return {'branch_id':child.meta.id, 'story_id':parent.meta.id, 'branch_revision':child.meta.branch_revision}
    result = await repo.execute_creation(memory, 'branch.create', {'source':parent.meta.id}, 'child-once', create)
    migration.rollback(prepared['id'])
    restarted = SessionRepo(AppPaths(root))
    async def forbidden():
        raise AssertionError('must replay original identity')
    assert await restarted.execute_creation(memory, 'branch.create', {'source':parent.meta.id}, 'child-once', forbidden) == result
    source = SimpleNamespace(paths=AppPaths(root),data_root=root,sessions=restarted,memory_store=memory,saves=SaveRepo(AppPaths(root)))
    raw = await export_story(source, parent.meta.id)
    target_repo, target_memory, target_saves = services(tmp_path / 'target')
    target = SimpleNamespace(paths=AppPaths(tmp_path/'target'),data_root=tmp_path/'target',sessions=target_repo,memory_store=target_memory,saves=target_saves)
    await import_story(target, raw, preserve_ids=True)
    assert await target_repo.execute_creation(target_memory, 'branch.create', {'source':parent.meta.id}, 'child-once', forbidden) == result
    imported = await import_story(target, raw)
    new_root = await target_repo.load_state(imported['story_id'])
    assert new_root.generation_operations['__mrp_creation_audit_v1__']['executable'] is False
    memory.close()
    target_memory.close()


def test_restore_receipt_response_reflects_restored_content(mrp_client):
    from mrp.tests.test_v6_worldlines import _character, _story
    root, initial = _story(mrp_client, _character(mrp_client))
    saved = mrp_client.post(f'/api/v1/sessions/{root}/save').json()
    mrp_client.patch(f'/api/v1/sessions/{root}', json={'title':'later'})
    response = mrp_client.post(f"/api/v1/saves/{saved['id']}/restore", headers={'X-Operation-ID':'restore-once'})
    assert response.status_code == 200, response.text
    assert response.json()['meta']['title'] == initial['meta']['title']
    assert int(response.headers['X-Branch-Revision']) == response.json()['meta']['branch_revision']
    repeated = mrp_client.post(f"/api/v1/saves/{saved['id']}/restore", headers={'X-Operation-ID':'restore-once'})
    assert repeated.json() == response.json()
    mrp_client.portal.call(mrp_client.app.state.container.saves.delete, saved['id'])
    missing_source_replay = mrp_client.post(f"/api/v1/saves/{saved['id']}/restore", headers={'X-Operation-ID':'restore-once'})
    assert missing_source_replay.status_code == 200
    assert missing_source_replay.json() == response.json()


def test_scenario_and_st_import_replay_precedes_changed_source(mrp_client):
    from mrp.tests.test_v6_worldlines import _character
    from mrp.tests.test_stage3_workflows import _sample
    from mrp.importers.st_chat import parse_st_chat
    character = _character(mrp_client)
    scenario = mrp_client.post('/api/v1/scenarios', json={'title':'synthetic', 'character_ids':[character]}).json()
    first = mrp_client.post(f"/api/v1/scenarios/{scenario['id']}/start", json={'opening_scene':'synthetic opening'}, headers={'X-Operation-ID':'scenario-once'})
    assert first.status_code == 200, first.text
    container = mrp_client.app.state.container
    async def forbidden(*a, **kw):
        raise AssertionError('replay must precede creating another runner')
    container.create_scenario_session = forbidden
    repeated = mrp_client.post(f"/api/v1/scenarios/{scenario['id']}/start", json={'opening_scene':'synthetic opening'}, headers={'X-Operation-ID':'scenario-once'})
    assert repeated.json() == first.json()
    raw = _sample()
    payload = {'jsonl':raw.decode(), 'source_sha256':parse_st_chat(raw)[2]['source_sha256'],
               'character_id':character, 'user_name':'player', 'acknowledge_degraded':True}
    imported = mrp_client.post('/api/v1/chat-import/st', json=payload, headers={'X-Operation-ID':'st-once'})
    assert imported.status_code == 200, imported.text
    container.characters.pop(character)
    replay = mrp_client.post('/api/v1/chat-import/st', json=payload, headers={'X-Operation-ID':'st-once'})
    assert replay.status_code == 200 and replay.json()['branch_id'] == imported.json()['branch_id']


@pytest.mark.asyncio
async def test_conflicting_staged_recovery_isolated_from_other_stories(tmp_path, monkeypatch):
    from mrp.storage import creation_operations as operations
    repo, memory, _ = services(tmp_path)
    original = value()
    await repo.save_state(original, 0)
    actual = operations.publish
    monkeypatch.setattr(operations, 'publish', lambda *a: (_ for _ in ()).throw(OSError('synthetic publication interrupted')))
    async def bad():
        await repo.adopt_creation_branch(original)
        child = original.model_copy(deep=True)
        child.meta.id = 'sess-conflicted'
        child.meta.branch_revision = 0
        await repo.save_state(child, 0)
        return {'branch_id':child.meta.id}
    async def good():
        child = original.model_copy(deep=True)
        child.meta.id = 'sess-good'
        child.meta.branch_revision = 0
        await repo.save_state(child, 0)
        return {'branch_id':child.meta.id}
    for operation, create in [('bad', bad), ('good', good)]:
        with pytest.raises(OSError):
            await repo.execute_creation(memory, 'create', {}, operation, create)
    original.meta.title = 'later revision'
    await repo.save_state(original, 0)
    monkeypatch.setattr(operations, 'publish', actual)
    recover(repo.story_db, memory)
    assert await repo.load_state('sess-conflicted') is None
    assert await repo.load_state('sess-good')
    assert (await repo.load_state(original.meta.id)).meta.title == 'later revision'
    with repo.story_db.connect() as connection:
        assert connection.execute('SELECT reason FROM creation_failure_details WHERE operation_id=?', ('bad',)).fetchone()
        assert connection.execute('SELECT status FROM creation_operations WHERE operation_id=?', ('bad',)).fetchone() == ('aborted',)
    memory.close()


@pytest.mark.parametrize('schema', [1, 2])
def test_old_schema_restore_body_header_receipt_use_actual_revision_rule(mrp_client, schema):
    from mrp.tests.test_v6_worldlines import _character, _story
    from mrp.shared.models import SaveFile, SessionState
    root, initial = _story(mrp_client, _character(mrp_client))
    state = SessionState.model_validate(initial)
    state.schema_version = schema
    state.meta.title = 'old schema content'
    save = SaveFile(schema_version=schema, state=state)
    container = mrp_client.app.state.container
    mrp_client.portal.call(container.saves.publish, 'save-old-schema', save)
    response = mrp_client.post('/api/v1/saves/save-old-schema/restore', headers={'X-Operation-ID':'old-restore'})
    assert response.status_code == 200, response.text
    assert response.json()['meta']['branch_revision'] == int(response.headers['X-Branch-Revision'])
    assert response.json()['meta']['branch_revision'] == initial['meta']['branch_revision']
    assert mrp_client.post('/api/v1/saves/save-old-schema/restore', headers={'X-Operation-ID':'old-restore'}).json() == response.json()
