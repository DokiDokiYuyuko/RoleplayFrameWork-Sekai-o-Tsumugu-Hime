"""Application use cases run with explicit fake capabilities and no container."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
import asyncio
import pytest
from mrp.application.branch_commit import BranchCommit
from mrp.application.story_generation import StoryGeneration
from mrp.shared.models import SessionState, SessionMeta, Message
from mrp.shared.story_errors import RegenerationConflict

class CommitPort:
    def __init__(self):
        self.receipts = {}
        self.fail = False
        self.writes = 0
        self.events = []
    async def require_visible(self, branch):
        pass
    def branch_gate(self, branch):
        return nullcontext()
    async def lookup_command(self, branch, operation):
        return self.receipts.get(operation)
    async def lookup_generation(self, branch, operation):
        return None
    async def lookup_memory_job(self, branch, operation):
        return None
    async def reserve_generation(self, branch, operation, fingerprint):
        raise AssertionError('settings must not reserve a model')
    async def load_committed(self, branch):
        return self.saved.model_copy(deep=True)
    async def atomic_memory(self, runner):
        return True
    async def persist(self, staged):
        if self.fail:
            raise OSError('synthetic SQL failure')
        self.writes += 1
        staged.state.meta.branch_revision += 1
        self.saved = staged.state.model_copy(deep=True)
        receipt = deepcopy(staged.command_receipt)
        receipt['revision'] = self.saved.meta.branch_revision
        self.receipts[receipt['operation_id']] = receipt
        self.events.extend(staged.commit_events)
    async def uses_outbox(self, branch):
        return True
    async def dispatch_outbox(self, runner, revision):
        pass

@pytest.mark.asyncio
async def test_branch_commit_uses_only_ports_for_rollback_and_busy_stale_replay():
    state = SessionState(schema_version=3, meta=SessionMeta(id='synthetic-ports',title='original'))
    runtime = SimpleNamespace(turn_lock=nullcontext(), active_turn_run_id=None,
        active_conversation_run_id=None, turn_checkpoint_active=False)
    runner = SimpleNamespace(state=state, runtime=runtime, lorebooks=[],
        _committed_state=state.model_copy(deep=True), busy=lambda: bool(runtime.active_turn_run_id))
    port = CommitPort()
    app = BranchCommit(port)
    calls = 0
    async def change():
        nonlocal calls
        calls += 1
        runner.state.meta.title = 'changed'
        return {'title':'changed'}
    port.fail = True
    with pytest.raises(OSError):
        await app.execute(runner,'settings',{'title':'changed'},change,operation_id='intent',expected_revision=0)
    assert runner.state.meta.title == 'original' and not port.receipts
    port.fail = False
    first = await app.execute(runner,'settings',{'title':'changed'},change,operation_id='intent',expected_revision=0)
    runtime.active_turn_run_id = 'later-model'
    replay = await app.execute(runner,'settings',{'title':'changed'},change,operation_id='intent',expected_revision=0)
    assert replay == first and port.writes == 1 and calls == 2

class GenerationPort:
    def __init__(self):
        self.referenced = False
        self.calls = 0
        self.installed = []
    async def referenced_by_child(self, branch, messages):
        return self.referenced
    async def regenerate(self, runner, action, message, request, *, protect, variant_index, baseline_transform):
        await protect({message})
        self.calls += 1
        return {'generated':True}
    async def generate_group(self, runner, group, operation):
        self.calls += 1
        return Message(session_id=runner.state.meta.id, seq=1, actor='synthetic-group', content='synthetic draft', turn=1)
    def install_message(self, runner, draft):
        self.installed.append(draft)
        runner.state.messages.append(draft)

@pytest.mark.asyncio
async def test_generation_application_keeps_child_guard_and_staged_install_without_engine_access():
    port = GenerationPort()
    app = StoryGeneration(port)
    state = SessionState(meta=SessionMeta(id='synthetic-generation', title='synthetic'))
    emitted = []
    async def emit(event,payload):
        emitted.append((event,payload))
    runner = SimpleNamespace(state=state,_emit=emit)
    port.referenced = True
    with pytest.raises(RegenerationConflict):
        await app.regenerate(runner,'single','source',SimpleNamespace())
    assert port.calls == 0
    port.referenced = False
    assert await app.regenerate(runner,'single','source',SimpleNamespace()) == {'generated':True}
    result = await app.group_reply(runner,SimpleNamespace(), 'operation')
    assert result['id'] == port.installed[0].id
    assert emitted[0][0] == 'message.final' and len(state.messages) == 1
