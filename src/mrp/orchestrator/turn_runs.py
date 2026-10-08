"""Durable ordinary turns. Only fully generated replies enter story state.

The context-local adapter leaves direct engine callers and finite conversations
unchanged. Ordinary HTTP sends save their input before planning, then checkpoint
each reply. A resumed turn reuses its route and parallel generation baseline.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import logging
from types import SimpleNamespace

from mrp.orchestrator.message_regeneration import restore_state_in_place
from mrp.shared.models import Message, SessionState, TurnRun, TurnSlot, new_id, utcnow

logger = logging.getLogger(__name__)
execution: ContextVar["Execution | None"] = ContextVar("ordinary_turn_execution", default=None)


from mrp.shared.story_errors import TurnRunConflict


class TurnCheckpointFailure(RuntimeError):
    pass


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def material_fingerprint(state):
    """Ignore operational counters; reject resuming after edits or identity changes."""
    from mrp.orchestrator.worldline_state import current_state_projection
    projection = current_state_projection(state)
    for identity in projection["player_identities"]:
        # Loading may capture legacy avatars; these fields do not affect prose.
        identity.pop("media_captured", None)
        identity.pop("avatar_ref", None)
    return _hash({
        "messages": [(m.id, m.fingerprint, m.active_variant, m.dependency_stale) for m in state.messages],
        "generation_state": projection,
    })


def durable_reply(method):
    """Reserve a stable reply slot, generate off-state, commit before publishing."""
    @wraps(method)
    async def call(engine, actor, turn, *args, **kwargs):
        live = execution.get()
        if live is None or live.runner is not engine.r or kwargs.get("target") is not None:
            if kwargs.get("defer_commit") and kwargs.get("target") is None:
                from mrp.orchestrator.reply_generation_adapter import generate_reply
                result = await generate_reply(method, engine, actor, turn, args, kwargs)
                message = result.message()
                if kwargs.get("emit_final", True):
                    await engine.r._emit("message.final", {"message": message.model_dump(mode="json")})
                return message
            return await method(engine, actor, turn, *args, **kwargs)
        return await live.reply(method, engine, actor, turn, args, kwargs)
    return call


class Execution:
    def __init__(self, service, runner, operation_id):
        self.service, self.runner, self.operation_id = service, runner, operation_id
        self.epoch = self.run.epoch
        self.checkpoint_lock = service.checkpoint_locks.setdefault(runner.state.meta.id, asyncio.Lock())

    @property
    def run(self):
        return self.service.find(self.runner, self.operation_id)

    def guard(self):
        if self.runner.runtime.closed or self.run.epoch != self.epoch or self.run.status != "running":
            raise TurnRunConflict("这一轮已中止，迟到的回应未写入故事")

    @staticmethod
    def merge_usage(target, source):
        known_usage = {row.id for row in target.usage_records}
        target.usage_records.extend(row.model_copy(deep=True) for row in source.usage_records if row.id not in known_usage)
        target.usage_incomplete = target.usage_incomplete or source.usage_incomplete

    async def checkpoint(self, before=None, *, staged=None):
        state = staged if staged is not None else self.runner.state
        run = next(item for item in state.turn_runs if item.operation_id == self.operation_id)
        run.updated_at = utcnow()
        run.basis_fingerprint = material_fingerprint(state)
        self.runner.runtime.turn_checkpoint_active = True
        self.runner._turn_public_state = getattr(self.runner, "_turn_durable_state", self.runner.state)
        try:
            await self.service.container.persist_session(self.runner if staged is None else SimpleNamespace(state=staged))
            if staged is not None:
                self.merge_usage(staged, self.runner.state)
                restore_state_in_place(self.runner.state, staged)
            self.runner._turn_durable_state = self.runner.state.model_copy(deep=True)
            self.runner._committed_state = self.runner._turn_durable_state
        except BaseException as exc:
            # save_state may have incremented revision or already replaced the
            # body before the index failed. Compensate to the last durable head.
            durable = before or getattr(self.runner, "_turn_durable_state", None)
            if durable is not None:
                durable = durable.model_copy(deep=True)
                self.merge_usage(durable, self.runner.state)
                restore_state_in_place(self.runner.state, durable)
                try:
                    await self.service.container.sessions.restore_state(self.runner.state)
                except Exception:
                    logger.exception("ordinary turn checkpoint compensation failed")
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise TurnCheckpointFailure("这一轮保存失败，已保留上次成功保存的内容") from exc
        finally:
            self.runner.runtime.turn_checkpoint_active = False
            self.runner.__dict__.pop("_turn_public_state", None)
        await self.runner._emit("turn.run.updated", {"run": self.run.model_dump(mode="json"),
            "branch_revision": self.runner.state.meta.branch_revision})

    async def accepted(self, messages):
        self.guard()
        self.run.turn = messages[0].turn if messages else self.runner.state.current_turn()
        self.run.input_message_ids = [m.id for m in messages]
        # Invalidate an older director proposal only once this input is durable.
        self.runner.pending_director = None
        for prior in self.runner.state.turn_runs:
            if prior.operation_id != self.operation_id and prior.status in {"awaiting_director", "failed", "interrupted"}:
                prior.status, prior.stop_reason = "cancelled", "new_player_input"
                prior.parallel_context = None
        async with self.checkpoint_lock:
            await self.checkpoint()
        for message in messages:
            await self.runner._emit("message.final", {"message": message.model_dump(mode="json")})

    async def prepare_plan(self, plan):
        self.guard()
        if self.run.plan is None:
            self.run.plan = plan
            async with self.checkpoint_lock:
                await self.checkpoint()

    async def padding_done(self):
        self.guard()
        self.run.padding_prepared = True
        async with self.checkpoint_lock:
            await self.checkpoint()

    async def parallel_baseline(self, snapshot):
        self.guard()
        if self.run.parallel_context is not None:
            snapshot = SessionState.model_validate(self.run.parallel_context)
        else:
            from mrp.orchestrator.worldline_state import apply_projection
            apply_projection(snapshot, self.runner.context_builder.generation_material_projection(state=snapshot))
            baseline = snapshot.model_dump(mode="json")
            # Avoid nesting this run's baseline inside itself, or carrying unrelated
            # completed execution records into every saved parallel turn.
            baseline["turn_runs"], baseline["conversation_runs"] = [], []
            self.run.parallel_context = baseline
            if self.runner.memory is not None and hasattr(self.runner.memory, "current_watermark"):
                self.run.parallel_memory_watermark = self.runner.memory.current_watermark(snapshot.meta.id)
            async with self.checkpoint_lock:
                await self.checkpoint()
        object.__setattr__(snapshot, "_generation_frozen", True)
        object.__setattr__(snapshot, "_generation_memory_watermark", self.run.parallel_memory_watermark)
        object.__setattr__(snapshot, "_generation_memory_cutoff", self.run.created_at)
        return snapshot

    async def reply(self, method, engine, actor, turn, args, kwargs):
        self.guard()
        async with self.checkpoint_lock:
            slot = next((s for s in self.run.slots if s.actor_id == actor.id), None)
            if slot is not None and slot.status == "committed":
                message = next((m for m in self.runner.state.messages if m.id == slot.message_id), None)
                if message is None:
                    raise TurnRunConflict("已提交回应不在当前路线，请刷新后继续")
                return message
            pending = kwargs.get("pending_override")
            if slot is None:
                reserved_seq = pending.seq if pending is not None else max(
                    [self.runner.state.next_seq(), *(s.seq + 1 for s in self.run.slots)])
                slot = TurnSlot(actor_id=actor.id, seq=reserved_seq,
                    message_id=pending.id if pending is not None else new_id("msg"))
                self.run.slots.append(slot)
            else:
                self.runner.runtime.retired_conversation_generations.add(slot.generation_id)
                slot.generation_id = new_id("gen")
            slot.status, slot.error = "pending", None
            await self.checkpoint()
            pending = Message(id=slot.message_id, session_id=self.runner.state.meta.id,
                seq=slot.seq, turn=turn, actor=actor.id, content="", status="pending",
                kind="roleplay", scene_id=self.runner.state.active_scene_id,
                operation_id=self.operation_id, generation_id=slot.generation_id)
        options = {**kwargs, "pending_override": pending, "defer_commit": True, "emit_final": False}
        # Parallel orchestration announced an ephemeral pending row before this
        # adapter reserved a durable generation. Announce the stable attempt.
        options["pending_emitted"] = False
        try:
            from mrp.orchestrator.reply_generation_adapter import generate_reply
            result = await generate_reply(method, engine, actor, turn, args, options)
            message = result.message()
            async with self.checkpoint_lock:
                self.guard()
                # Publish a new story head only after its atomic save succeeds.
                # GET snapshots during storage I/O retain the old durable prefix
                # plus the runtime pending projection, never an uncommitted final.
                from mrp.orchestrator.runtime import append_message
                from mrp.orchestrator.worldline_state import record_message_revision
                staged = self.runner.state.model_copy(deep=True)
                append_message(staged, message)
                staged.messages.sort(key=lambda m: m.seq)
                watermark = (self.runner.memory.current_watermark(staged.meta.id)
                    if self.runner.memory is not None and hasattr(self.runner.memory, "current_watermark") else None)
                record_message_revision(staged, message, watermark)
                staged_actor = next((group for group in staged.groups if group.id == actor.id), None)
                if staged_actor is not None:
                    staged_actor.last_spoke_turn = turn
                staged_run = next(item for item in staged.turn_runs if item.operation_id == self.operation_id)
                slot = next(s for s in staged_run.slots if s.actor_id == actor.id)
                slot.status = "committed"
                await self.checkpoint(staged=staged)
            await self.runner._emit("message.final", {"message": message.model_dump(mode="json")})
            return message
        except BaseException as exc:
            async with self.checkpoint_lock:
                if self.run.epoch == self.epoch and self.run.status == "running":
                    slot = next(s for s in self.run.slots if s.actor_id == actor.id)
                    slot.status, slot.error = "failed", str(exc)[:300]
                    await self.checkpoint()
            raise

    async def scene_prepared(self, ids):
        self.guard()
        self.run.prepared_scene_message_ids = ids
        async with self.checkpoint_lock:
            await self.checkpoint()

    async def followup(self, source_id, actor_id):
        self.guard()
        self.run.followups[source_id] = actor_id
        async with self.checkpoint_lock:
            await self.checkpoint()


class TurnRuns:
    def __init__(self, container):
        self.container = container
        self.checkpoint_locks = {}

    @staticmethod
    def find(runner, operation_id):
        run = next((r for r in runner.state.turn_runs if r.operation_id == operation_id), None)
        if run is None or run.session_id != runner.state.meta.id:
            raise TurnRunConflict("这一轮不属于当前世界线")
        return run

    def attach(self, runner):
        recovered = False
        for run in runner.state.turn_runs:
            if run.status == "running":
                recovered = True
                run.status, run.stop_reason = "interrupted", "service_restarted"
                run.epoch += 1
                for slot in run.slots:
                    if slot.status != "committed":
                        slot.status = "cancelled"
                        runner.runtime.retired_conversation_generations.add(slot.generation_id)
            if run.session_id != runner.state.meta.id and run.status in {"interrupted", "failed", "awaiting_director"}:
                run.status, run.stop_reason = "cancelled", "branch_changed"
                run.parallel_context = None
                runner.pending_director = None
        runner.turn_runs = self
        runner._turn_recovered = recovered
        runner._turn_durable_state = runner.state.model_copy(deep=True)

    @staticmethod
    def response(runner, run):
        ids = set(run.input_message_ids) | {s.message_id for s in run.slots if s.status == "committed"}
        messages = [m for m in runner.state.messages if m.id in ids or m.turn == run.turn]
        return {"messages": [m.model_dump(mode="json") for m in messages],
            "errors": list(run.errors), "turn_run": run.model_dump(mode="json")}

    async def send(self, runner, req):
        request = req.model_dump(mode="json")
        from mrp.application.send_input import submission_identity
        operation_id, request_hash = submission_identity(req)
        if any(r.operation_id == operation_id and r.session_id == runner.state.meta.id for r in runner.state.conversation_runs):
            raise TurnRunConflict("同一发送 ID 已用于自由交谈，请使用新的发送 ID")
        prior = next((r for r in runner.state.turn_runs
            if r.operation_id == operation_id and r.session_id == runner.state.meta.id), None)
        if prior is not None:
            if prior.request_fingerprint != request_hash:
                raise TurnRunConflict("同一发送 ID 的内容或回应设置已变化，请使用新的发送 ID")
            return self.response(runner, prior)
        if req.client_message_id and any(m.id == req.client_message_id for m in runner.state.messages):
            raise TurnRunConflict("消息 ID 已存在且没有可核对的执行记录，请使用新的发送 ID")
        if runner.busy():
            raise TurnRunConflict("回合进行中，请等待完成或停止后再发送")
        if not runner._ensure_open():
            raise TurnRunConflict("故事已关闭，请重新打开后再发送")
        runner._turn_durable_state = runner.state.model_copy(deep=True)
        run = TurnRun(session_id=runner.state.meta.id, operation_id=operation_id,
            request_fingerprint=request_hash, request=request,
            player_identity_id=runner.state.meta.player_identity_id, scene_id=runner.state.active_scene_id)
        runner.state.turn_runs.append(run)
        return await self._execute(runner, run, initial=True)

    async def _execute(self, runner, run, *, initial=False):
        runner.runtime.active_turn_run_id = run.operation_id
        live = Execution(self, runner, run.operation_id)
        token = execution.set(live)
        request = run.request
        try:
            if initial:
                await runner.turns.player_say(request["content"], request.get("force_character"),
                    request.get("mentions") or None, request.get("channel", "dialogue"),
                    client_message_id=request.get("client_message_id"), reply_mode=request.get("reply_mode"),
                    expected_player_identity_id=request.get("expected_player_identity_id"))
            else:
                async with runner.runtime.turn_lock:
                    inputs = [m for m in runner.state.messages if m.id in run.input_message_ids]
                    await runner.turns.execute_player_input(request["content"], request.get("force_character"),
                        request.get("mentions") or None, request.get("channel", "dialogue"),
                        run.turn, inputs, request.get("reply_mode"))
            # A failed reply checkpoint restores models in place and replaces
            # the run objects; terminal changes must target the current model.
            run = self.find(runner, run.operation_id)
            if run.epoch == live.epoch and run.status == "running":
                run.errors = list(runner.runtime.turn_errors)
                incomplete = any(s.status != "committed" for s in run.slots)
                run.status = "failed" if incomplete or run.errors else (
                    "awaiting_director" if runner.pending_director is not None else "completed")
                if run.status == "completed":
                    run.parallel_context = None
                run.last_error = run.errors[-1] if run.errors else None
                async with live.checkpoint_lock:
                    await live.checkpoint()
        except BaseException as exc:
            if not run.input_message_ids:
                runner.state.turn_runs = [r for r in runner.state.turn_runs if r.operation_id != run.operation_id]
                raise
            if isinstance(exc, TurnCheckpointFailure):
                raise
            run = self.find(runner, run.operation_id)
            if run.epoch == live.epoch and run.status == "running":
                run.status = "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
                run.last_error = str(exc)[:400] or "生成中断，已接受的输入和完整回应已保存"
                run.errors = [*runner.runtime.turn_errors, run.last_error]
                run.stop_reason = "request_cancelled" if isinstance(exc, asyncio.CancelledError) else "generation_failed"
                for slot in run.slots:
                    if slot.status != "committed":
                        runner.runtime.retired_conversation_generations.add(slot.generation_id)
                async with live.checkpoint_lock:
                    await live.checkpoint()
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            execution.reset(token)
            if runner.runtime.active_turn_run_id == run.operation_id:
                runner.runtime.active_turn_run_id = None
        return self.response(runner, self.find(runner, run.operation_id))

    async def resume(self, runner, operation_id, *, expected_branch_revision=None, expected_player_identity_id=None):
        run = self.find(runner, operation_id)
        if run.status == "completed":
            return self.response(runner, run)
        if runner.busy() or run.status == "running":
            raise TurnRunConflict("这一轮仍在执行，请等待后再恢复")
        if run.status not in {"failed", "interrupted"}:
            raise TurnRunConflict("这一轮不能恢复；导演提案请先确认或拒绝")
        if expected_branch_revision is not None and expected_branch_revision != runner.state.meta.branch_revision:
            raise TurnRunConflict("路线已经变化，请刷新后再恢复")
        if (run.player_identity_id != runner.state.meta.player_identity_id
                or (expected_player_identity_id is not None and expected_player_identity_id != run.player_identity_id)
                or run.basis_fingerprint != material_fingerprint(runner.state)):
            raise TurnRunConflict("路线、人物或控制身份已变化，请基于当前剧情发送新输入")
        run.epoch += 1
        run.status, run.errors, run.last_error, run.stop_reason = "running", [], None, ""
        runner.runtime.turn_errors = []
        live = Execution(self, runner, operation_id)
        runner.runtime.active_turn_run_id = operation_id
        try:
            async with live.checkpoint_lock:
                await live.checkpoint()
        except BaseException:
            runner.runtime.active_turn_run_id = None
            raise
        return await self._execute(runner, run)

    async def stop(self, runner, operation_id):
        run = self.find(runner, operation_id)
        if run.status not in {"running", "failed", "interrupted"}:
            return self.response(runner, run)
        live = Execution(self, runner, operation_id)
        async with live.checkpoint_lock:
            run.epoch += 1
            run.status, run.stop_reason = "interrupted", "player_stopped"
            for slot in run.slots:
                if slot.status != "committed":
                    slot.status = "cancelled"
            await live.checkpoint()
            for slot in run.slots:
                if slot.status == "cancelled":
                    generation = runner.runtime.generation_events.get(slot.message_id, {})
                    await runner._emit("message.error", {"message_id": slot.message_id, "turn": run.turn,
                        "generation_id": slot.generation_id,
                        "attempt_id": generation.get("attempt_id"), "operation_id": run.operation_id,
                        "error": "已停止，这条未完成回应未写入故事", "discard_pending": True})
                    runner.runtime.retired_conversation_generations.add(slot.generation_id)
                    runner.runtime.pending_messages.pop(slot.message_id, None)
        return self.response(runner, run)

    async def resolve_director(self, runner, *, accept):
        pending = runner.pending_director
        if pending is None:
            return None
        run = next((r for r in reversed(runner.state.turn_runs)
            if r.session_id == runner.state.meta.id and r.status in {"awaiting_director", "failed"} and r.turn == pending.turn), None)
        if run is None:
            result = await (runner.confirm_pending_director() if accept else runner.reject_pending_director())
            await self.container.persist_session(runner)
            return {"messages": [m.model_dump(mode="json") for m in result or []]}
        if runner.busy():
            raise TurnRunConflict("回合进行中，请稍后确认导演提案")
        run.status, run.errors = "running", []
        runner.runtime.turn_errors = []
        runner.pending_director = None
        if not accept:
            pending = runner.director.decide(trigger="talkativeness", text="", characters=runner.state.characters,
                history=runner.state.messages, rng_seed=pending.rng_seed)
            pending.turn = run.turn
            pending.rationale = "玩家否决 LLM 决策，回退规则路由"
            runner.state.director_log.append(pending)
        run.plan = {"kind": "route", "decision": pending.model_dump(mode="json"),
            "group_targets": run.plan.get("group_targets", []) if run.plan else []}
        live = Execution(self, runner, run.operation_id)
        runner.runtime.active_turn_run_id = run.operation_id
        try:
            async with live.checkpoint_lock:
                await live.checkpoint()
        except BaseException:
            runner.runtime.active_turn_run_id = None
            raise
        return await self._execute(runner, run)
