"""记忆固化与摘要：后台任务、手动固化、卫生重查（W3 拆分自 session.py）。

本模块携带三条审计修复：
- B7：手动 `consolidate_all` 与后台固化共用同一把 `consolidation_lock`——
  冲突时后到者等待锁，锁内复检水位后自然 no-op（绝不并发各写一条记录）；
- B8：失败重试的 `asyncio.sleep(30)` 在锁外（睡醒后重新取锁复检水位再试）；
- B23：`asyncio.to_thread` worker 只读浅快照（messages 列表副本 + turn 上界），
  不直接读事件循环线程持有的 `state.messages`。
"""
from __future__ import annotations

import asyncio
import logging

from mrp.orchestrator.runtime import snapshot_state
from mrp.shared.models import MemoryRecord, Message, TurnContext, TokenUsage

logger = logging.getLogger(__name__)

_CONSOLIDATION_RETRY_DELAY_S = 30.0
MEMORY_INPUT_BUDGET = 8000


def memory_input_limit(capacity: int | None) -> int:
    """One extraction stays inside a small budget, even when the model context is huge."""
    if capacity is None or capacity > MEMORY_INPUT_BUDGET:
        return MEMORY_INPUT_BUDGET
    return max(1, int(capacity))


class MemoryPipeline:
    """一个 SessionRunner 的记忆固化/摘要流水线（无状态，一律实时读 runner）。"""

    def __init__(self, runner) -> None:
        self.r = runner

    async def generate_memory_records(self, snap, actor, start, end, summarizer=None):
        try:
            return await self._generate_memory_records(snap, actor, start, end, summarizer)
        finally:
            usage = getattr(self.r.episodic_consolidator, 'usage', TokenUsage())
            self.r.turns.track_purpose_cost('memory', usage.model_copy(deep=True))

    async def _generate_memory_records(self, snap, actor, start, end, summarizer=None):
        """Generate a frozen window without writes, retries or notifications."""
        r = self.r
        if r.episodic_consolidator is None:
            # Preserve the legacy summarizer's content policy without its store writes.
            from mrp.orchestrator.memory import _fallback_summarize
            messages = [message for message in snap.messages if start <= message.turn <= end and message.can_see(actor)]
            if not messages:
                return []
            text = '\n'.join(f'{message.actor}: {message.content}' for message in messages)
            summarize = summarizer or _fallback_summarize
            content = await asyncio.to_thread(summarize, text)
            return [MemoryRecord(character_id=actor, session_id=snap.meta.id, kind='episodic', content=content,
                turn_start=start, turn_end=end, source_message_ids=[message.id for message in messages],
                source_fingerprints={message.id:message.fingerprint for message in messages})]
        capacity = None
        if getattr(r.episodic_consolidator, '_network', False):
            settings = getattr(r, 'app_settings', None)
            if settings is not None:
                from mrp.llm import judge_config
                from mrp.orchestrator.model_capacity import resolve_model_capacity
                config = judge_config()
                aux = settings.model_copy(update={'model':config.model,'model_provider':config.provider,
                    'gateway':config.base_url,'context_limit_override':None})
                capacity = memory_input_limit((await resolve_model_capacity(aux, config.model)).input_limit)
        if hasattr(r.episodic_consolidator, 'consolidate_records'):
            result = await asyncio.to_thread(r.episodic_consolidator.consolidate_records,
                snap, actor, start - 1, end, input_limit=capacity,
                existing=r.memory.records_for(actor, session_id=snap.meta.id))
            return list(result)
        record = await asyncio.to_thread(r.episodic_consolidator.consolidate_window, snap, actor, start - 1, end)
        return [record] if record else []

    async def generate_scene_record(self, snap, actor, scene):
        try:
            return await asyncio.to_thread(self.r.scene_summarizer.summarize_scene,
                snapshot_state(snap, upper_turn=scene.turn_end), actor, scene)
        finally:
            self.r.turns.track_purpose_cost('memory', self.r.scene_summarizer.usage.model_copy(deep=True))

    def _sources_still_current(
        self, record: MemoryRecord, fingerprints: dict[str, str],
    ) -> bool:
        live = {message.id: message for message in self.r.state.messages}
        return all(
            message_id in fingerprints
            and message_id in live
            and live[message_id].status == "final"
            and not live[message_id].dependency_stale
            and live[message_id].fingerprint == fingerprints[message_id]
            for message_id in record.source_message_ids
        )

    def _auto_consolidation_enabled(self) -> bool:
        """Production defaults off. Runners built without settings keep the old automatic path."""
        settings = getattr(self.r, "app_settings", None)
        if settings is None:
            return True
        return bool(getattr(settings, "memory_consolidation_enabled", False))

    # ---------- R36.1 触发检查 ----------

    def maybe_spawn_consolidation(self, turn: int, reason: str) -> None:
        """R36.1 触发检查：常规间隔（无状态水位，重启免疫）/场景边界调用方直传 reason。"""
        r = self.r
        if not self._auto_consolidation_enabled():
            return
        if not r.state.meta.memory_enabled:
            return
        if r.episodic_consolidator is None or r.memory is None:
            return
        for c in [*r.state.characters, *r.state.groups]:
            if not getattr(c, "present", getattr(c, "status", "") == "active"):
                continue
            if hasattr(r.memory, "next_window"):
                from mrp.orchestrator.important_memory import visible_memory_messages
                if getattr(r, 'memory_command_port', None) is not None:
                    start, end, repair = r.memory.plan_next_window(c.id, r.state.meta.id, turn, retry_failed=False)
                else:
                    start, end, repair = r.memory.next_window(
                        c.id, r.state.meta.id, turn, visible_memory_messages(r.state, c.id), retry_failed=False)
            else:
                start, end, repair = r.memory.last_consolidated_turn(c.id, r.state.meta.id) + 1, turn, False
            if repair or end - start + 1 >= r.state.meta.memory_interval_turns:
                self.spawn_consolidation_task(turn, reason)
                break  # 单任务内统一处理所有到期角色

    def spawn_consolidation_task(self, turn: int, reason: str) -> None:
        """后台增量固化（重试≤2，全失败静默——绝不影响主流程）。

        并发安全：`consolidation_lock` 串行 + 锁内运行时水位复检——多余的过期任务
        （spawn 时水位未更新）会在锁内看到新水位而自然跳过（no-op，B7）。
        """
        r = self.r
        if r.runtime.closed:  # B5：关闭后不再派发
            return
        if r.episodic_consolidator is None or r.memory is None:
            return
        r.runtime.spawn(self._consolidation_worker(turn, reason))

    async def _consolidation_worker(self, turn: int, reason: str) -> None:
        r = self.r
        interval = r.state.meta.memory_interval_turns
        for c in [*r.state.characters, *r.state.groups]:
            if not getattr(c, "present", getattr(c, "status", "") == "active"):
                continue
            await self._drain_actor_windows(
                c.id, turn, min_interval=interval, retries=2, reason=reason,
                retry_failed=False, drain_forward=False,
            )

    async def _drain_actor_windows(self, character_id: str, turn: int, **kwargs) -> list[MemoryRecord]:
        """Automatic work does one interval. Manual work walks forward until the watermark stops."""
        r = self.r
        drain_forward = bool(kwargs.pop("drain_forward", False))
        if not hasattr(r.memory, "next_window"):
            return await self._consolidate_character(character_id, turn, **kwargs)
        accepted: list[MemoryRecord] = []
        for _ in range(48):
            before = r.memory.last_consolidated_turn(character_id, r.state.meta.id)
            accepted.extend(await self._consolidate_character(character_id, turn, **kwargs))
            if not drain_forward:
                break
            after = r.memory.last_consolidated_turn(character_id, r.state.meta.id)
            if after <= before:
                break
        return accepted

    async def _consolidate_character(
        self, character_id: str, turn: int, *, min_interval: int, retries: int,
        reason: str, turn_start: int | None = None, retry_failed: bool = True,
    ) -> list[MemoryRecord]:
        r = self.r
        port = getattr(r, 'memory_command_port', None)
        if port is not None and r.memory is not None:
            return await port.consolidate_window(r, character_id, turn, min_interval=min_interval,
                reason=reason, turn_start=turn_start, retry_failed=retry_failed)
        if not hasattr(r.memory, "next_window"):
            record = await self._consolidate_legacy(character_id, turn, min_interval=min_interval, retries=retries, reason=reason)
            return [record] if record else []
        async with r.runtime.consolidation_lock:
            snap = snapshot_state(r.state, upper_turn=turn).model_copy(deep=True)
            from mrp.orchestrator.important_memory import visible_memory_messages
            visible = visible_memory_messages(snap, character_id)
            start, end, repair = r.memory.next_window(
                character_id, snap.meta.id, turn, visible, retry_failed=retry_failed)
            if turn_start is not None:
                start, end, repair = turn_start, turn, True
            if start > end or (not repair and end - start + 1 < min_interval):
                return []
            cap = max(1, int(r.state.meta.memory_interval_turns or 1))
            if end - start + 1 > cap:
                wide_end = end
                end = start + cap - 1
                if repair and wide_end != end and hasattr(r.memory, "drop_window"):
                    r.memory.drop_window(snap.meta.id, character_id, start, wide_end)
            fingerprints = {m.id: m.fingerprint for m in visible if start <= m.turn <= end}
            r.memory.mark_window(snap.meta.id, character_id, start, end, "pending", fingerprints)
            batch_usage = TokenUsage()
            try:
                capacity = None
                if getattr(r.episodic_consolidator, "_network", False):
                    capacity = memory_input_limit(None)
                    settings = getattr(r, "app_settings", None)
                    if settings is not None:
                        from mrp.llm import judge_config
                        from mrp.orchestrator.model_capacity import resolve_model_capacity
                        config = judge_config()
                        aux = settings.model_copy(update={"model": config.model, "model_provider": config.provider,
                                                          "gateway": config.base_url, "context_limit_override": None})
                        capacity = memory_input_limit((await resolve_model_capacity(aux, config.model)).input_limit)
                if hasattr(r.episodic_consolidator, "consolidate_records"):
                    records = await asyncio.to_thread(r.episodic_consolidator.consolidate_records,
                        snap, character_id, start - 1, end, input_limit=capacity,
                        existing=r.memory.records_for(character_id, session_id=snap.meta.id))
                    batch_usage = getattr(records, "usage", r.episodic_consolidator.usage).model_copy(deep=True)
                else:
                    record = await asyncio.to_thread(r.episodic_consolidator.consolidate_window,
                                                       snap, character_id, start - 1, end)
                    records = [record] if record else []
                async with r.runtime.turn_lock:
                    current = {m.id: m.fingerprint for m in visible_memory_messages(r.state, character_id)
                               if start <= m.turn <= end}
                    if current != fingerprints or r.state.meta.player_identity_id != snap.meta.player_identity_id:
                        r.memory.mark_window(snap.meta.id, character_id, start, end, "dirty", fingerprints,
                                             "整理期间来源已改变")
                        return []
                    accepted = await asyncio.to_thread(r.memory.commit_window, snap.meta.id,
                                                       character_id, start, end, fingerprints, records)
                await r._emit("memory.consolidated", {"character_id": character_id,
                    "records": [record.model_dump(mode="json") for record in accepted],
                    "reason": reason, "turn_start": start, "turn_end": end})
                return accepted
            except Exception as error:
                batch_usage = getattr(error, "memory_usage", batch_usage)
                r.memory.mark_window(snap.meta.id, character_id, start, end, "failed", fingerprints,
                                     "整理失败，可再次整理；未提交不完整记忆")
                logger.warning("memory extraction failed (actor=%s range=%s-%s)", character_id, start, end, exc_info=True)
                if retries <= 1:
                    raise
                return []
            finally:
                r.turns.track_purpose_cost("memory", batch_usage)

    async def _consolidate_legacy(
        self, character_id: str, turn: int, *,
        min_interval: int, retries: int, reason: str,
    ) -> MemoryRecord | None:
        """单角色固化：锁内读水位 + 生成 + 写入；失败重试的 sleep 在锁外（B8）。

        - min_interval：`turn - watermark < min_interval` 即跳过（后台=会话间隔；
          手动=0——手动路径不筛间隔，只靠水位防重复）；
        - retries=1：异常向上抛（手动路径保持既有语义）；retries>=2：重试后静默。
        """
        r = self.r
        port = getattr(r, 'memory_command_port', None)
        if port is not None and r.memory is not None:
            records = await port.consolidate_window(r, character_id, turn, min_interval=min_interval, reason=reason)
            return records[0] if records else None
        for attempt in range(retries):
            failed = False
            async with r.runtime.consolidation_lock:
                watermark = r.memory.last_consolidated_turn(character_id, r.state.meta.id)
                if turn - watermark < min_interval:
                    return None  # 已被其它路径固化到水位（B7：并发 no-op）
                snap = snapshot_state(r.state, upper_turn=turn)  # B23：worker 只读快照
                source_fingerprints = {
                    message.id: message.fingerprint for message in snap.messages
                }
                try:
                    record = await asyncio.to_thread(
                        r.episodic_consolidator.consolidate_window,
                        snap, character_id, watermark, turn,
                    )
                except Exception:  # noqa: BLE001 —— 容错：后台静默重试
                    logger.warning(
                        "consolidation failed (character=%s turn=%s attempt=%s)",
                        character_id, turn, attempt, exc_info=True,
                    )
                    if retries <= 1:
                        raise  # 手动路径：异常上抛（锁随栈展开释放）
                    failed = True
                    record = None
                if record is not None:
                    async with r.runtime.turn_lock:
                        if r.state.meta.player_identity_id != snap.meta.player_identity_id or not self._sources_still_current(record, source_fingerprints):
                            return None
                        await asyncio.to_thread(r.memory.add, record)
                    r.turns.track_purpose_cost("memory", r.episodic_consolidator.usage)
                    await r._emit(
                        "memory.consolidated",
                        {
                            "character_id": character_id,
                            "records": [record.model_dump(mode="json")],
                            "reason": reason,
                        },
                    )
                    return record
            if not failed:
                return None  # 空窗口：无新内容可固化
            if attempt + 1 < retries:
                # B8：退避 sleep 不持锁；下轮重新取锁并复检水位
                await asyncio.sleep(_CONSOLIDATION_RETRY_DELAY_S)
        return None

    def spawn_scene_summary_task(self, closed_scene) -> None:
        """R36.3 场景关闭后：每个曾是成员的角色各生成一份视角摘要。"""
        r = self.r
        if not self._auto_consolidation_enabled():
            return
        if not r.state.meta.memory_enabled:
            return
        if r.scene_summarizer is None or r.memory is None:
            return
        if r.runtime.closed:  # B5：关闭后不再派发
            return
        r.runtime.spawn(self._scene_summary_worker(closed_scene))

    async def _scene_summary_worker(self, closed_scene) -> None:
        r = self.r
        port = getattr(r, 'memory_command_port', None)
        if port is not None:
            await port.summarize_scene(r, closed_scene)
            return
        # B23：worker 只读浅快照（上界=场景关闭回合；对象本身共享，只读）
        snap = snapshot_state(r.state, upper_turn=closed_scene.turn_end)
        source_fingerprints = {message.id: message.fingerprint for message in snap.messages}
        for cid in list(closed_scene.member_ids):
            try:
                record = await asyncio.to_thread(
                    r.scene_summarizer.summarize_scene,
                    snap, cid, closed_scene,
                )
            except Exception:  # noqa: BLE001 —— 静默失败
                logger.warning(
                    "scene summary failed (scene=%s character=%s)",
                    closed_scene.id, cid, exc_info=True,
                )
                continue
            if record is None:
                continue
            async with r.runtime.turn_lock:
                if r.state.meta.player_identity_id != snap.meta.player_identity_id or not self._sources_still_current(record, source_fingerprints):
                    continue
                await asyncio.to_thread(r.memory.add, record)
            r.turns.track_purpose_cost("memory", r.scene_summarizer.usage)
            await r._emit(
                "memory.consolidated",
                {
                    "character_id": cid,
                    "records": [record.model_dump(mode="json")],
                    "reason": "scene",
                },
            )

    async def consolidate_all(self, reason: str = "manual") -> list:
        """手动固化（同步路径，既有端点复用）：全角色立即固化当前窗口。

        手动不筛 present——离席角色对其在场期间发生的事同样有记忆
        （自动触发的 bg 路径才做 present 过滤控成本）。
        B7：与后台固化共用同一把锁；等待后锁内复检水位，不并发各写一条。
        """
        r = self.r
        if not r._ensure_open():  # B5
            return []
        if r.episodic_consolidator is None or r.memory is None:
            return []
        turn = r.state.current_turn()
        out: list = []
        for c in [*r.state.characters, *r.state.groups]:
            record = await self._drain_actor_windows(
                c.id, turn, min_interval=0, retries=1, reason=reason,
                retry_failed=True, drain_forward=True,
            )
            if record is not None:
                out.extend(record)
        return out

    # ---------- R34 卫生重查 ----------

    async def recheck_hygiene(self, message_id: str) -> Message | None:
        """R34「可再试」：对消息当前 content 重跑校验并回写。

        B22：复用回合内同一校验入口（`TurnEngine.run_hygiene_check`），
        checks / violations_found 统计与用量账口径完全对齐。
        """
        r = self.r
        if not r._ensure_open():  # B5
            return None
        async with r.runtime.turn_lock:
            msg = next((m for m in r.state.messages if m.id == message_id), None)
            if msg is None or msg.actor == "player" or msg.status != "final":
                return None
            if not r.hygiene_active:
                return None
            character = r.state.character(msg.actor)
            if character is None:
                return None
            fake_ctx = TurnContext(
                session_id=r.state.meta.id,
                character_id=character.id,
                turn=msg.turn,
                visible_messages=r.state.visible_messages_for(character.id),
                injections=[],
                budget_tokens=None,
            )
            report = await r.turns.run_hygiene_check(character, fake_ctx, msg.content)
            msg.hygiene = report
            if msg.variants and msg.active_variant is not None:
                msg.variants[msg.active_variant].hygiene = report
            await r._emit("message.updated", {"message": msg.model_dump(mode="json")})
            return msg
