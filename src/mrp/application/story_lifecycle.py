"""Story-level coordination around repository lifecycle transactions."""
import asyncio
from contextlib import asynccontextmanager
from .branch_runtime import StoryGateRegistry
from .lifecycle_ports import LifecyclePorts


class StoryLifecycle:
    def __init__(self, ports: LifecyclePorts):
        self.ports = ports
        self.repo = ports.repo
        self.gates = StoryGateRegistry()
        self.repo.recover_cutovers()
        self.repo.recover_operations()
        try:
            self.cleanup_purges()
        except OSError as error:
            self._diagnostic('lifecycle-recovery',error)


    @asynccontextmanager
    async def story_gate(self, story_id):
        async with self.gates.gate(story_id):
            await asyncio.to_thread(self.repo.require_unlocked, story_id)
            yield

    @asynccontextmanager
    async def branch_gate(self, branch_id):
        story_id = await asyncio.to_thread(self.repo.story_for, branch_id)
        async with self.story_gate(story_id):
            yield

    async def ensure_transactional_branch(self, branch_id):
        await asyncio.to_thread(self.ports.require_visible,branch_id)
        if await asyncio.to_thread(self.ports.is_transactional,branch_id):
            return await self.ports.read_state(branch_id)
        story_id = await asyncio.to_thread(self.repo.story_for, branch_id)
        async with self.story_gate(story_id):
            await asyncio.to_thread(self.repo.normalize_story, story_id)
        return await self.ports.read_state(branch_id)

    def reject_busy(self,story_id):
        return self.ports.reject_busy(story_id)

    async def execute(self, action, story_id, payload, operation_id=None):
        from mrp.shared.models import new_id
        operation_id = operation_id or new_id('lifecycle')
        replay = await asyncio.to_thread(self.repo.reserve,operation_id,action,story_id,payload)
        if replay is not None:
            return await self.deliver(operation_id,action,story_id,replay),operation_id
        async def complete():
            self.reject_busy(story_id)
            async with self.story_gate(story_id):
                self.reject_busy(story_id)
                if action in ('delete','branch_delete','preset'):
                    await asyncio.to_thread(self.repo.normalize_story,story_id)
                    # Validate request membership before making a backup.
                    if payload.get('membership_revision') is not None and payload['membership_revision'] != await asyncio.to_thread(self.repo.membership,story_id):
                        raise self.repo.conflict('故事成员或修订已变化')
                    entry = await self.ports.snapshot(story_id,reason='before_' + action)
                    preset = None
                    if action == 'preset' and payload.get('preset_id'):
                        preset = self.ports.preset(payload['preset_id'])
                        if preset is None: raise self.repo.conflict('提示词方案不存在','not_found',404)
                    result = await asyncio.to_thread(self.repo.commit,operation_id,action,story_id,
                        branch_id=payload.get('branch_id'),membership=payload.get('membership_revision'),entry=entry,
                        preset_id=payload.get('preset_id'),preset=preset)
                else:
                    await self.ensure_legacy_trash(story_id)
                    entry = await asyncio.to_thread(self.repo.trash,story_id,payload.get('generation_id'))
                    result = await asyncio.to_thread(self.repo.commit,operation_id,action,story_id,generation=entry['generation_id'])
                return await self.deliver(operation_id,action,story_id,result)
        task = asyncio.create_task(complete())
        interrupted = False
        while not task.done():
            try: await asyncio.shield(task)
            except asyncio.CancelledError: interrupted = True
        try:
            result = task.result()
        except BaseException as exc:
            await asyncio.to_thread(self.repo.reject_operation,operation_id,exc)
            raise
        if interrupted: raise asyncio.CancelledError
        return result,operation_id

    def cleanup_purges(self):
        self.ports.cleanup_purges()

    async def ensure_legacy_trash(self, story_id):
        if story_id in await asyncio.to_thread(self.repo.known_trash_stories): return
        await self.ports.adopt_legacy_trash(story_id)

    def _diagnostic(self,story,error):
        try: self.ports.diagnostic(story,error)
        except Exception: pass  # Diagnostics cannot reverse a durable commit.

    async def deliver(self,operation,action,story,result):
        error=None
        try:
            await self.ports.post_commit(action,story,result)
            if action=='purge': await asyncio.to_thread(self.cleanup_purges)
        except Exception as failed:
            error=failed
            self._diagnostic(story,failed)
        try:
            return await asyncio.to_thread(self.repo.report_delivery,operation,error is not None,error)
        except Exception as failed:
            # The authoritative command already committed. Keep its original
            # journal and report deferred delivery without a false failure.
            self._diagnostic(story,failed)
            return {**result,'cleanup_pending':True}
