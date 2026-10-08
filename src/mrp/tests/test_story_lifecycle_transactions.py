import asyncio
import json
import pytest
from mrp.tests.test_command_receipts import value,receipt
from mrp.storage.paths import AppPaths
from mrp.storage.session_repo import SessionRepo
from mrp.storage.save_repo import SaveRepo
from mrp.storage.lifecycle_repo import LifecycleConflict
from mrp.orchestrator.memory import MemoryStore
from mrp.shared.models import MemoryRecord

@pytest.mark.asyncio
@pytest.mark.parametrize('legacy',[False,True])
async def test_closure_soft_delete_same_identity_restore_receipts_claims(tmp_path,legacy):
    sessions=SessionRepo(AppPaths(tmp_path),sqlite_new_stories=not legacy)
    saves=SaveRepo(AppPaths(tmp_path)); memory=MemoryStore(sessions.story_db.path,tmp_path/'mirrors',None)
    try:
        root=value(); await sessions.save_state(root,0)
        await sessions.commit_state(root,0,command_receipt=receipt())
        await sessions.reserve_generation(root.meta.id,'paid-once','paid-fp')
        child=root.model_copy(deep=True); child.meta.id='sess-child'; child.meta.parent_branch_id=root.meta.id
        child.generation_operations={}; await sessions.save_state(child,0)
        save_id,_=await saves.create(root)
        record=MemoryRecord(character_id='actor',session_id=root.meta.id,content='private memory',turn_start=0,turn_end=0)
        memory.add(record)
        repo=sessions.create_lifecycle_repo(saves); before=repo.membership(root.meta.id)
        repo.normalize_story(root.meta.id)
        assert repo.membership(root.meta.id)==before
        assert len(await sessions.list_summaries())==2 and await saves.load(save_id)
        if legacy: assert json.loads(sessions.path_for(root.meta.id).read_text())['storage']=='sqlite'
        assert repo.reserve('delete-one','delete',root.meta.id,{}) is None
        result=repo.commit('delete-one','delete',root.meta.id,membership=before,entry={'sha256':'synthetic','title':'Synthetic'})
        assert await sessions.list_summaries()==[] and await saves.load(save_id) is None
        assert memory.records_for('actor',session_id=root.meta.id)==[]
        assert repo.reserve('restore-one','restore',root.meta.id,{}) is None
        restored=repo.commit('restore-one','restore',root.meta.id,generation=result['generation_id'])
        assert {row['new_id'] for row in restored['branches']}=={root.meta.id,child.meta.id}
        assert await saves.load(save_id)
        assert (await sessions.lookup_command(root.meta.id,'edit-once'))['fingerprint']=='request-a'
        assert await sessions.lookup_generation(root.meta.id,'paid-once')=='paid-fp'
        assert memory.records_for('actor',session_id=root.meta.id)[0].content=='private memory'
        assert (await sessions.load_state(root.meta.id)).meta.branch_revision==root.meta.branch_revision+2
        late=root.model_copy(deep=True)
        with pytest.raises(ValueError): await sessions.commit_state(late,0,expected_revision=root.meta.branch_revision)
    finally: memory.close()

@pytest.mark.asyncio
async def test_purge_receipt_terminal_and_unrelated_copy_survives(tmp_path):
    sessions=SessionRepo(AppPaths(tmp_path),sqlite_new_stories=True); saves=SaveRepo(AppPaths(tmp_path))
    memory=MemoryStore(sessions.story_db.path,tmp_path/'mirrors',None)
    try:
        root=value(); await sessions.save_state(root,0); await sessions.commit_state(root,0,command_receipt=receipt())
        repo=sessions.create_lifecycle_repo(saves)
        with sessions.story_db.transaction() as db:
            db.execute('INSERT INTO creation_operations VALUES(?,?,?,?,?,?)',('copy','story.import','fp','published',json.dumps({'branch_id':'another','source_story_id':root.meta.id}),1))
            db.execute('INSERT INTO creation_entities VALUES(?,?,?)',('copy','branch','another'))
            db.execute('INSERT INTO creation_operations VALUES(?,?,?,?,?,?)',('original','story.create','fp','published',json.dumps({'meta':{'id':root.meta.id,'title':'private'}}),1))
            db.execute('INSERT INTO creation_entities VALUES(?,?,?)',('original','branch',root.meta.id))
        repo.reserve('del','delete',root.meta.id,{})
        result=repo.commit('del','delete',root.meta.id,entry={'sha256':'safe'})
        repo.reserve('purge','purge',root.meta.id,{})
        repo.commit('purge','purge',root.meta.id,generation=result['generation_id'])
        with sessions.story_db.connect() as db:
            assert db.execute('SELECT status FROM creation_operations WHERE operation_id=?',('copy',)).fetchone()==('published',)
            assert db.execute('SELECT status,result FROM creation_operations WHERE operation_id=?',('original',)).fetchone()==('result_purged',None)
            assert not any('before' in payload for (payload,) in db.execute('SELECT payload FROM materials'))
        with pytest.raises(LifecycleConflict) as error: sessions.require_visible_branch(root.meta.id)
        assert error.value.status_code==410
        with pytest.raises(LifecycleConflict): await sessions.lookup_command_scope('edit-once','request-a')
        with pytest.raises(LifecycleConflict): repo.reserve('del','delete',root.meta.id,{})
    finally: memory.close()

@pytest.mark.asyncio
async def test_shadow_cutover_postcommit_guard_failure_recovers_original_bytes(tmp_path,monkeypatch):
    sessions=SessionRepo(AppPaths(tmp_path)); saves=SaveRepo(AppPaths(tmp_path)); repo=sessions.create_lifecycle_repo(saves)
    root=value(); await sessions.save_state(root,0); save_id,_=await saves.create(root)
    original=sessions.path_for(root.meta.id).read_bytes()
    actual=repo.finish_cutover
    monkeypatch.setattr(repo,'finish_cutover',lambda manifest: (_ for _ in ()).throw(OSError('synthetic guard crash')))
    with pytest.raises(OSError): repo.normalize_story(root.meta.id)
    assert sessions.path_for(root.meta.id).read_bytes()==original
    assert await sessions.load_state_readonly(root.meta.id)
    assert await saves.load(save_id)
    with pytest.raises(LifecycleConflict): repo.require_unlocked(root.meta.id)
    monkeypatch.setattr(repo,'finish_cutover',actual); repo.recover_cutovers()
    repo.require_unlocked(root.meta.id)
    frozen=list((tmp_path/'online_cutovers'/root.meta.id).glob('*.json'))
    assert original in [path.read_bytes() for path in frozen]
    assert json.loads(sessions.path_for(root.meta.id).read_text())['storage']=='sqlite'

@pytest.mark.asyncio
async def test_lifecycle_atomic_preset_midwrite_failure_and_late_fork(tmp_path,monkeypatch):
    sessions=SessionRepo(AppPaths(tmp_path),sqlite_new_stories=True); saves=SaveRepo(AppPaths(tmp_path)); repo=sessions.create_lifecycle_repo(saves)
    root=value(); await sessions.save_state(root,0)
    child=root.model_copy(deep=True); child.meta.id='sess-child'; child.meta.parent_branch_id=root.meta.id
    await sessions.save_state(child,0)
    before=repo.membership(root.meta.id); actual=sessions.story_db.write; count=0
    def fail_second(*args,**kwargs):
        nonlocal count
        count+=1
        if count==2: raise OSError('synthetic second branch failure')
        return actual(*args,**kwargs)
    repo.reserve('preset','preset',root.meta.id,{})
    monkeypatch.setattr(sessions.story_db,'write',fail_second)
    with pytest.raises(OSError): repo.commit('preset','preset',root.meta.id,preset_id='new',preset={'name':'new'})
    assert repo.membership(root.meta.id)==before
    assert (await sessions.load_state(root.meta.id)).meta.prompt_preset_id is None
    monkeypatch.setattr(sessions.story_db,'write',actual)
    repo.reserve('delete','delete',root.meta.id,{})
    repo.commit('delete','delete',root.meta.id,entry={'sha256':'safe'})
    late=root.model_copy(deep=True); late.meta.id='sess-late'; late.meta.parent_branch_id=root.meta.id
    with pytest.raises(ValueError): await sessions.save_state(late,0)
    with pytest.raises(ValueError): await saves.create(root)

@pytest.mark.asyncio
async def test_purge_cleanup_retains_independent_snapshots_and_online_originals(tmp_path):
    from mrp.tests.conftest import make_test_container
    from mrp.tests.test_phase3_acceptance import seed_character
    container=make_test_container(tmp_path)
    try:
        runner=await container.create_session('Synthetic',[await seed_character(container)],'',[],register=False)
        await container.persist_session(runner)
        independent=await container.story_backups.snapshot(runner.state.meta.id,reason='independent')
        independent_path=container.story_backups.root/runner.state.meta.id/independent['filename']
        independent_bytes=independent_path.read_bytes()
        frozen=container.data_root/'online_cutovers'/runner.state.meta.id/'independent-original.json'
        frozen.parent.mkdir(parents=True); frozen.write_bytes(b'original historical evidence')
        deleted,_=await container.story_lifecycle.execute('delete',runner.state.meta.id,{},'delete')
        trash=container.story_lifecycle.repo.trash(runner.state.meta.id)
        trash_path=container.story_backups.root/runner.state.meta.id/trash['filename']
        await container.story_lifecycle.execute('purge',runner.state.meta.id,{'generation_id':deleted['generation_id']},'purge')
        assert not trash_path.exists()
        assert independent_path.read_bytes()==independent_bytes
        assert frozen.read_bytes()==b'original historical evidence'
    finally: await container.aclose()

@pytest.mark.asyncio
async def test_legacy_trash_zip_adoption_restores_original_identity_and_memory(tmp_path):
    from mrp.tests.conftest import make_test_container
    from mrp.tests.test_phase3_acceptance import seed_character
    container=make_test_container(tmp_path)
    try:
        cid=await seed_character(container)
        runner=await container.create_session('Legacy trash',[cid],'',[],register=False)
        await container.persist_session(runner)
        branch=runner.state.meta.id
        record=MemoryRecord(character_id=cid,session_id=branch,content='historical memory',turn_start=0,turn_end=0)
        container.memory_store.add(record)
        await container.story_backups.trash_story(branch)
        await container.sessions.delete_state(branch)
        container.memory_store.purge_branch(branch)
        await container.story_backups.confirm_trashed(branch)
        result,_=await asyncio.wait_for(container.story_lifecycle.execute('restore',branch,{'generation_id':None},'restore-legacy'),5)
        assert result['story_id']==branch
        assert result['branches']==[{'old_id':branch,'new_id':branch}]
        assert container.memory_store.records_for(cid,session_id=branch)[0].content=='historical memory'
        assert await container.sessions.load_state(branch)
        assert container.story_lifecycle.repo.trash()==[]
    finally: await container.aclose()

@pytest.mark.asyncio
async def test_durable_purge_delivery_failure_is_pending_and_replay_only_cleans(tmp_path,monkeypatch):
    from mrp.tests.conftest import make_test_container
    from mrp.tests.test_phase3_acceptance import seed_character
    container=make_test_container(tmp_path)
    try:
        runner=await container.create_session('Synthetic',[await seed_character(container)],'',[],register=False)
        await container.persist_session(runner); branch=runner.state.meta.id
        deleted,_=await container.story_lifecycle.execute('delete',branch,{},'delete')
        repo=container.story_lifecycle.repo; cleanup=repo.cleanup_purges
        monkeypatch.setattr(repo,'cleanup_purges',lambda container: (_ for _ in ()).throw(PermissionError('synthetic locked artifact')))
        result,_=await container.story_lifecycle.execute('purge',branch,{'generation_id':deleted['generation_id']},'purge')
        assert result['cleanup_pending'] is True
        revision=result['branch_revision']
        assert (await container.sessions.load_state(branch)) is None
        monkeypatch.setattr(repo,'cleanup_purges',cleanup)
        replay,_=await container.story_lifecycle.execute('purge',branch,{'generation_id':deleted['generation_id']},'purge')
        assert replay['cleanup_pending'] is False and replay['branch_revision']==revision
    finally: await container.aclose()

@pytest.mark.asyncio
async def test_preset_reload_failure_keeps_sql_head_and_replay_repairs_no_second_commit(tmp_path,monkeypatch):
    from mrp.tests.conftest import make_test_container
    from mrp.tests.test_phase3_acceptance import seed_character
    container=make_test_container(tmp_path)
    try:
        runner=await container.create_session('Synthetic',[await seed_character(container)],'',[],register=False)
        await container.persist_session(runner); await container._register_runner(runner)
        branch=runner.state.meta.id; before=runner.state.meta.branch_revision
        read=container.sessions.load_state_readonly
        async def failed(identity):
            if container.sessions.story_db.revision(identity)>before: raise OSError('synthetic runtime reload failure')
            return await read(identity)
        monkeypatch.setattr(container.sessions,'load_state_readonly',failed)
        result,_=await container.story_lifecycle.execute('preset',branch,{'preset_id':None},'preset')
        assert result['cleanup_pending'] is True
        assert container.sessions.story_db.revision(branch)==before+1
        monkeypatch.setattr(container.sessions,'load_state_readonly',read)
        replay,_=await container.story_lifecycle.execute('preset',branch,{'preset_id':None},'preset')
        assert replay['cleanup_pending'] is False and replay['branch_revision']==before+1
        assert runner.state.meta.branch_revision==before+1
    finally: await container.aclose()

@pytest.mark.asyncio
async def test_delivery_status_write_failure_returns_committed_result(tmp_path,monkeypatch):
    from mrp.tests.conftest import make_test_container
    from mrp.tests.test_phase3_acceptance import seed_character
    container=make_test_container(tmp_path)
    try:
        runner=await container.create_session('Synthetic',[await seed_character(container)],'',[],register=False)
        await container.persist_session(runner); branch=runner.state.meta.id
        repo=container.story_lifecycle.repo; report=repo.report_delivery
        monkeypatch.setattr(repo,'report_delivery',lambda *args: (_ for _ in ()).throw(OSError('synthetic delivery journal status failure')))
        result,_=await container.story_lifecycle.execute('delete',branch,{},'delete')
        assert result['cleanup_pending'] is True and await container.sessions.load_state(branch) is None
        revision=result['branch_revision']
        monkeypatch.setattr(repo,'report_delivery',report)
        replay,_=await container.story_lifecycle.execute('delete',branch,{},'delete')
        assert replay['branch_revision']==revision and replay['cleanup_pending'] is False
    finally: await container.aclose()

@pytest.mark.asyncio
async def test_locked_purge_artifact_does_not_block_restarted_container(tmp_path,monkeypatch):
    from mrp.tests.conftest import make_test_container
    from mrp.tests.test_phase3_acceptance import seed_character
    from mrp.storage.lifecycle_repo import LifecycleRepo
    container=make_test_container(tmp_path)
    try:
        runner=await container.create_session('Synthetic',[await seed_character(container)],'',[],register=False)
        await container.persist_session(runner); branch=runner.state.meta.id
        deleted,_=await container.story_lifecycle.execute('delete',branch,{},'delete')
        cleanup=LifecycleRepo.cleanup_purges
        monkeypatch.setattr(LifecycleRepo,'cleanup_purges',lambda *args: (_ for _ in ()).throw(PermissionError('synthetic locked artifact')))
        result,_=await container.story_lifecycle.execute('purge',branch,{'generation_id':deleted['generation_id']},'purge')
        assert result['cleanup_pending']
    finally: await container.aclose()
    restarted=make_test_container(tmp_path)
    try:
        assert await restarted.sessions.load_state(branch) is None
        assert 'lifecycle-recovery' in restarted.story_backups.errors
        monkeypatch.setattr(LifecycleRepo,'cleanup_purges',cleanup)
        replay,_=await restarted.story_lifecycle.execute('purge',branch,{'generation_id':deleted['generation_id']},'purge')
        assert replay['cleanup_pending'] is False
    finally: await restarted.aclose()
