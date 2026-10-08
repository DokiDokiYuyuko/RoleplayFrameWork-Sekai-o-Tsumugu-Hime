"""Staged story commands: committed reads and events advance after persistence."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
import logging
import hashlib
import json
from types import SimpleNamespace

from mrp.shared.state_restore import restore_state_in_place
from mrp.shared.models import new_id
from mrp.application.ports import BranchCommitPort, BranchRunner

logger = logging.getLogger(__name__)
_active_commit = ContextVar("active_branch_commit", default=None)
COMMITTED_EVENTS = {"message.final", "message.updated", "message.deleted", "scene.switched",
                    "session.roster.changed", "session.player.changed", "director.pending", "director.decided",
                    "memory.consolidated", "memory.updated", "memory.deleted", "memory.restored"}


class BranchCommitConflict(ValueError):
    def __init__(self, message, code="branch_revision_conflict", status_code=409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class BranchCommitFailure(RuntimeError):
    pass


def committed_head(runner):
    return getattr(runner, "_branch_committed_state",
        getattr(runner, "_turn_public_state", getattr(runner, "_committed_state", runner.state)))


def buffer_committed_event(runner, event, payload, lossy=False):
    transaction = _active_commit.get()
    if transaction is None or transaction.runner is not runner or event not in COMMITTED_EVENTS:
        return False
    transaction.events.append((event, deepcopy(payload), lossy))
    return True


def stage_memory_invalidation(runner, message_ids):
    transaction = _active_commit.get()
    if transaction is None or transaction.runner is not runner or not transaction.atomic_memory:
        return False
    transaction.memory_actions.append({"kind": "invalidate_sources", "message_ids": sorted(message_ids)})
    return True


def stage_memory_actions(runner, actions):
    transaction = _active_commit.get()
    if transaction is None or transaction.runner is not runner or not transaction.atomic_memory:
        raise BranchCommitConflict("该路线需要先迁移到事务存储", "storage_migration_required")
    transaction.memory_actions.extend(deepcopy(actions))


class BranchCommit:
    def __init__(self, port: BranchCommitPort):
        self.port = port

    async def execute(self, runner: BranchRunner, action, payload, mutate, *, operation_id=None,
                      expected_revision=None, memory=False, response_meta=None, generation=False):
        if operation_id:
            try:
                await self.port.require_visible(runner.state.meta.id)
            except ValueError as exc:
                if getattr(exc, 'command_conflict', False):
                    raise BranchCommitConflict(str(exc), exc.code, exc.status_code) from exc
                raise
            receipt = await self.port.lookup_command(runner.state.meta.id, operation_id)
            if receipt is not None and (runner.busy() or receipt['revision'] <= runner.state.meta.branch_revision):
                fingerprint = hashlib.sha256(json.dumps({'action':action,'payload':payload}, ensure_ascii=False,
                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                if receipt['fingerprint'] != fingerprint:
                    raise BranchCommitConflict('操作 ID 已用于不同的请求', 'operation_conflict')
                if response_meta is not None:
                    response_meta.update(operation_id=operation_id, branch_revision=receipt['revision'])
                return deepcopy(receipt['result'])
        async with self.port.branch_gate(runner.state.meta.id):
            return await self._execute(runner, action, payload, mutate, operation_id=operation_id,
                expected_revision=expected_revision, memory=memory, response_meta=response_meta, generation=generation)

    async def _execute(self, runner, action, payload, mutate, *, operation_id=None,
                       expected_revision=None, memory=False, response_meta=None, generation=False):
        """Replay durable command results before checking stale CAS or live state."""
        fingerprint = hashlib.sha256(json.dumps({"action": action, "payload": payload},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if operation_id:
            receipt = await self.port.lookup_command(runner.state.meta.id, operation_id)
            if receipt is not None:
                if receipt["fingerprint"] != fingerprint:
                    raise BranchCommitConflict("操作 ID 已用于不同的请求", "operation_conflict")
                # Replaying an immutable receipt cannot mutate a live generation.
                # Active run ownership must not block confirmation of an old command.
                if runner.busy() or receipt["revision"] <= runner.state.meta.branch_revision:
                    if response_meta is not None:
                        response_meta.update(operation_id=operation_id, branch_revision=receipt["revision"])
                    return deepcopy(receipt["result"])
        async with self._command_lock(runner):
            if operation_id:
                receipt = await self.port.lookup_command(runner.state.meta.id, operation_id)
                if receipt is not None:
                    if receipt["fingerprint"] != fingerprint:
                        raise BranchCommitConflict("操作 ID 已用于不同的请求", "operation_conflict")
                    if receipt["revision"] > runner.state.meta.branch_revision:
                        await self._reload_committed(runner)
                    if response_meta is not None:
                        response_meta.update(operation_id=operation_id, branch_revision=receipt["revision"])
                    return deepcopy(receipt["result"])
                if (operation_id in runner.state.generation_operations or
                        any(run.operation_id == operation_id for run in runner.state.turn_runs) or
                        any(run.operation_id == operation_id for run in runner.state.conversation_runs)):
                    raise BranchCommitConflict("操作 ID 已用于生成请求", "operation_conflict")
                reservation = runner.state.generation_operations.get("__mrp_pending_commands_v1__", {}).get(operation_id)
                if reservation is not None and reservation["fingerprint"] != fingerprint:
                    raise BranchCommitConflict("操作 ID 已用于进行中的命令", "operation_conflict")
                claim = await self.port.lookup_generation(runner.state.meta.id, operation_id)
                if claim is not None:
                    if claim != fingerprint:
                        raise BranchCommitConflict("操作 ID 已用于不同的生成尝试", "operation_conflict")
                    raise BranchCommitConflict("生成尝试已经启动；完整结果未确认，不能自动重复模型调用", "operation_incomplete")
                job = await self.port.lookup_memory_job(runner.state.meta.id, operation_id)
                if job is not None and (action != 'memory.consolidate' or job['fingerprint'] != fingerprint):
                    raise BranchCommitConflict('操作 ID 已用于记忆整理任务', 'operation_conflict')
            if (runner.runtime.active_conversation_run_id or runner.runtime.active_turn_run_id
                    or runner.runtime.turn_checkpoint_active):
                raise BranchCommitConflict("回合进行中，稍后再试", "branch_busy")
            if expected_revision is not None and runner.state.meta.branch_revision != expected_revision:
                raise BranchCommitConflict("路线内容已变化，请重新载入后再操作")
            if generation and operation_id:
                try:
                    await self.port.reserve_generation(runner.state.meta.id, operation_id, fingerprint)
                except ValueError as exc:
                    if getattr(exc, "command_conflict", False):
                        raise BranchCommitConflict(str(exc), getattr(exc, "code", "operation_conflict")) from exc
                    raise
            receipt = {"operation_id": operation_id, "fingerprint": fingerprint} if operation_id else None
            try:
                async with self.edit(runner, expected_revision=expected_revision, memory=memory,
                                     operation_id=operation_id, command_receipt=receipt):
                    result = deepcopy(await mutate())
                    transaction = _active_commit.get()
                    changes = runner.state != runner._branch_committed_state or transaction.events or transaction.memory_actions
                    revision = runner.state.meta.branch_revision + int(bool(changes or receipt) and runner.state.schema_version >= 3)
                    if isinstance(result, dict) and "branch_revision" in result:
                        result["branch_revision"] = revision
                    if (isinstance(result, dict) and isinstance(result.get("state"), dict)
                            and isinstance(result["state"].get("meta"), dict)):
                        result["state"]["meta"]["branch_revision"] = revision
                    if receipt is not None:
                        receipt["result"] = result
                        if response_meta is not None:
                            response_meta.update(operation_id=operation_id, branch_revision=revision)
                if receipt is not None and response_meta is not None:
                    response_meta["branch_revision"] = runner.state.meta.branch_revision
                if receipt is not None:
                    result = deepcopy(receipt["result"])
            except ValueError as exc:
                committed = getattr(exc, "receipt", None)
                if committed is not None and committed["fingerprint"] == fingerprint:
                    if response_meta is not None:
                        response_meta.update(operation_id=operation_id, branch_revision=committed["revision"])
                    await self._reload_committed(runner)
                    return deepcopy(committed["result"])
                if getattr(exc, "command_conflict", False):
                    raise BranchCommitConflict(str(exc), getattr(exc, "code", "operation_conflict")) from exc
                raise
            return result

    @asynccontextmanager
    async def _command_lock(self, runner):
        from mrp.shared.story_errors import TurnRunConflict, ConversationConflict
        try:
            async with runner.runtime.turn_lock:
                yield
        except (TurnRunConflict, ConversationConflict) as exc:
            raise BranchCommitConflict(str(exc), "branch_busy") from exc

    async def _reload_committed(self, runner):
        authoritative = await self.port.load_committed(runner.state.meta.id)
        if authoritative is not None:
            restore_state_in_place(runner.state, authoritative)
            runner._committed_state = authoritative.model_copy(deep=True)
            runner.lorebooks = runner.state.lorebooks

    @asynccontextmanager
    async def edit(self, runner: BranchRunner, *, expected_revision=None, operation_id=None, memory=False, command_receipt=None):
        async with self.port.branch_gate(runner.state.meta.id):
            async with self._edit(runner, expected_revision=expected_revision, operation_id=operation_id,
                                  memory=memory, command_receipt=command_receipt) as staged:
                yield staged

    @asynccontextmanager
    async def _edit(self, runner, *, expected_revision=None, operation_id=None, memory=False, command_receipt=None):
        """Compatibility methods may mutate the staged runner under this owned lock."""
        async with runner.runtime.turn_lock:
            if expected_revision is not None and runner.state.meta.branch_revision != expected_revision:
                raise BranchCommitConflict("路线内容已变化，请重新载入后再操作")
            original = runner.state
            before = original  # The original model stays untouched until commit succeeds.
            staged = original.model_copy(deep=True)
            books = runner.lorebooks
            atomic_memory = await self.port.atomic_memory(runner)
            transaction = SimpleNamespace(runner=runner, events=[], atomic_memory=atomic_memory, memory_actions=[])
            operation_id = operation_id or new_id("commit")
            prepared = False
            durable = False
            interrupted = False
            runner._branch_committed_state = getattr(runner, "_committed_state", before)
            runner.state = staged
            token = _active_commit.set(transaction)
            try:
                if memory and not atomic_memory:
                    prepared = await self.port.prepare_legacy_memory(runner, operation_id)
                yield staged
                if staged == before and not transaction.events and not transaction.memory_actions and command_receipt is None:
                    runner.state = original
                    runner.lorebooks = books
                    if prepared:
                        await self.port.finish_legacy_memory(runner, operation_id)
                    return
                if prepared:
                    staged.generation_operations[operation_id] = {"kind": "branch_commit", "response": {}}
                saving = asyncio.create_task(self.port.persist(SimpleNamespace(state=staged,
                    expected_revision=before.meta.branch_revision, commit_operation_id=operation_id,
                    commit_events=transaction.events, memory_actions=transaction.memory_actions,
                    command_receipt=command_receipt)))
                # to_thread SQL cannot be cancelled safely once a commit starts.
                # Settle the durability outcome before releasing the branch lock.
                while not saving.done():
                    try:
                        await asyncio.shield(saving)
                    except asyncio.CancelledError:
                        interrupted = True
                saving.result()
                durable = True
                restore_state_in_place(original, staged)
                runner.state = original
                runner._committed_state = original.model_copy(deep=True)
                if hasattr(runner, "_turn_durable_state") and not runner.runtime.active_turn_run_id:
                    runner._turn_durable_state = runner._committed_state
            except BaseException as exc:
                # Paid usage remains independent of whether story content commits.
                known = {row.id for row in original.usage_records}
                original.usage_records.extend(row.model_copy(deep=True) for row in staged.usage_records if row.id not in known)
                original.usage_incomplete |= staged.usage_incomplete
                runner.state = staged if durable else original
                runner.lorebooks = books
                if durable:
                    runner._committed_state = staged.model_copy(deep=True)
                if prepared and not durable and getattr(exc, "receipt", None) is None:
                    await self.port.finish_legacy_memory(runner, operation_id, rollback=True)
                raise
            finally:
                _active_commit.reset(token)
                runner.__dict__.pop("_branch_committed_state", None)
            if transaction.memory_actions:
                try:
                    actors = {actor for action in transaction.memory_actions for actor in action.get('touched_actors', [])}
                    await self.port.refresh_mirrors(original.meta.id, actors)
                except Exception:
                    logger.warning("committed memory mirror refresh deferred", exc_info=True)
            if prepared:
                try:
                    await self.port.finish_legacy_memory(runner, operation_id)
                except Exception:
                    logger.warning("committed memory journal cleanup deferred", exc_info=True)
            if await self.port.uses_outbox(original.meta.id):
                try:
                    await self.port.dispatch_outbox(runner, original.meta.branch_revision)
                except Exception:
                    logger.warning("committed story notification deferred to outbox pump", exc_info=True)
                if interrupted:
                    raise asyncio.CancelledError
                return
            for event, payload, lossy in transaction.events:
                payload.setdefault("branch_revision", original.meta.branch_revision)
                try:
                    await runner._emit(event, payload, lossy=lossy)
                except Exception:
                    logger.warning("committed story notification deferred", exc_info=True)
            if interrupted:
                raise asyncio.CancelledError
