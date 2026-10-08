"""Durable, finite conversations. Background ownership is separate from turn locks."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextvars import copy_context
from types import SimpleNamespace

from mrp.orchestrator.conversation_context import ConversationConflict, owner, usage_hook
from mrp.orchestrator.conversation_selector import ConversationSelector, ScriptedConversationSelector
from mrp.orchestrator.generation_sources import make_provenance, source_ref
from mrp.orchestrator.message_regeneration import restore_state_in_place
from mrp.shared.models import ConversationRun, ConversationStep, Message, TokenUsage, new_id, utcnow
from mrp.shared.player_identity import player_key
from mrp.orchestrator import visibility

logger = logging.getLogger(__name__)
ACTIVE = {"queued", "running"}


class ConversationRuns:
    def __init__(self, container, selector=None):
        self.container = container
        self.selector = selector or (ScriptedConversationSelector() if container.fake_mode else ConversationSelector(container))
        self.tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    def _lock(self, sid):
        return self.locks.setdefault(sid, asyncio.Lock())

    @staticmethod
    def _find(runner, rid, *, owned=True):
        run = next((x for x in runner.state.conversation_runs if x.id == rid), None)
        if run is None:
            raise ConversationConflict("这段对话运行不存在")
        if owned and run.session_id != runner.state.meta.id:
            raise ConversationConflict("这段运行属于原世界线，请在当前世界线新建一段")
        return run

    def attach(self, runner):
        # Loading/restoring/forking never recreates an in-memory job.
        recovered = False
        for run in runner.state.conversation_runs:
            if run.status in ACTIVE:
                recovered = True
                run.status, run.stage = "interrupted", "boundary"
                run.epoch += 1
                run.stop_reason = "service_restarted" if run.session_id == runner.state.meta.id else "branch_changed"
                run.usage_incomplete = True
                run.updated_at = utcnow()
                self._record_unfinished(run, "cancelled", "运行中断，未完成回复未提交")
                runner.state.messages = [m for m in runner.state.messages
                    if m.status == "final" or m.id != run.current_message_id]
                if run.current_generation_id:
                    runner.runtime.retired_conversation_generations.add(run.current_generation_id)
        runner.conversation_runs = self
        runner._conversation_recovered = recovered

    @staticmethod
    def _candidates(runner, participants):
        state, scene = runner.state, runner.active_scene()
        if scene is None:
            raise ConversationConflict("当前存档没有有效场景，请先选择场景")
        controlled = player_key(state)
        chars = {c.id: c for c in state.characters if c.present and not c.muted
                 and c.id in scene.member_ids and c.id != controlled}
        groups = {g.id: g for g in state.groups if g.status == "active" and g.scene_id == scene.id
                  and g.id in scene.group_ids and g.id != controlled}
        available = {**chars, **groups}
        selected = list(dict.fromkeys(participants))
        if not selected or any(cid not in available for cid in selected):
            raise ConversationConflict("请选择当前场景中可回应的人物或群体；受控人物不能自动发言")
        return {cid: available[cid] for cid in selected}

    @staticmethod
    def shared_messages(state, candidates):
        # The selector only knows facts actually shared by all eligible actors.
        joined = {cid: getattr(actor, "joined_seq", state.character_joined_at_seq.get(cid, 0))
                  for cid, actor in candidates.items()}
        return [m for m in state.messages if m.status == "final" and not m.dependency_stale
                and m.scene_id == state.active_scene_id and m.kind in {"scene", "roleplay"}
                and not m.control_event
                and all(m.seq >= joined[cid] and m.can_see(cid) for cid in candidates)]

    @staticmethod
    def _cas(runner, revision, identity):
        if runner.state.meta.branch_revision != revision:
            raise ConversationConflict("世界线已经更新，请刷新后再继续")
        if runner.state.meta.player_identity_id != identity:
            raise ConversationConflict("控制身份已经变化，请刷新后确认身份")

    @staticmethod
    def _merge_usage(target, source):
        seen = {row.id for row in target.usage_records}
        target.usage_records.extend(row for row in source.usage_records if row.id not in seen)
        target.usage_incomplete = target.usage_incomplete or source.usage_incomplete
        known = {x.id: x for x in source.conversation_runs}
        for run in target.conversation_runs:
            live = known.get(run.id)
            if live is None: continue
            for name in ("input_tokens", "output_tokens", "cached_tokens"):
                setattr(run.cumulative_usage, name, max(getattr(run.cumulative_usage, name), getattr(live.cumulative_usage, name)))
                if run.current_generation_id == live.current_generation_id:
                    setattr(run.current_usage, name, max(getattr(run.current_usage, name), getattr(live.current_usage, name)))

    async def _persist(self, runner, before, *, staged=None):
        try:
            if staged is not None:
                self._merge_usage(staged, runner.state)
            await self.container.persist_session(runner if staged is None else SimpleNamespace(state=staged))
            if staged is not None:
                self._merge_usage(staged, runner.state)
                restore_state_in_place(runner.state, staged)
            runner._committed_state = runner.state.model_copy(deep=True)
        except BaseException:
            # save_state increments revision before its atomic file write.
            self._merge_usage(before, runner.state)
            restore_state_in_place(runner.state, before)
            try:
                await self.container.sessions.restore_state(runner.state)
            except Exception:
                logger.exception("conversation checkpoint compensation failed")
            raise

    async def _updated(self, runner, rid):
        run = self._find(runner, rid, owned=False)
        await runner._emit("conversation.run.updated", {"run": run.model_dump(mode="json"),
                           "branch_revision": runner.state.meta.branch_revision})

    async def _change(self, runner, rid, update):
        before = runner.state.model_copy(deep=True)
        staged = before.model_copy(deep=True)
        run = next(x for x in staged.conversation_runs if x.id == rid)
        update(run)
        run.updated_at = utcnow()
        await self._persist(runner, before, staged=staged)
        if self._find(runner, rid, owned=False).status not in ACTIVE and runner.runtime.active_conversation_run_id == rid:
            runner.runtime.active_conversation_run_id = None
        await self._updated(runner, rid)
        return self._find(runner, rid, owned=False)

    def _spawn(self, runner, run):
        runner.runtime.active_conversation_run_id = run.id
        key = runner.state.meta.id, run.id
        context = copy_context()
        context.run(owner.set, None)
        context.run(usage_hook.set, None)
        task = asyncio.create_task(self._execute(runner, run.id, run.epoch), context=context)
        self.tasks[key] = task
        task.add_done_callback(lambda done: self.tasks.pop(key, None) if self.tasks.get(key) is done else None)

    @staticmethod
    def _append_staged(runner, state, message):
        from mrp.orchestrator.runtime import append_message
        from mrp.orchestrator.worldline_state import record_message_revision
        message = append_message(state, message)
        watermark = (runner.memory.current_watermark(state.meta.id)
            if runner.memory is not None and hasattr(runner.memory, "current_watermark") else None)
        record_message_revision(state, message, watermark)
        return message

    async def start(self, runner, *, operation_id, participant_ids, max_replies=6, directive="",
                    expected_branch_revision, expected_player_identity_id, mode="observe", seed=None, submission_fingerprint=None):
        if not operation_id or not 1 <= max_replies <= 30:
            raise ConversationConflict("每段回复数应为 1 至 30，并提供操作 ID")
        sid = runner.state.meta.id
        payload = {"mode": mode, "participants": list(dict.fromkeys(participant_ids)), "max_replies": max_replies,
                   "directive": directive, "seed": seed}
        fingerprint = submission_fingerprint or hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        async with self._lock(sid):
            prior = next((x for x in runner.state.conversation_runs if x.operation_id == operation_id
                          and x.session_id == sid), None)
            if prior:
                if prior.request_fingerprint != fingerprint:
                    raise ConversationConflict("操作 ID 已用于另一段对话")
                return prior
            if runner.busy():
                raise ConversationConflict("当前正在回应，请先暂停或停止")
            self._cas(runner, expected_branch_revision, expected_player_identity_id)
            self._candidates(runner, participant_ids)
            token = owner.set((sid, "starting"))
            try:
                async with runner.runtime.turn_lock:
                    self._cas(runner, expected_branch_revision, expected_player_identity_id)
                    before = runner.state.model_copy(deep=True)
                    staged = before.model_copy(deep=True)
                    run = ConversationRun(session_id=sid, operation_id=operation_id, request_fingerprint=fingerprint,
                        mode=mode, participant_ids=payload["participants"], scene_id=runner.state.active_scene_id,
                        player_identity_id=expected_player_identity_id, directive=directive, max_replies=max_replies,
                        turn=runner.state.current_turn() + 1)
                    staged.conversation_runs.append(run)
                    if seed is not None:
                        from mrp.orchestrator.channels import parse_channels
                        parts = parse_channels(seed["content"], default_kind="scene" if seed["channel"] == "narration" else "roleplay")
                        for index, (kind, text) in enumerate(parts):
                            message = self._append_staged(runner, staged, Message(
                                id=seed.get("client_message_id") if index == 0 and seed.get("client_message_id") else new_id("msg"),
                                session_id=sid, seq=staged.next_seq(), turn=run.turn, actor="player", content=text,
                                kind=kind, visible_to=["player"] if kind == "inner" else visibility.compute_visible_to(
                                    runner.state.characters, group_ids=visibility.active_group_ids(runner.state)),
                                input_group_id="input-" + operation_id, mentions=run.participant_ids if kind != "inner" else [],
                                reply_mode="free" if kind != "inner" else None, executed_reply_mode="free" if kind != "inner" else None,
                            ))
                            run.seed_message_ids.append(message.id)
                        if not any(m.kind in {"scene", "roleplay"} for m in staged.messages if m.id in run.seed_message_ids):
                            run.status, run.stop_reason = "completed", "private_input"
                    await self._persist(runner, before, staged=staged)
                    run = self._find(runner, run.id)
                    for message in runner.state.messages:
                        if message.id in run.seed_message_ids:
                            await runner._emit("message.final", {"message": message.model_dump(mode="json")})
                    if run.status in ACTIVE:
                        self._spawn(runner, run)
                    await self._updated(runner, run.id)
                    return run
            finally:
                owner.reset(token)

    async def send_free(self, runner, req):
        if req.channel == "inner":
            # Private input uses the same durable acceptance and operation policy.
            return await self.container.turn_runs.send(runner, req)
        from mrp.application.send_input import submission_identity
        operation, fingerprint = submission_identity(req)
        if any(r.operation_id == operation and r.session_id == runner.state.meta.id for r in runner.state.turn_runs):
            raise ConversationConflict("同一发送 ID 已用于普通回合，请使用新的发送 ID")
        run = await self.start(runner, operation_id=operation, participant_ids=req.mentions or ([req.force_character] if req.force_character else []),
            max_replies=req.max_replies, expected_branch_revision=runner.state.meta.branch_revision,
            expected_player_identity_id=req.expected_player_identity_id, mode="free",
            directive=getattr(req, "conversation_directive", ""),
            seed={"content": req.content, "channel": req.channel, "client_message_id": req.client_message_id},
            submission_fingerprint=fingerprint)
        task = self.tasks.get((runner.state.meta.id, run.id))
        if task is not None:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # A queued service job can be stopped before its coroutine starts.
                # Client cancellation still leaves this independent job alive.
                if asyncio.current_task().cancelling() or not task.cancelled():
                    raise
        run = self._find(runner, run.id)
        ids = set(run.seed_message_ids + [s.message_id for s in run.steps if s.status == "committed"])
        return {"messages": [m.model_dump(mode="json") for m in runner.state.messages if m.id in ids],
                "errors": [run.last_error] if run.last_error else [], "conversation_run": run.model_dump(mode="json")}

    async def list(self, runner):
        return runner.state.conversation_runs

    async def get(self, runner, rid):
        return self._find(runner, rid, owned=False)

    @staticmethod
    def _record_unfinished(run, status, error):
        if run.current_generation_id and not any(s.generation_id == run.current_generation_id for s in run.steps):
            run.steps.append(ConversationStep(id=run.current_step_id or new_id("step"), index=len(run.steps) + 1,
                epoch=run.epoch,
                generation_id=run.current_generation_id, speaker_id=run.current_speaker_id or "",
                attempt_id=run.current_attempt_id,
                message_id=run.current_message_id, status=status, error=error,
                trigger_message_ids=run.current_trigger_message_ids, reply_to_message_ids=run.current_reply_to_message_ids,
                usage=run.current_usage.model_copy()))

    async def _discard(self, runner, run, error):
        if not run.current_generation_id:
            return
        generation = runner.runtime.generation_events.get(run.current_message_id, {})
        # Announce retirement before suppressing late deltas/finals from workers.
        await runner._emit("message.error", {"message_id": run.current_message_id,
            "generation_id": generation.get("generation_id") or run.current_generation_id,
            "operation_id": generation.get("operation_id") or run.operation_id,
            "attempt_id": generation.get("attempt_id") or run.current_attempt_id,
            "turn": run.turn, "error": error, "discard_pending": True})
        runner.runtime.retired_conversation_generations.add(run.current_generation_id)

    async def pause(self, runner, rid):
        async with self._lock(runner.state.meta.id):
            run = self._find(runner, rid)
            if run.status not in ACTIVE or run.pause_requested:
                return run
            def update(item):
                item.pause_requested = True
                if item.stage == "boundary" or item.status == "queued":
                    item.status, item.stop_reason = "paused", "user_paused"
            return await self._change(runner, rid, update)

    async def stop(self, runner, rid):
        async with self._lock(runner.state.meta.id):
            run = self._find(runner, rid)
            if run.status == "cancelled":
                return run
            before = runner.state.model_copy(deep=True)
            staged = before.model_copy(deep=True)
            run = next(x for x in staged.conversation_runs if x.id == rid)
            generation = runner.runtime.generation_events.get(run.current_message_id, {})
            run.current_attempt_id = generation.get("attempt_id") or run.current_attempt_id
            self._record_unfinished(run, "cancelled", "用户停止")
            run.usage_incomplete |= run.status in ACTIVE and run.stage in {"generating", "selecting"}
            run.epoch += 1
            run.stop_requested = True
            run.status, run.stage, run.stop_reason = "cancelled", "boundary", "user_stopped"
            run.updated_at = utcnow()
            await self._persist(runner, before, staged=staged)
            run = self._find(runner, rid)
            await self._discard(runner, run, "运行已停止，未完成回复已撤销")
            task = self.tasks.get((runner.state.meta.id, rid))
            if task: task.cancel()
            if runner.runtime.active_conversation_run_id == rid:
                runner.runtime.active_conversation_run_id = None
            await self._updated(runner, rid)
            return self._find(runner, rid)

    async def resume(self, runner, rid, *, expected_branch_revision, expected_player_identity_id, additional_replies=None):
        async with self._lock(runner.state.meta.id):
            run = self._find(runner, rid)
            if runner.busy():
                raise ConversationConflict("当前正在回应，请等待暂停完成")
            self._cas(runner, expected_branch_revision, expected_player_identity_id)
            if run.status not in {"paused", "awaiting_user", "completed", "failed", "interrupted"}:
                raise ConversationConflict("当前运行不能继续，请新建一段对话")
            if run.scene_id != runner.state.active_scene_id or run.player_identity_id != runner.state.meta.player_identity_id:
                raise ConversationConflict("场景或控制身份已变化，请在当前场景新建一段")
            self._candidates(runner, run.participant_ids)
            if additional_replies is None and (run.status == "completed" or run.completed_replies >= run.max_replies):
                additional_replies = 6
            if additional_replies is not None and not 1 <= additional_replies <= 30:
                raise ConversationConflict("追加回复数应为 1 至 30")
            def update(item):
                if additional_replies is not None:
                    item.max_replies = item.completed_replies + additional_replies
                item.status, item.stage = "queued", "boundary"
                item.epoch += 1
                item.pause_requested = item.stop_requested = False
                item.last_error, item.stop_reason = None, ""
                item.current_speaker_id = item.current_step_id = item.current_message_id = None
                item.current_generation_id = item.current_attempt_id = None
                item.current_trigger_message_ids = item.current_reply_to_message_ids = []
                item.current_usage = TokenUsage()
                item.turn = runner.state.current_turn() + (1 if item.mode == "observe" else 0)
            async with runner.runtime.turn_lock:
                self._cas(runner, expected_branch_revision, expected_player_identity_id)
                run = await self._change(runner, rid, update)
                self._spawn(runner, run)
                return run

    def _valid(self, runner, rid, epoch):
        run = self._find(runner, rid, owned=False)
        return run.session_id == runner.state.meta.id and run.epoch == epoch and run.status in ACTIVE and not run.stop_requested

    async def _execute(self, runner, rid, epoch):
        sid = runner.state.meta.id
        own = owner.set((sid, rid))
        step_usage = TokenUsage()
        def accumulate(usage, purpose):
            nonlocal step_usage
            usage = usage if isinstance(usage, TokenUsage) else TokenUsage(**usage)
            run = self._find(runner, rid, owned=False)
            for name in ("input_tokens", "output_tokens", "cached_tokens"):
                setattr(run.cumulative_usage, name, getattr(run.cumulative_usage, name) + getattr(usage, name))
                setattr(step_usage, name, getattr(step_usage, name) + getattr(usage, name))
            run.current_usage = step_usage.model_copy()
        hook = usage_hook.set(accumulate)
        try:
            while self._valid(runner, rid, epoch):
                async with runner.runtime.turn_lock:
                    async with self._lock(sid):
                        if not self._valid(runner, rid, epoch): break
                        run = self._find(runner, rid)
                        if run.pause_requested:
                            await self._change(runner, rid, lambda x: self._terminal(x, "paused", "user_paused"))
                            break
                        if run.completed_replies >= run.max_replies:
                            await self._change(runner, rid, lambda x: self._terminal(x, "completed", "max_replies"))
                            break
                        if run.scene_id != runner.state.active_scene_id or run.player_identity_id != runner.state.meta.player_identity_id:
                            raise ConversationConflict("场景或控制身份已变化")
                        candidates = self._candidates(runner, run.participant_ids)
                        public = self.shared_messages(runner.state, candidates)
                        step_usage = TokenUsage()
                        await self._change(runner, rid, lambda x: self._selecting(x))
                        snapshot = runner.state.model_copy(deep=True)
                        run_snapshot = self._find(runner, rid).model_copy(deep=True)
                    selection = await self.selector.select(state=snapshot, run=run_snapshot,
                        candidates=candidates, public_messages=public)
                    runner.turns.track_purpose_cost("conversation_selector", selection.usage)
                    async with self._lock(sid):
                        if not self._valid(runner, rid, epoch): break
                        run = self._find(runner, rid)
                        scheduling_ids = getattr(selection, "trace", {}).get("included_message_ids", [m.id for m in public])
                        scheduling_messages = [m for m in public if m.id in scheduling_ids]
                        trace = {"step_index": len(run.steps) + 1, "action": selection.action,
                            "speaker_id": selection.speaker_id, "reason": selection.reason,
                            "source_message_ids": [m.id for m in scheduling_messages], "usage": selection.usage.model_dump(),
                            **getattr(selection, "trace", {})}
                        run.scheduling_trace.append(trace)
                        if run.pause_requested:
                            await self._change(runner, rid, lambda x: self._terminal(x, "paused", "user_paused"))
                            break
                        if selection.action != "speak":
                            await self._change(runner, rid, lambda x: self._terminal(x,
                                "awaiting_user" if selection.action == "needs_user" else "completed",
                                "needs_user" if selection.action == "needs_user" else "natural_stop"))
                            break
                        if selection.speaker_id not in candidates:
                            raise ConversationConflict("发言调度选择了不可用人物")
                        replied = getattr(selection, "reply_to_message_ids", []) or ([public[-1].id] if public else [])
                        if any(mid not in {m.id for m in public} for mid in replied):
                            raise ConversationConflict("发言调度引用了人物不可见的消息")
                        provenance = make_provenance(snapshot, "free", list(candidates), reply_to_message_ids=replied,
                            scheduling_sources=[source_ref(m) for m in scheduling_messages], director_directive=run.directive)
                        provenance.trigger_message_ids = [public[-1].id] if public else []
                        provenance.run_id, provenance.step_id = rid, new_id("step")
                        object.__setattr__(snapshot, "_conversation_turn", run.turn)
                        object.__setattr__(snapshot, "_conversation_directive", run.directive)
                        pending = Message(session_id=sid, seq=runner.state.next_seq(), turn=run.turn,
                            actor=selection.speaker_id, content="", kind="roleplay", status="pending",
                            operation_id=run.operation_id, generation_id=new_id("gen"), attempt_id=new_id("attempt"),
                            visible_to=visibility.compute_visible_to(runner.state.characters, group_ids=visibility.active_group_ids(runner.state)))
                        def generating(item):
                            item.stage = "generating"
                            item.current_speaker_id = pending.actor
                            item.current_step_id = provenance.step_id
                            item.current_message_id, item.current_generation_id = pending.id, pending.generation_id
                            item.current_attempt_id = pending.attempt_id
                            item.current_trigger_message_ids = provenance.trigger_message_ids
                            item.current_reply_to_message_ids = provenance.reply_to_message_ids
                            item.current_usage = step_usage.model_copy()
                        await self._change(runner, rid, generating)
                    actor = candidates[pending.actor]
                    if any(g.id == pending.actor for g in runner.state.groups):
                        draft = await runner.turns.run_group_turn(actor, run.turn, state_snapshot=snapshot,
                            pending_override=pending, defer_commit=True, emit_final=False, reply_mode="free",
                            participants=list(candidates), provenance=provenance)
                    else:
                        draft = await runner.turns.run_character_turn(actor, run.turn, context_state=snapshot,
                            pending_override=pending, defer_commit=True, emit_final=False, reply_mode="free",
                            participants=list(candidates), provenance=provenance)
                    async with self._lock(sid):
                        # Cancellation may return late from to_thread or a cancellation-resistant engine.
                        if not self._valid(runner, rid, epoch):
                            if self.container.runners.get(sid) is runner and not runner.runtime.closed:
                                await self._change(runner, rid, lambda x: None)  # retain known late usage only
                            break
                        run = self._find(runner, rid)
                        before = runner.state.model_copy(deep=True)
                        staged = before.model_copy(deep=True)
                        run = next(x for x in staged.conversation_runs if x.id == rid)
                        run.stage = "committing"
                        draft = self._append_staged(runner, staged, draft)
                        step = ConversationStep(id=provenance.step_id, index=len(run.steps) + 1,
                            epoch=epoch,
                            generation_id=draft.generation_id, speaker_id=draft.actor, message_id=draft.id,
                            attempt_id=draft.attempt_id,
                            status="committed", trigger_message_ids=provenance.trigger_message_ids,
                            reply_to_message_ids=provenance.reply_to_message_ids, usage=step_usage.model_copy())
                        run.steps.append(step)
                        run.completed_replies += 1
                        run.last_committed_step_id, run.last_committed_message_id = step.id, draft.id
                        run.current_step_id = run.current_message_id = run.current_generation_id = run.current_attempt_id = None
                        run.current_speaker_id = None
                        run.current_trigger_message_ids = run.current_reply_to_message_ids = []
                        run.current_usage = TokenUsage()
                        run.stage = "boundary"
                        if run.pause_requested:
                            self._terminal(run, "paused", "user_paused")
                        elif run.completed_replies >= run.max_replies:
                            self._terminal(run, "completed", "max_replies")
                        run.updated_at = utcnow()
                        await self._persist(runner, before, staged=staged)
                        run = self._find(runner, rid)
                        if run.status not in ACTIVE and runner.runtime.active_conversation_run_id == rid:
                            runner.runtime.active_conversation_run_id = None
                        await runner._emit("message.final", {"message": draft.model_dump(mode="json")})
                        await self._updated(runner, rid)
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            # stop/shutdown already wrote the terminal checkpoint; do not revive it.
            pass
        except Exception as exc:
            usage = getattr(exc, "usage", None)
            if usage and not getattr(exc, "_conversation_usage_charged", False):
                runner.turns.track_purpose_cost("conversation_failed", TokenUsage(**usage) if isinstance(usage, dict) else usage)
            async with self._lock(sid):
                if self._valid(runner, rid, epoch):
                    run = self._find(runner, rid)
                    await self._discard(runner, run, "本段生成未完成，请重试或继续")
                    def failed(item):
                        item.usage_incomplete |= item.stage in {"selecting", "generating"} and not usage
                        self._record_unfinished(item, "failed", str(exc)[:400])
                        if item.steps and item.steps[-1].status == "failed": item.steps[-1].usage = step_usage.model_copy()
                        self._terminal(item, "failed", "generation_failed")
                        item.last_error = str(exc)[:400]
                    try:
                        await self._change(runner, rid, failed)
                    except Exception:
                        failed(self._find(runner, rid))
                        logger.exception("conversation failure checkpoint could not be saved")
        finally:
            usage_hook.reset(hook)
            owner.reset(own)
            if self.tasks.get((sid, rid)) is asyncio.current_task():
                if runner.runtime.active_conversation_run_id == rid:
                    runner.runtime.active_conversation_run_id = None
                run = self._find(runner, rid, owned=False)
                if run.completed_replies and run.status in {
                    "paused", "awaiting_user", "completed", "failed", "cancelled"
                }:
                    # Keep the existing memory interval policy. Consolidate only
                    # after a committed segment, outside this run's usage hook.
                    try:
                        runner.memory_pipeline.maybe_spawn_consolidation(run.turn, "interval")
                    except Exception:
                        logger.exception("conversation memory scheduling failed")

    @staticmethod
    def _terminal(run, status, reason):
        run.status, run.stage, run.stop_reason = status, "boundary", reason

    @staticmethod
    def _selecting(run):
        run.status, run.stage = "running", "selecting"

    async def close(self):
        for (sid, rid), task in list(self.tasks.items()):
            runner = self.container.runners.get(sid)
            if runner is not None:
                async with self._lock(sid):
                    run = self._find(runner, rid)
                    await self._discard(runner, run, "服务退出，未完成回复未提交")
                    def interrupted(item):
                        self._record_unfinished(item, "cancelled", "服务退出")
                        item.epoch += 1
                        item.usage_incomplete = True
                        self._terminal(item, "interrupted", "service_restarted")
                    try:
                        await self._change(runner, rid, interrupted)
                    except Exception:
                        interrupted(self._find(runner, rid))
                    runner.runtime.active_conversation_run_id = None
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
