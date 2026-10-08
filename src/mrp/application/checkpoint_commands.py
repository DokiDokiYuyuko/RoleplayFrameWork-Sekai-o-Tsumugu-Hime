"""A director decision spans existing durable turn checkpoints, not one edit.

Persist the decision identity before executing it. A retry of a terminal turn
only captures its saved result; interrupted work still requires explicit resume.
"""
import asyncio
from copy import deepcopy
import hashlib
import json

from mrp.application.branch_commit import BranchCommitConflict, BranchCommit
from mrp.application.ports import CommandRepositoryPort, DirectorCheckpointPort, BranchRunner
from weakref import WeakValueDictionary
from mrp.contracts.story import project_message, project_turn_run

PENDING_KEY = "__mrp_pending_commands_v1__"


class CheckpointCommands:
    def __init__(self, repository: CommandRepositoryPort, checkpoints: DirectorCheckpointPort, commits: BranchCommit):
        self.repository, self.checkpoints, self.commits = repository, checkpoints, commits
        self._locks = WeakValueDictionary()

    async def resolve_director(self, runner: BranchRunner, accept, *, operation_id=None, response_meta=None):
        if not operation_id:
            return await self.checkpoints.resolve(runner, accept)
        action, payload = "director.resolve", {"accept": accept}
        fingerprint = hashlib.sha256(json.dumps({"action": action, "payload": payload},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        async with self._locks.setdefault(runner.state.meta.id, asyncio.Lock()):
            receipt = await self.repository.lookup_command(runner.state.meta.id, operation_id)
            if receipt is not None:
                if receipt["fingerprint"] != fingerprint:
                    raise BranchCommitConflict("操作 ID 已用于不同的请求", "operation_conflict")
                if response_meta is not None:
                    response_meta.update(operation_id=operation_id, branch_revision=receipt["revision"])
                return deepcopy(receipt["result"])
            if (await self.repository.lookup_generation(runner.state.meta.id, operation_id) is not None or
                await self.repository.lookup_memory_job(runner.state.meta.id, operation_id) is not None):
                raise BranchCommitConflict("操作 ID 已用于另一生成尝试", "operation_conflict")
            reservation = runner.state.generation_operations.get(PENDING_KEY, {}).get(operation_id)
            new_reservation = reservation is None
            if reservation is not None and reservation["fingerprint"] != fingerprint:
                raise BranchCommitConflict("操作 ID 已用于不同的请求", "operation_conflict")
            if reservation is None and (operation_id in runner.state.generation_operations or
                    any(run.operation_id == operation_id for run in runner.state.turn_runs) or
                    any(run.operation_id == operation_id for run in runner.state.conversation_runs)):
                raise BranchCommitConflict("操作 ID 已用于生成请求", "operation_conflict")
            if runner.busy():
                raise BranchCommitConflict("回合进行中，稍后再试", "branch_busy")
            if reservation is None:
                pending = self.checkpoints.pending(runner)
                if pending is None:
                    return None
                run = next((run for run in reversed(runner.state.turn_runs)
                    if run.session_id == runner.state.meta.id and run.turn == pending.turn
                    and run.status in {"awaiting_director", "failed"}), None)
                if run is None:
                    # Pre-checkpoint stories use a single staged command boundary.
                    async def mutate():
                        messages = await self.checkpoints.resolve_legacy(runner, accept)
                        return {"messages": [project_message(message) for message in messages or []]}
                    return await self.commits.execute(runner, action, payload, mutate,
                        operation_id=operation_id, memory=True, response_meta=response_meta, generation=True)
                async with self.commits.edit(runner):
                    runner.state.generation_operations.setdefault(PENDING_KEY, {})[operation_id] = {
                        "fingerprint": fingerprint, "run_id": run.operation_id,
                        "decision": pending.model_dump(mode="json")}
                reservation = runner.state.generation_operations[PENDING_KEY][operation_id]
            run = self.checkpoints.find(runner, reservation["run_id"])
            same_decision = (self.checkpoints.pending(runner) is not None and
                self.checkpoints.pending(runner).model_dump(mode="json") == reservation["decision"])
            if run.status == "awaiting_director" and not same_decision:
                raise BranchCommitConflict("导演提案已改变，请刷新后选择当前提案")
            if same_decision and (run.status == "awaiting_director" or
                                  (new_reservation and run.status == "failed")):
                result = await self.checkpoints.resolve(runner, accept)
            else:
                # Never turn a lost HTTP response into another model attempt.
                result = self.checkpoints.response(runner, run)
            result = {**result, "messages": [project_message(message) for message in result.get("messages", [])]}
            if "turn_run" in result:
                result["turn_run"] = project_turn_run(result["turn_run"])
            async def capture_terminal():
                runner.state.generation_operations.get(PENDING_KEY, {}).pop(operation_id, None)
                return result
            return await self.commits.execute(runner, action, payload, capture_terminal,
                operation_id=operation_id, response_meta=response_meta)
