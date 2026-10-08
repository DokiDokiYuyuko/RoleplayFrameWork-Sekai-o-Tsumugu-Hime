"""Infrastructure composition for narrow story application capabilities."""
from __future__ import annotations
import asyncio
from contextlib import contextmanager, nullcontext
from typing import TYPE_CHECKING
from mrp.application.ports import BranchRunner, StagedCommit, RegenerationInput
from mrp.shared.models import MemoryRecord, SessionState, Scene, GroupActor, Message
if TYPE_CHECKING:
    from mrp.server.container import AppContainer

class CommandRepositoryAdapter:
    def __init__(self, container: AppContainer):
        self._container = container
    async def lookup_command(self, branch, operation):
        return await self._container.sessions.lookup_command(branch, operation)
    async def lookup_generation(self, branch, operation):
        return await self._container.sessions.lookup_generation(branch, operation)
    async def lookup_memory_job(self, branch, operation):
        return await self._container.sessions.lookup_memory_job(branch, operation)
    async def reserve_generation(self, branch, operation, fingerprint):
        return await self._container.sessions.reserve_generation(branch, operation, fingerprint)

class BranchCommitAdapter(CommandRepositoryAdapter):
    async def require_visible(self, branch):
        method = getattr(self._container.sessions, 'require_visible_branch', None)
        if method:
            await asyncio.to_thread(method, branch)
    def branch_gate(self, branch):
        lifecycle = getattr(self._container, 'story_lifecycle', None)
        return lifecycle.branch_gate(branch) if lifecycle else nullcontext()
    async def load_committed(self, branch):
        return await self._container.sessions.load_state(branch)
    async def atomic_memory(self, runner: BranchRunner):
        return (runner.memory is self._container.memory_store and
            await asyncio.to_thread(self._container.sessions.story_db.active, runner.state.meta.id))
    async def prepare_legacy_memory(self, runner: BranchRunner, operation):
        if runner.memory is None or not hasattr(runner.memory, 'prepare_generation_operation'):
            return False
        await asyncio.to_thread(runner.memory.prepare_generation_operation, runner.state.meta.id, operation)
        return True
    async def finish_legacy_memory(self, runner: BranchRunner, operation, *, rollback=False):
        await asyncio.to_thread(runner.memory.finish_generation_operation, runner.state.meta.id, operation, rollback=rollback)
    async def persist(self, staged: StagedCommit):
        method = getattr(self._container, 'persist_owned_branch_commit', self._container.persist_session)
        await method(staged)
    async def refresh_mirrors(self, branch, actors):
        await asyncio.to_thread(self._container.memory_store.refresh_branch_mirrors, branch)
        for actor in actors:
            await asyncio.to_thread(self._container.memory_store.refresh_actor_mirror, actor)
    async def uses_outbox(self, branch):
        return await asyncio.to_thread(self._container.sessions.story_db.active, branch)
    async def dispatch_outbox(self, runner: BranchRunner, revision):
        await self._container.dispatch_story_outbox(runner=runner, revision=revision)

class MemoryRepositoryAdapter(CommandRepositoryAdapter):
    def consolidation_enabled(self):
        return self._container.settings.memory_consolidation_enabled
    def branch_gate(self, branch):
        return self._container.story_lifecycle.branch_gate(branch)
    async def ensure_transactional(self, branch):
        await self._container.story_lifecycle.ensure_transactional_branch(branch)
    async def prepare_records(self, records):
        return await asyncio.to_thread(self._container.memory_store.prepare_records, records)
    async def record_by_id(self, record):
        return await asyncio.to_thread(self._container.memory_store.record_by_id, record)
    async def branch_actor_ids(self, branch):
        return await asyncio.to_thread(self._container.memory_store.branch_actor_ids, branch)
    async def plan_next_window(self, actor, branch, turn, *, retry_failed):
        return await asyncio.to_thread(self._container.memory_store.plan_next_window, actor, branch, turn, retry_failed=retry_failed)
    async def window_rows(self, actor, branch):
        return await asyncio.to_thread(self._container.memory_store.window_rows, actor, branch)
    async def last_consolidated_turn(self, actor, branch):
        return await asyncio.to_thread(self._container.memory_store.last_consolidated_turn, actor, branch)
    async def claim_memory_job(self, branch, operation, fingerprint, plan):
        return await self._container.sessions.claim_memory_job(branch, operation, fingerprint, plan)
    async def current_watermark(self, branch):
        return await asyncio.to_thread(self._container.memory_store.current_watermark, branch)
    async def records_for(self, actor, branch):
        return await asyncio.to_thread(self._container.memory_store.records_for, actor, session_id=branch)

class BackgroundRuntimeAdapter:
    def __init__(self, container: AppContainer):
        self._registry = container.branch_runtimes
    @contextmanager
    def branch_lease(self, branch):
        with self._registry.request_scope():
            self._registry.pin(branch)
            yield
    def spawn(self, runner, work):
        return runner.runtime.spawn(work)

class MemoryGenerationAdapter:
    def __init__(self, container: AppContainer):
        self._container = container
    async def generate_window(self, runner, snapshot, actor, start, end):
        return await runner.memory_pipeline.generate_memory_records(snapshot, actor, start, end, self._container.summarizer)
    async def generate_scene(self, runner, snapshot, actor, scene):
        return await runner.memory_pipeline.generate_scene_record(snapshot, actor, scene)

class DirectorCheckpointAdapter:
    def __init__(self, container: AppContainer):
        self._container = container
    def pending(self, runner):
        return runner.pending_director
    async def resolve(self, runner, accept):
        return await self._container.turn_runs.resolve_director(runner, accept=accept)
    async def resolve_legacy(self, runner, accept):
        return await (runner.confirm_pending_director() if accept else runner.reject_pending_director())
    def find(self, runner, operation):
        return self._container.turn_runs.find(runner, operation)
    def response(self, runner, run):
        return self._container.turn_runs.response(runner, run)

class StoryGenerationAdapter:
    def __init__(self, container: AppContainer):
        self._container = container
    async def referenced_by_child(self, branch, messages):
        rows = await self._container.sessions.list_summaries()
        return any(row.parent_branch_id == branch and row.fork_message_id in messages for row in rows)
    async def regenerate(self, runner, action, message, request: RegenerationInput, *, protect, variant_index, baseline_transform):
        return await runner.regeneration.run(action, message, request, protect=protect,
            variant_index=variant_index, baseline_transform=baseline_transform)
    async def generate_group(self, runner, group: GroupActor, operation):
        return await runner.turns.run_group_turn(group, runner.state.current_turn(),
            idempotency_key=operation, defer_commit=True, emit_final=False)
    def install_message(self, runner, draft: Message):
        runner._append_message(draft)
