import pytest
from types import SimpleNamespace
from mrp.tests.test_command_receipts import value
from mrp.storage.paths import AppPaths
from mrp.storage.session_repo import SessionRepo
from mrp.shared.models import Message, TurnRun, ConversationRun, ConversationStep
from mrp.shared.run_windows import select_page_runs

@pytest.mark.asyncio
async def test_old_page_keeps_related_attempt_and_latest_recoverable_with_bounded_trace(tmp_path):
    repo=SessionRepo(AppPaths(tmp_path),sqlite_new_stories=True)
    state=value(); branch=state.meta.id
    state.messages=[]
    for index in range(40):
        message=Message(id=f'msg-{index}',session_id=branch,seq=index,turn=index,actor='player',content='synthetic',operation_id=f'op-{index}',status='final',input_group_id='group-old' if index<3 else None)
        state.messages.append(message)
        state.turn_runs.append(TurnRun(id=f'turn-{index}',session_id=branch,operation_id=f'op-{index}',request_fingerprint='fp',turn=index,status='completed',input_message_ids=[message.id]))
        state.conversation_runs.append(ConversationRun(id=f'convo-{index}',session_id=branch,operation_id=f'op-{index}',turn=index,status='completed',scene_id='scene',seed_message_ids=[message.id]))
    latest=ConversationRun(id='latest-pause',session_id=branch,operation_id='resume-latest',turn=41,status='paused',scene_id='scene',current_generation_id='gen-current',current_attempt_id='attempt-current',current_message_id='pending-current')
    latest.steps=[ConversationStep(index=i+1,generation_id=f'gen-{i}',speaker_id='actor',status='committed',message_id=f'not-in-page-{i}') for i in range(1500)]
    latest.scheduling_trace=[{'body':'private historical trace'*50} for _ in range(1500)]
    state.conversation_runs.append(latest)
    await repo.save_state(state,0)
    page,turn,seq,before=repo.story_db.read_window(branch,limit=2,around='msg-1')
    assert [m.id for m in page.messages]==['msg-0','msg-1','msg-2']
    assert {r.id for r in page.turn_runs}=={'turn-0','turn-1','turn-2'}
    assert {r.id for r in page.conversation_runs}=={'convo-0','convo-1','convo-2','latest-pause'}
    resume=next(r for r in page.conversation_runs if r.id=='latest-pause')
    assert resume.current_generation_id=='gen-current' and resume.current_attempt_id=='attempt-current'
    assert resume.steps==[] and resume.scheduling_trace==[]
    assert {r.id for r in select_page_runs(state.conversation_runs,page.messages)}=={r.id for r in page.conversation_runs}
    # Reopen ensures the derived page associations survived process boundaries.
    reopened=SessionRepo(AppPaths(tmp_path),sqlite_new_stories=True)
    assert {r.id for r in reopened.story_db.read_window(branch,limit=2,around='msg-1')[0].conversation_runs}=={r.id for r in page.conversation_runs}

@pytest.mark.asyncio
async def test_cached_story_view_uses_same_page_rule_without_full_run_projection(tmp_path):
    from mrp.tests.conftest import make_test_container
    from mrp.server.routers.story_views import story_view
    container=make_test_container(tmp_path)
    try:
        state=value(); state.messages=[]
        for index in range(60):
            state.messages.append(Message(id=f'page-msg-{index}',session_id=state.meta.id,seq=index,turn=index,actor='player',content='synthetic',operation_id=f'page-op-{index}'))
            state.turn_runs.append(TurnRun(id=f'page-run-{index}',session_id=state.meta.id,operation_id=f'page-op-{index}',request_fingerprint='fp',status='completed',input_message_ids=[f'page-msg-{index}']))
        await container.sessions.save_state(state,0)
        container.runners[state.meta.id]=SimpleNamespace(public_head=state,runtime=SimpleNamespace(pending_messages={}))
        result=await story_view(state.meta.id,limit=4,before_seq=None,around=None,container=container)
        assert len(result['messages'])==len(result['turn_runs'])==4
        container.runners.clear()
        cold=await story_view(state.meta.id,limit=4,before_seq=None,around=None,container=container)
        assert result['turn_runs']==cold['turn_runs']
    finally: await container.aclose()
