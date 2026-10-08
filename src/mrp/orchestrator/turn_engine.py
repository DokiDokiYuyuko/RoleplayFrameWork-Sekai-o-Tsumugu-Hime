"""回合执行：角色回合、真/伪流式、R34 卫生重试、成本累加（W3 拆分自 session.py）。

- `_DeltaBridge`（R48 真流式桥）随回合执行一起内聚在本模块；
- `player_say`（玩家一轮：通道分发 → 垫场 → 导演决策 → 执行）也在此；
- 所有对外契约不变：SessionRunner 上的同名方法为 facade 委托。
"""
from __future__ import annotations

from mrp.orchestrator.completion import completion_state

import asyncio
import logging
import json

from mrp.orchestrator import visibility
from mrp.orchestrator.turn_runs import execution, durable_reply
from mrp.orchestrator.channels import parse_channels
from mrp.orchestrator.context_plan import plan_context
from mrp.orchestrator.generation_sources import make_provenance, finish_provenance
from mrp.orchestrator.scene_frame import build_shared_scene_frame, recent_public_scene_context
from mrp.shared.actor_labels import build_reply_frame
from mrp.shared.player_identity import current_inner, identity_source
from mrp.shared.models import (
    Character,
    ComposedPrompt,
    GenerationMeta,
    GenerationProvenance,
    GroupActor,
    Injection,
    Message,
    MessageVariant,
    SessionState,
    TokenUsage,
    TurnContext,
    fingerprint,
    new_id,
)
from mrp.shared.prompt import persona_from_card, player_name_for_context
from mrp.shared.reply_quality import is_duplicate_reply
from mrp.llm import chat_text_with_usage, director_config, extract_json

logger = logging.getLogger(__name__)

PSEUDO_STREAM_CHUNK = 48  # C5：伪流式合流窗口（24 → 48 字/片，delta 事件约减半）
# 48 与既有 delta 分片断言兼容（test_m8 长回复 `len(deltas) >= 3`：104 字 = 3 片）；
# 调整该值需同步核对 test_m8 的长回复分片断言与 design/v3/m8-r33 的粒度口径。
DELTA_BRIDGE_CLOSE_TIMEOUT = 2.0  # C5：桥关闭超时（5s → 2s，不拖住回合）
_REPLY_PLAN_SYSTEM = """你负责安排玩家明确点选的多人回应。结合玩家输入和近期公开剧情，判断各对象应并行独立反应，还是串行接话。shared_scene 只能概括已经发生的共同场面事实、地点和时间，不得预写任何参与者未来的动作、台词、决定或彼此关系；不需要补充时置为空字符串。只输出 JSON：{\"mode\":\"parallel|serial\",\"order\":[候选对象id...],\"reason\":\"简短中文原因\",\"shared_scene\":\"已知共同场面或空字符串\"}。order 必须是候选 id 的完整排列；并行时也保持候选原顺序。"""


class _DeltaBridge:
    """R48 真流式桥：引擎 on_delta（可能在工作线程触发）→ SSE message.delta。

    - feed() 线程安全（call_soon_threadsafe 投递到事件循环）；
    - pump 后台任务 60ms 合流，把密集小片合并成少量 delta 事件（EventBus 有损队列友好）；
    - close() 停泵并排空，返回已发总字数（含 initial_offset），供 _emit_deltas 去重。
    重试（R34 卫生 / 引擎重建）会换新桥、offset 从 0 重来——前端按
    offset<len 截断重写（chatStore.ts 既有规则）。
    """

    def __init__(
        self,
        emit,
        message_id: str,
        character_id: str,
        turn: int,
        initial_offset: int = 0,
    ) -> None:
        self._emit = emit
        self._message_id = message_id
        self._character_id = character_id
        self._turn = turn
        self._offset = initial_offset
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._task = self._loop.create_task(self._pump())

    def feed(self, text: str) -> None:
        """引擎侧回调（任意线程）。"""
        if text:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, text)

    async def _pump(self) -> None:
        while True:
            first = await self._queue.get()
            if first is None:
                return
            batch = [first]
            deadline = self._loop.time() + 0.06  # 60ms 合流窗口
            while True:
                timeout = deadline - self._loop.time()
                if timeout <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                if item is None:
                    await self._flush(batch)
                    return
                batch.append(item)
            await self._flush(batch)

    async def _flush(self, batch: list[str]) -> None:
        text = "".join(batch)
        if not text:
            return
        await self._emit(
            "message.delta",
            {
                "message_id": self._message_id,
                "turn": self._turn,
                "character_id": self._character_id,
                "delta": text,
                "offset": self._offset,
                "delivery": "live",
            },
            lossy=True,
        )
        self._offset += len(text)

    async def close(self) -> int:
        """停泵排空，返回已发总字数（含 initial_offset）。"""
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        try:
            await asyncio.wait_for(self._task, timeout=DELTA_BRIDGE_CLOSE_TIMEOUT)
        except asyncio.TimeoutError:  # 兜底：不让桥拖住回合
            self._task.cancel()
        return self._offset


class TurnEngine:
    """一个 SessionRunner 的回合执行器（无状态，状态一律实时读 runner）。"""

    def __init__(self, runner) -> None:
        self.r = runner

    def prepare_generation(self, message: Message) -> None:
        message.generation_id = message.generation_id or new_id("gen")
        message.attempt_id = message.attempt_id or new_id("attempt")
        self.r.runtime.generation_events[message.id] = {
            "generation_id": message.generation_id, "operation_id": message.operation_id,
            "attempt_id": message.attempt_id,
        }

    async def announce_attempt(self, message: Message, ctx: TurnContext | None = None) -> None:
        message.attempt_id = new_id("attempt")
        self.prepare_generation(message)
        if ctx is not None:
            ctx.attempt_id = message.attempt_id
            ctx.request_ids = []
        await self.r._emit("message.pending", {
            "message": message.model_copy(update={"content": "", "status": "pending"}).model_dump(mode="json"),
            "character_id": message.actor, "turn": message.turn,
        })

    def store_generation_inspection(self, message, ctx, composed, recall_audit=None):
        record = {"ctx": ctx, "composed": composed, "memory_recall": recall_audit or []}
        self.r.runtime.inspections[(message.actor, message.turn)] = record
        self.r.runtime.inspections.record_generation(message.id, message.generation_id, record)

    # ---------- 玩家一轮 ----------

    async def player_say(
        self,
        content: str,
        force_character: str | None = None,
        mentions: list[str] | None = None,
        channel: str = "dialogue",
        *,
        client_message_id: str | None = None,
        reply_mode: str | None = None,
        expected_player_identity_id: str | None = None,
    ) -> list[Message]:
        """玩家发言触发的完整回合。

        通道（R22 玩家侧三通道）：
        - dialogue：台词（默认，当前行为）
        - inner：内心描写——只存不触发回合，作为潜台词注入后续角色回合
        - narration：画外音——存为场景事件，触发角色反应

        路由优先级（R30.3）：显式 mentions（确定性，按序去重）> force_character
        （点名直通）> 隐式提及检测 > talkativeness 轮盘。
        """
        if not self.r._ensure_open():  # B5：aclose 后 no-op
            return []
        async with self.r.runtime.turn_lock:
            if expected_player_identity_id is not None and expected_player_identity_id != self.r.state.meta.player_identity_id:
                from mrp.orchestrator.player_control import PlayerSwitchConflict
                raise PlayerSwitchConflict("控制角色已经变化，草稿未发送，请刷新后确认身份")
            if client_message_id is not None:
                existing = next((m for m in self.r.state.messages if m.id == client_message_id), None)
                if existing is not None:
                    if existing.actor != "player":
                        raise RuntimeError("消息 ID 已被其他内容占用")
                    return [m for m in self.r.state.messages if m.turn == existing.turn]
            turn = self.r.state.current_turn() + 1
            self.r.runtime.turn_errors = []
            created: list[Message] = []
            input_group_id = f"input-{client_message_id}" if client_message_id else new_id("input")

            # ---- 通道分发 ----
            if channel == "inner":
                # 内心：只对自己可见，不触发任何角色回合
                msg = self.r._append_message(
                    Message(
                        id=client_message_id or new_id("msg"),
                        session_id=self.r.state.meta.id,
                        seq=self.r.state.next_seq(),
                        turn=turn,
                        actor="player",
                        content=content,
                        kind="inner",
                        visible_to=["player"],  # 反心灵感应：角色历史里没有这条
                        input_group_id=input_group_id,
                    )
                )
                live = execution.get()
                if live is not None:
                    await live.accepted([msg])
                else:
                    await self.r._emit("message.final", {"message": msg.model_dump(mode="json")})
                return [msg]

            # ---- R22 语法解析：故事写作的未标记内容默认为场景；两种公开模式均拆显式标记 ----
            parsed = parse_channels(content, default_kind="scene" if channel == "narration" else "roleplay")
            for pk, ptext in parsed:
                msg = self.r._append_message(
                    Message(
                        id=client_message_id if not created and client_message_id else new_id("msg"),
                        session_id=self.r.state.meta.id,
                        seq=self.r.state.next_seq(),
                        turn=turn,
                        actor="player",
                        content=ptext,
                        kind=pk,
                        visible_to=["player"] if pk == "inner" else visibility.compute_visible_to(self.r.state.characters, group_ids=visibility.active_group_ids(self.r.state)),
                        input_group_id=input_group_id,
                        mentions=list(mentions or []) if pk != "inner" else [],
                        reply_mode=reply_mode if pk != "inner" else None,
                    )
                )
                created.append(msg)
                if execution.get() is None:
                    await self.r._emit("message.final", {"message": msg.model_dump(mode="json")})
            live = execution.get()
            if live is not None:
                await live.accepted(created)
            return await self.execute_player_input(content, force_character, mentions, channel, turn, created, reply_mode)

    async def execute_player_input(self, content, force_character, mentions, channel, turn, created, reply_mode):
        parsed = [(message.kind, message.content) for message in created if message.actor == "player"]
        live = execution.get()
        if live is not None and live.run.plan is not None:
            return await self.execute_turn_plan(live.run.plan, turn, created)
        public_parts = [(kind, text) for kind, text in parsed if kind != "inner"]
        if not public_parts:
            return created
        route_text = " ".join(text for _, text in public_parts)
        route_channel = "dialogue" if any(kind == "roleplay" for kind, _ in public_parts) else "narration"

        # 1.5 R37.4 短输入垫场：≤4 字纯台词自动垫 1-2 句环境描写（先于路由落账，
        # 被路由角色的上下文自动包含）
        if live is None or not live.run.padding_prepared:
            pad_msg = await self.maybe_pad(content, channel, route_text, turn)
            if pad_msg is not None:
                created.append(pad_msg)
            if live is not None:
                await live.padding_done()

        # 2. 导演决策（R30 显式 mentions 最高优先；R35 LLM 导演占兜底槽位）
        active_groups, explicit_group_targets, group_targets, character_mentions = self.resolve_group_targets(
            route_text, turn, mentions
        )
        # The explicit recipient list is the authoritative ordered roster for
        # the new collaboration contract; older clients retain legacy routing.
        if reply_mode is not None and mentions:
            chars = self.r._characters_by_id()
            ordered = list(dict.fromkeys(mentions))
            ordered = [actor_id for actor_id in ordered if (
                (actor_id in chars and chars[actor_id].present and not chars[actor_id].muted)
                or actor_id in active_groups
            )]
            if len(ordered) >= 2:
                player_message = next((m for m in reversed(created) if m.actor == "player" and m.kind != "inner"), None)
                mode, order, reason, shared_scene = await self.decide_reply_plan(reply_mode, ordered, route_text)
                if player_message is not None:
                    player_message.executed_reply_mode = mode
                    player_message.reply_order = order
                    player_message.reply_reason = reason
                    player_message.reply_basis_fingerprint = player_message.fingerprint
                    player_message.reply_shared_scene = shared_scene
                await self.r._emit("reply.plan", {
                    "turn": turn, "mode": mode, "order": order, "reason": reason,
                    "basis_fingerprint": player_message.fingerprint if player_message else "",
                })
                if live is not None:
                    await live.prepare_plan({"kind": "ordered", "mode": mode, "order": order,
                        "shared_scene": shared_scene, "skip_scene_plan": reply_mode == "auto"})
                created.extend(await self.run_ordered_turn(order, chars, active_groups, turn, mode,
                                                          parallel_plan=shared_scene, skip_scene_plan=reply_mode == "auto"))
                return created
        decision = await self.r.director_flow.decide_with_hints(
            route_text, route_channel, turn,
            character_mentions or (explicit_group_targets if explicit_group_targets else None),
            force_character, group_mentions=group_targets,
        )

        plan = {"kind": "route", "decision": decision.model_dump(mode="json"),
                "group_targets": group_targets,
                "requires_confirmation": self.r.state.meta.director_mode == "confirm" and decision.trigger.startswith("llm")}
        if live is not None:
            await live.prepare_plan(plan)
        return await self.execute_turn_plan(plan, turn, created)

    async def execute_turn_plan(self, plan, turn, created):
        chars = self.r._characters_by_id()
        active_groups = {g.id: g for g in self.r.state.groups if g.status == "active"}
        if plan["kind"] == "ordered":
            created.extend(await self.run_ordered_turn(plan["order"], chars, active_groups, turn, plan["mode"],
                parallel_plan=plan.get("shared_scene", ""), skip_scene_plan=plan.get("skip_scene_plan", False)))
            return created
        from mrp.shared.models import DirectorDecision
        decision = DirectorDecision.model_validate(plan["decision"])
        group_targets = [gid for gid in plan.get("group_targets", []) if gid in active_groups]
        if plan.get("requires_confirmation"):
            for group_id in group_targets:
                try:
                    created.append(await self.run_group_turn(active_groups[group_id], turn))
                except Exception:
                    self.r.runtime.turn_errors.append(f"{active_groups[group_id].label}回应失败，请重试")
            self.r.pending_director = decision
            await self.r._emit("director.pending", {"decision": decision.model_dump(mode="json")})
            return created
        created.extend(await self.execute_routed_turn(decision, turn, group_targets, active_groups))
        return created

    async def decide_reply_plan(self, requested: str, ordered: list[str], player_text: str) -> tuple[str, list[str], str, str]:
        """Resolve explicit multi-actor mode; planner failures fail open to serial."""
        if requested in ("parallel", "serial"):
            return requested, list(ordered), "玩家选择并行" if requested == "parallel" else "按点选顺序接力", ""
        scene = self.r.active_scene()
        latest_player = next((m for m in reversed(self.r.state.messages) if m.turn == self.r.state.current_turn() and m.actor == "player" and m.kind != "inner"), None)
        recent = recent_public_scene_context(
            self.r.state, self.r.state.current_turn(), limit=8,
            exclude_message_id=latest_player.id if latest_player else None,
            viewer_ids=ordered,
        )
        candidates = []
        chars = self.r._characters_by_id()
        groups = {g.id: g for g in self.r.state.groups}
        for actor_id in ordered:
            candidates.append({"id": actor_id, "name": chars[actor_id].card.name if actor_id in chars else groups[actor_id].label})
        user = "\n".join([
            f"场景：{scene.title if scene else '未知'}；{scene.description if scene else ''}",
            "候选对象（id 不可更改）：" + json.dumps(candidates, ensure_ascii=False),
            "近期公开剧情：\n" + (recent or "（无）"),
            f"本轮玩家输入：{player_text}",
        ])
        try:
            text, usage = await asyncio.wait_for(asyncio.to_thread(
                chat_text_with_usage,
                [{"role": "system", "content": _REPLY_PLAN_SYSTEM}, {"role": "user", "content": user}],
                director_config(), max_tokens=240, timeout=8.0, no_thinking=True,
            ), timeout=8.5)
            self.track_purpose_cost("director", TokenUsage(**usage))
            data = extract_json(text)
            mode = data.get("mode") if isinstance(data, dict) else None
            order = data.get("order") if isinstance(data, dict) else None
            if mode not in ("parallel", "serial") or not isinstance(order, list) or len(order) != len(ordered) or set(order) != set(ordered):
                raise ValueError("自动安排结果无效")
            shared_scene = data.get("shared_scene") or ""
            if not isinstance(shared_scene, str):
                raise ValueError("共同场面结果无效")
            return mode, order, str(data.get("reason") or "辅助模型安排")[:200], shared_scene
        except Exception as exc:  # noqa: BLE001 — safe deterministic fallback
            return "serial", list(ordered), f"自动判断失败，已按点选顺序串行：{str(exc)[:100]}", ""

    async def run_ordered_turn(self, order, chars, groups, turn: int, mode: str, *, parallel_plan: str = "", skip_scene_plan: bool = False, raise_on_error: bool = False) -> list[Message]:
        if mode == "parallel":
            return await self.run_parallel_turn(order, chars, groups, turn,
                                                parallel_plan=parallel_plan, skip_scene_plan=skip_scene_plan,
                                                raise_on_error=raise_on_error)
        created: list[Message] = []
        for actor_id in order:
            try:
                if actor_id in chars:
                    message = await self.run_character_turn(
                        chars[actor_id], turn, reply_mode="serial", participants=order,
                    )
                else:
                    message = await self.run_group_turn(
                        groups[actor_id], turn, reply_mode="serial", participants=order,
                    )
                created.append(message)
            except Exception as exc:  # stop the chain, keep replies already committed
                label = chars[actor_id].card.name if actor_id in chars else groups[actor_id].label
                self.r.runtime.turn_errors.append(f"{label}回应失败，后续接力已停止：{str(exc)[:160]}")
                if raise_on_error:
                    raise
                break
        return created

    def resolve_group_targets(
        self, route_text: str, turn: int, mentions: list[str] | None,
    ) -> tuple[dict[str, GroupActor], list[str], list[str], list[str]]:
        """发送与重跑共用同一套群体路由，避免重跑只保留正式角色。"""
        current_scene = self.r.active_scene()
        active_groups = {
            group.id: group for group in self.r.state.groups
            if group.status == "active" and current_scene is not None
            and group.id in current_scene.group_ids
        }
        explicit_groups = list(dict.fromkeys(cid for cid in (mentions or []) if cid in active_groups))
        group_targets = list(explicit_groups)
        character_mentions = [cid for cid in (mentions or []) if cid not in active_groups]
        if not group_targets:
            action_words = ("靠近", "攻击", "交易", "求助", "朝", "递给", "叫住", "询问", "冲向", "看向", "呼喊", "挥", "举起", "对着")
            for group in active_groups.values():
                names = [group.label, *group.aliases]
                mentioned = any(name and name.casefold() in route_text.casefold() for name in names)
                if group.participation == "on_cue" and mentioned and any(word in route_text for word in action_words):
                    group_targets = [group.id]
                    break
            if not group_targets:
                occasional = [
                    group for group in active_groups.values()
                    if group.participation == "occasional"
                    and turn - (group.last_spoke_turn or 0) >= 3
                ]
                if occasional and self.r.runtime.rng(turn) % 4 == 0:
                    group_targets = [occasional[0].id]
        return active_groups, explicit_groups, group_targets, character_mentions

    async def execute_routed_turn(
        self, decision, turn: int, group_targets: list[str],
        active_groups: dict[str, GroupActor],
    ) -> list[Message]:
        """明确点名的多个对象独立并行；其他导演行为沿用现有流程。"""
        chars = self.r._characters_by_id()
        participants = [cid for cid in decision.chosen if cid in chars and chars[cid].present and not chars[cid].muted]
        participants.extend(gid for gid in group_targets if gid in active_groups and gid not in participants)
        if decision.action == "pick_speaker" and decision.trigger == "explicit_mention" and len(participants) > 1:
            return await self.run_parallel_turn(participants, chars, active_groups, turn)
        created = await self.r.director_flow.execute_decision(decision, turn)
        for group_id in group_targets:
            try:
                created.append(await self.run_group_turn(active_groups[group_id], turn))
            except Exception:  # noqa: BLE001 — 一个群体失败不抹掉其他回复
                logger.warning("group response failed (group=%s turn=%s)", group_id, turn, exc_info=True)
                self.r.runtime.turn_errors.append(f"{active_groups[group_id].label}回应失败，请重试")
        return created

    async def run_parallel_turn(
        self, participants: list[str], chars: dict[str, Character],
        groups: dict[str, GroupActor], turn: int, *, parallel_plan: str = "", skip_scene_plan: bool = False,
        raise_on_error: bool = False,
    ) -> list[Message]:
        """所有模型读取同一历史快照；完成事件各自推送，最终按预留序号落账。"""
        r = self.r
        snapshot = r.state.model_copy(deep=True)
        live = execution.get()
        if live is not None:
            snapshot = await live.parallel_baseline(snapshot)
        current_input_ids = {
            message.id for message in snapshot.messages
            if message.turn == turn and message.actor == "player"
            and message.kind in ("roleplay", "scene") and message.status == "final"
        }
        shared_frame = build_shared_scene_frame(
            snapshot, turn, exclude_message_ids=current_input_ids, include_world_core=False,
            viewer_ids=participants,
        )
        first_seq = r.state.next_seq()
        pending_messages = [
            Message(
                session_id=r.state.meta.id, seq=first_seq + index, turn=turn,
                actor=actor_id, content="", kind="roleplay", status="pending",
                scene_id=r.state.active_scene_id,
                visible_to=visibility.compute_visible_to(
                    r.state.characters, group_ids=visibility.active_group_ids(r.state)
                ),
            )
            for index, actor_id in enumerate(participants)
        ]
        for pending in pending_messages:
            if live is not None:
                continue  # The durable adapter announces its reserved generation.
            self.prepare_generation(pending)
            await r._emit("message.pending", {
                "message": pending.model_dump(mode="json"),
                "character_id": pending.actor, "turn": turn,
            })
        parallel_plan = parallel_plan or (
            "[本轮共同场面]\n"
            "以生成前的可见对话和已发生场景为共同事实；不预写任何参与者本轮将采取的行动或台词。"
            "群体建卡时的状态可能滞后；不要引用其他参与者尚未生成的回复，也不要凭空回到旧地点。"
        )
        if (not skip_scene_plan and r.group_responder is not None
                and hasattr(r.group_responder, "plan_parallel_scene")
                and any(actor_id in groups for actor_id in participants)
                and (live is None or not live.run.plan.get("parallel_effective_plan"))):
            names = {
                actor_id: chars[actor_id].card.name if actor_id in chars else groups[actor_id].label
                for actor_id in participants
            }
            try:
                planned, usage = await r.group_responder.plan_parallel_scene(snapshot, turn, names)
                if planned:
                    parallel_plan = planned
                if usage.get("input_tokens") or usage.get("output_tokens"):
                    self.track_purpose_cost("parallel_scene_plan", TokenUsage(**usage))
            except Exception:  # noqa: BLE001 — 调度失败不能阻断已点名角色的回复
                logger.warning("parallel scene planning failed (turn=%s)", turn, exc_info=True)
        if live is not None:
            if live.run.plan.get("parallel_effective_plan"):
                parallel_plan = live.run.plan["parallel_effective_plan"]
            else:
                live.guard()
                live.run.plan["parallel_effective_plan"] = parallel_plan
                async with live.checkpoint_lock:
                    await live.checkpoint()
        tasks = []
        for actor_id, pending in zip(participants, pending_messages):
            if actor_id in chars:
                visible = snapshot.visible_messages_for(actor_id)
                tasks.append(asyncio.create_task(self.run_character_turn(
                    chars[actor_id], turn, visible_override=visible,
                    pending_override=pending, pending_emitted=True, defer_commit=True,
                    shared_frame=shared_frame, parallel_plan=parallel_plan,
                    reply_mode="parallel", participants=participants,
                    context_state=snapshot,
                    emit_final=not raise_on_error,
                )))
            else:
                tasks.append(asyncio.create_task(self.run_group_turn(
                    groups[actor_id], turn, state_snapshot=snapshot,
                    pending_override=pending, pending_emitted=True, defer_commit=True,
                    parallel_plan=parallel_plan, reply_mode="parallel", participants=participants,
                    emit_final=not raise_on_error,
                )))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        if raise_on_error and any(isinstance(result, BaseException) for result in results):
            for pending in pending_messages:
                await r._emit("message.deleted", {"message_id": pending.id, "turn": turn})
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        created: list[Message] = []
        for actor_id, pending, result in zip(participants, pending_messages, results):
            if isinstance(result, BaseException):
                logger.warning("parallel response failed (actor=%s turn=%s): %s", actor_id, turn, result)
                label = chars[actor_id].card.name if actor_id in chars else groups[actor_id].label
                r.runtime.turn_errors.append(f"{label}回应失败，请重试")
                await r._emit("message.error", {
                    "message_id": pending.id, "turn": turn, "error": f"{label}回应失败，请重试",
                })
                continue
            if actor_id in groups:
                groups[actor_id].last_spoke_turn = turn
            created.append(result if live is not None else r._append_message(result))
        if raise_on_error:
            for message in created:
                await r._emit("message.final", {"message": message.model_dump(mode="json")})
        r.memory_pipeline.maybe_spawn_consolidation(turn, "interval")
        return created

    async def maybe_pad(self, content: str, channel: str, route_text: str, turn: int) -> Message | None:
        """R37.4 短输入垫场：≤4 字纯台词自动垫 1-2 句环境描写。fail-open 返回 None。"""
        if not (
            self.r.padding_gen is not None
            and self.r.state.meta.short_input_padding
            and channel == "dialogue"
            and len(route_text.strip()) <= 4
            and parse_channels(content) == [("roleplay", content)]
        ):
            return None
        scene = self.r.active_scene()
        recent = [m.content[:60] for m in self.r.state.messages[-4:]]
        try:
            pad = await asyncio.to_thread(
                self.r.padding_gen.generate,
                scene.title if scene else "",
                scene.description if scene else "",
                recent,
            )
        except Exception:  # noqa: BLE001 —— 双保险 fail-open：垫场绝不挡回合
            logger.warning("padding generation failed (turn=%s)", turn, exc_info=True)
            pad = ""
        if not pad:
            return None
        live = execution.get()
        if live is not None:
            live.guard()
        pad_msg = self.r._append_message(
            Message(
                session_id=self.r.state.meta.id,
                seq=self.r.state.next_seq(),
                turn=turn,
                actor="director",
                content=f"（{pad}）",
                kind="scene",
                visible_to=visibility.compute_visible_to(self.r.state.characters, group_ids=visibility.active_group_ids(self.r.state)),
            )
        )
        self.track_purpose_cost("padding", self.r.padding_gen.usage)
        if live is not None:
            await live.padding_done()
        await self.r._emit("message.final", {"message": pad_msg.model_dump(mode="json")})
        return pad_msg

    # ---------- 角色回合 ----------

    @durable_reply
    async def run_character_turn(
        self,
        character: Character,
        turn: int,
        *,
        target: Message | None = None,
        extra_injections: list | None = None,
        visible_override: list[Message] | None = None,
        pending_override: Message | None = None,
        pending_emitted: bool = False,
        defer_commit: bool = False,
        shared_frame: str = "",
        parallel_plan: str = "",
        reply_mode: str = "single",
        participants: list[str] | None = None,
        context_state: SessionState | None = None,
        provenance: GenerationProvenance | None = None,
        emit_final: bool = True,
        previous_candidate_content: str | None = None,
    ) -> Message:
        """单个角色的一次回合（每轮重组 + 每回合新 engine session）。

        target=None：正常回合（pending 新消息 → final 落账）。
        target=msg（R32 swipe）：原位更新该消息——重生成期间旧候选文本必须
        从其自身上下文中排除（design/v3/m8-r32 §3.3），否则模型"看到自己刚说完的话
        再重说一遍"，污染重roll。
        extra_injections：附加注入（R34 校验反馈重试用，追加在常规注入之后）。
        """
        r = self.r
        state = context_state or r.state
        if target is not None:
            pending_msg = target  # swipe：复用原消息行（同 id，前端 upsert 原地替换）
        elif pending_override is not None:
            pending_msg = pending_override
        else:
            pending_msg = Message(
                session_id=r.state.meta.id,
                seq=r.state.next_seq(),
                turn=turn,
                actor=character.id,
                content="",
                kind="roleplay",
                visible_to=visibility.compute_visible_to(r.state.characters, group_ids=visibility.active_group_ids(r.state)),
                status="pending",
            )
        self.prepare_generation(pending_msg)
        provenance = provenance or make_provenance(
            state, reply_mode, participants, shared_scene=parallel_plan, shared_frame=shared_frame,
        )
        if provenance.baseline_memory_watermark is None and getattr(state, "_generation_frozen", False):
            provenance.baseline_memory_watermark = getattr(state, "_generation_memory_watermark", None)
        if (provenance.baseline_memory_watermark is None and not getattr(state, "_generation_frozen", False)
                and r.memory is not None and hasattr(r.memory, "current_watermark")):
            provenance.baseline_memory_watermark = r.memory.current_watermark(state.meta.id)
        # A retry announces its new attempt before any offset-zero deltas.
        if not pending_emitted:
            await r._emit(
                "message.pending",
                {"message": pending_msg.model_dump(mode="json"), "character_id": character.id, "turn": turn},
            )
        try:
            visible = [
                m for m in (visible_override if visible_override is not None else state.visible_messages_for(character.id))
                if target is None or m.id != target.id
            ]
            # R36.3 远期历史压缩：超过 horizon 的已结束场景整块替换为摘要伪消息
            capacity = await r.context_builder.model_capacity(character, state=state)
            raw_visible = list(visible)
            visible = r.context_builder.compress_history(
                character, visible, input_limit=capacity.input_limit, state=state,
            )
            reply_frame = build_reply_frame(
                state, character.id, turn, reply_mode, visible, participants, provenance=provenance,
            )
            recall_audit = []
            injections = await r.context_builder.build_injections(character, turn, visible=visible,
                                                                  participants=participants, recall_audit=recall_audit,
                                                                  state=state)
            directive = getattr(state, "_conversation_directive", provenance.director_directive)
            if directive:
                injections.append(Injection(source="system", entry_id="conversation_directive",
                    content="[作者幕后要求；这不是人物听见的话，也不是已发生的事实]\n" + directive,
                    anchor="near", order=95, reason="当前有限对话的作者引导"))
            if shared_frame:
                injections.append(Injection(
                    source="system", entry_id="shared_scene_frame", content=shared_frame,
                    anchor="near", order=90, reason="同轮并行发言者共享的公开场景基线",
                ))
            if parallel_plan:
                injections.append(Injection(
                    source="system", entry_id="parallel_scene_plan", content=parallel_plan,
                    anchor="near", order=100, reason="同轮并行发言者共享的行动基线",
                ))
            if extra_injections:
                injections = [*injections, *extra_injections]
            ctx = TurnContext(
                session_id=r.state.meta.id,
                character_id=character.id,
                turn=turn,
                visible_messages=visible,
                actor_labels=reply_frame.actor_labels,
                reply_frame=reply_frame,
                injections=injections,
                budget_tokens=capacity.input_limit,
                capacity_source=capacity.source,
                capacity_fetched_at=capacity.fetched_at,
                model_id=character.llm.model,
                gateway=character.llm.base_url,
                model_provider=character.llm.effective_provider,
                context_limit=capacity.context_limit,
                output_reserve=capacity.output_reserve,
                world_core_brief=state.meta.world_core_brief,
                world_runtime_policy=state.meta.world_runtime_policy,
                player_persona=state.meta.player_persona,
                player_identity_source=identity_source(state),
                reply_max_tokens=state.meta.reply_max_tokens,
                style=r.context_builder.narrative_style(),
                prompt_transforms=r.context_builder.prompt_transforms(state=state),
                message_id=pending_msg.id, generation_id=pending_msg.generation_id,
                operation_id=pending_msg.operation_id, attempt_id=pending_msg.attempt_id,
                provenance=provenance,
            )
            plan = plan_context(ctx, model=character.llm.model, persona=persona_from_card(character.card, player_name_for_context(ctx)))
            composed = plan.prompt
            ctx.planned_prompt = composed
            ctx.plan_id = plan.plan_id
            object.__setattr__(ctx, "_generation_state", state)
            from mrp.orchestrator.memory_recall import finish_recall_audit
            finish_recall_audit(recall_audit, composed.included_entry_ids, composed.omitted_reasons)
            provenance.baseline_state = r.context_builder.generation_material_projection(state=state)
            ctx.provenance = finish_provenance(provenance, state, raw_visible, recall_audit=recall_audit,
                include_player_inner=any(i.entry_id == "player_inner_subtext" for i in injections))
            r.runtime.inspections[(character.id, turn)] = {"ctx": ctx, "composed": composed, "memory_recall": recall_audit}

        except Exception:
            await r._emit("message.error", {"message_id": pending_msg.id, "turn": turn,
                                            "error": "回复上下文准备失败，请重试", "discard_pending": target is None})
            raise
        bridge = (
            self.make_delta_bridge(pending_msg.id, character.id, turn)
            if r.state.meta.streaming_enabled
            else None
        )
        try:
            reply, emitted = await self.generate_with_bridge(character, ctx, bridge)
        except Exception:
            # 引擎失败：撤销前端 pending 气泡（全新 pending 无旧内容，前端直接移除）
            await r._emit(
                "message.error",
                {"message_id": pending_msg.id, "turn": turn, "error": "生成失败，请重试"},
            )
            raise

        # R34 输出卫生：校验 → 违规则带反馈重试一次（同一 pending 槽内无缝完成，
        # delta 的 offset 截断规则自然接管重写）
        hygiene = None
        active_ctx = ctx
        if r.hygiene_active:
            try:
                hygiene = await self.run_hygiene_check(character, ctx, reply.content)
            except Exception:  # noqa: BLE001 —— 双保险 fail-open：编排层也不让校验挡回合
                logger.warning("hygiene check failed (character=%s turn=%s)", character.id, turn, exc_info=True)
                hygiene = None
            if hygiene is not None and not hygiene.passed:
                feedback = Injection(
                    source="system",
                    entry_id="hygiene_feedback",
                    content=self.hygiene_feedback_text(hygiene),
                    anchor="system",
                    order=300,  # 紧贴对话之前（仅次于 inner 潜台词）
                )
                retry_ctx = ctx.model_copy(
                    update={"injections": [*ctx.injections, feedback], "planned_prompt": None, "plan_id": None}
                )
                await self.announce_attempt(pending_msg, retry_ctx)
                first_hygiene_usage = reply.usage
                retry_plan = plan_context(retry_ctx, model=character.llm.model, persona=persona_from_card(character.card, player_name_for_context(retry_ctx)))
                retry_ctx.planned_prompt = retry_plan.prompt
                retry_ctx.plan_id = retry_plan.plan_id
                retry_bridge = (
                    self.make_delta_bridge(pending_msg.id, character.id, turn)
                    if r.state.meta.streaming_enabled
                    else None
                )
                try:
                    reply, emitted = await self.generate_with_bridge(
                        character, retry_ctx, retry_bridge
                    )
                except Exception:
                    await r._emit(
                        "message.error",
                        {"message_id": pending_msg.id, "turn": turn, "error": "生成失败，请重试"},
                    )
                    raise
                composed = retry_plan.prompt
                reply = reply.model_copy(update={"usage": TokenUsage(
                    input_tokens=first_hygiene_usage.input_tokens + reply.usage.input_tokens,
                    output_tokens=first_hygiene_usage.output_tokens + reply.usage.output_tokens,
                    cached_tokens=first_hygiene_usage.cached_tokens + reply.usage.cached_tokens,
                )})
                active_ctx = retry_ctx
                r.runtime.inspections[(character.id, turn)] = {"ctx": retry_ctx, "composed": composed}
                try:
                    report2 = await self.run_hygiene_check(character, retry_ctx, reply.content)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "hygiene recheck failed (character=%s turn=%s)", character.id, turn, exc_info=True
                    )
                    report2 = None
                if report2 is not None:
                    hygiene = report2.model_copy(
                        update={"attempts": 2, "corrected": report2.passed}
                    )
                    if report2.passed:
                        r.runtime.hygiene_stats["auto_corrected"] += 1

        # 上游偶尔会把上一轮的整段回答原样返回。流式文本此时已经显示在
        # pending 气泡中，重试桥从 offset=0 开始，前端会用新回答覆盖旧片段。
        previous_reply = next((
            message for message in reversed(state.messages)
            if message.actor == character.id and message.status == "final"
            and message.id != pending_msg.id
        ), None)
        duplicate_baselines = [previous_reply.content] if previous_reply is not None else []
        # Swipe excludes the replaced message from the prompt, but its prior
        # candidate is still a useful copy baseline for the new output.
        if target is not None:
            duplicate_baselines.append(target.content)
        if previous_candidate_content is not None:
            duplicate_baselines.append(previous_candidate_content)
        if any(is_duplicate_reply(reply.content, old) for old in duplicate_baselines):
            logger.warning(
                "consecutive duplicate reply; retrying once (session=%s character=%s turn=%s)",
                r.state.meta.id, character.id, turn,
            )
            duplicate_feedback = Injection(
                source="system",
                entry_id="duplicate_reply_feedback",
                content=(
                    "刚生成的回复几乎照抄了该角色已有的整段回复，只改标点或少数字词也属于重复。"
                    "请针对本轮最新的玩家消息写出新的回应，推进当前对话；"
                    "以最新输入中已经改变的行动、问题和现场状况为准，不要复述已有回答。只输出角色回复正文。"
                ),
                anchor="near",
                order=310,
            )
            retry_ctx = active_ctx.model_copy(update={
                "injections": [*active_ctx.injections, duplicate_feedback],
                "planned_prompt": None, "plan_id": None,
            })
            await self.announce_attempt(pending_msg, retry_ctx)
            first_usage = reply.usage
            try:
                retry_plan = plan_context(
                    retry_ctx, model=character.llm.model,
                    persona=persona_from_card(character.card, player_name_for_context(retry_ctx)),
                )
                retry_ctx.planned_prompt = retry_plan.prompt
                retry_ctx.plan_id = retry_plan.plan_id
                retry_bridge = (
                    self.make_delta_bridge(pending_msg.id, character.id, turn)
                    if r.state.meta.streaming_enabled else None
                )
                replacement, emitted = await self.generate_with_bridge(
                    character, retry_ctx, retry_bridge
                )
                if any(is_duplicate_reply(replacement.content, old) for old in duplicate_baselines):
                    raise RuntimeError("上游连续两次返回了相同回复，请稍后重新生成")
            except Exception:
                await r._emit("message.error", {
                    "message_id": pending_msg.id, "turn": turn,
                    "error": "上游重复返回上一轮回复，自动重试失败，请重新生成",
                    "discard_pending": True,
                })
                raise
            reply = replacement.model_copy(update={"usage": TokenUsage(
                input_tokens=first_usage.input_tokens + replacement.usage.input_tokens,
                output_tokens=first_usage.output_tokens + replacement.usage.output_tokens,
                cached_tokens=first_usage.cached_tokens + replacement.usage.cached_tokens,
            )})
            composed = retry_plan.prompt
            active_ctx = retry_ctx
            r.runtime.inspections[(character.id, turn)] = {
                "ctx": retry_ctx, "composed": composed,
            }
            if r.hygiene_active:
                try:
                    hygiene = await self.run_hygiene_check(character, retry_ctx, reply.content)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "duplicate retry hygiene check failed (character=%s turn=%s)",
                        character.id, turn, exc_info=True,
                    )
                    hygiene = None

        # R33/R48 流式：真流式引擎已逐片发出（去重靠 already_emitted）；DSH 忽略
        # on_delta → emitted=0 → 行为与 v3 伪流式一致（开关关 = 与 v2 一致）
        if r.state.meta.streaming_enabled:
            await self.emit_deltas(
                pending_msg, character.id, turn, reply.content, already_emitted=emitted
            )

        meta = GenerationMeta(
            message_id=pending_msg.id, generation_id=pending_msg.generation_id,
            operation_id=pending_msg.operation_id, attempt_id=active_ctx.attempt_id,
            request_ids=active_ctx.request_ids, plan_id=active_ctx.plan_id,
            provenance=active_ctx.provenance,
            finish_reason=reply.finish_reason,
            completion_state=completion_state(reply.finish_reason),
            engine_session_id=reply.engine_session_id,
            model=character.llm.model,
            usage=reply.usage,
            injected_entry_ids=composed.included_entry_ids,
            memory_recall=recall_audit,
            response_style=next((json.loads(i.reason) for i in active_ctx.injections
                                 if i.entry_id.startswith("response_style:")), None),
            prompt_tokens_by_section=composed.tokens_by_section,
        )
        if target is not None:
            # R32.1 原位 variants：旧候选物化 → 追加新候选 → 镜像到顶层字段
            await r.message_ops.invalidate_memory_sources({target.id})
            if not target.variants:
                target.variants = [MessageVariant(
                    content=target.content,
                    generation_meta=target.generation_meta,
                    hygiene=target.hygiene,
                )]
            target.variants.append(
                MessageVariant(content=reply.content, generation_meta=meta, hygiene=hygiene)
            )
            target.active_variant = len(target.variants) - 1
            target.content = reply.content
            target.generation_meta = meta
            target.hygiene = hygiene
            target.fingerprint = fingerprint(target.actor, target.seq, target.content)
            target.status = "final"
            r._record_message_revision(target)
            msg = target
        else:
            final_message = Message(
                    id=pending_msg.id,
                    session_id=r.state.meta.id,
                    seq=pending_msg.seq,
                    turn=turn,
                    actor=character.id,
                    content=reply.content,
                    kind="roleplay",
                    visible_to=visibility.compute_visible_to(r.state.characters, group_ids=visibility.active_group_ids(r.state)),
                    status="final",
                    generation_meta=meta,
                    hygiene=hygiene,
                    generation_id=pending_msg.generation_id,
                    operation_id=pending_msg.operation_id, attempt_id=active_ctx.attempt_id,
            )
            final_message.scene_id = pending_msg.scene_id or r.state.active_scene_id
            msg = final_message if defer_commit else r._append_message(final_message)
        self.store_generation_inspection(msg, active_ctx, composed, recall_audit)
        if emit_final:
            await r._emit("message.final", {"message": msg.model_dump(mode="json")})
        await r._emit(
            "cost.update",
            {"cost_by_model": r.runtime.cost_by_model, "by_purpose": r.runtime.cost_by_purpose},
        )
        return msg

    async def generate_group_with_bridge(
        self, state: SessionState, group: GroupActor, pending: Message,
        *, exclude_message_id: str | None = None, feedback: str = "",
        parallel_plan: str = "", reply_mode: str = "single",
        participants: list[str] | None = None,
        provenance: GenerationProvenance | None = None,
    ):
        """共享网关的群体流式输出与正式角色使用同一条 SSE 桥。"""
        bridge = (
            self.make_delta_bridge(pending.id, group.id, pending.turn)
            if self.r.state.meta.streaming_enabled else None
        )
        try:
            shared_context_args = (
                {"context_builder": self.r.context_builder, "generation": {
                    "message_id": pending.id, "generation_id": pending.generation_id,
                    "operation_id": pending.operation_id, "attempt_id": pending.attempt_id,
                    "provenance": provenance,
                }}
                if getattr(self.r.group_responder, "uses_shared_context_builder", False)
                else {}
            )
            content, usage, trace = await self.r.group_responder.generate(
                state, group, exclude_message_id=exclude_message_id,
                feedback=feedback, on_delta=bridge.feed if bridge is not None else None,
                parallel_plan=parallel_plan, reply_mode=reply_mode,
                participants=participants, **shared_context_args,
            )
            settings = self.r.app_settings
            model = settings.model if settings else "shared-model"
            typed_usage = TokenUsage(**usage)
            usage_ctx = trace.get("ctx")
            self.track_cost(group.id, model, typed_usage, ctx=usage_ctx)
            self.track_purpose_cost("group_response", typed_usage, generation_id=getattr(usage_ctx, "generation_id", None))
        except Exception as exc:
            if getattr(exc, "usage", None):
                value = exc.usage if isinstance(exc.usage, TokenUsage) else TokenUsage(**exc.usage)
                settings = self.r.app_settings
                self.track_cost(group.id, settings.model if settings else "shared-model", value)
                exc._conversation_usage_charged = True
            raise
        finally:
            emitted = await bridge.close() if bridge is not None else 0
        return content, usage, trace, emitted

    @durable_reply
    async def run_group_turn(
        self, group: GroupActor, turn: int, *, target: Message | None = None,
        idempotency_key: str | None = None,
        state_snapshot: SessionState | None = None,
        pending_override: Message | None = None,
        pending_emitted: bool = False,
        defer_commit: bool = False,
        parallel_plan: str = "",
        reply_mode: str = "single",
        participants: list[str] | None = None,
        provenance: GenerationProvenance | None = None,
        emit_final: bool = True,
    ) -> Message:
        """One shared-model reply under the already-held session turn lock."""
        r = self.r
        scene = r.active_scene()
        if (r.group_responder is None or scene is None or group.status != "active"
                or group.scene_id != scene.id or group.id not in scene.group_ids):
            raise ValueError("群体已离开当前场景，无法回应")
        pending = target or pending_override or Message(
            session_id=r.state.meta.id, seq=r.state.next_seq(), turn=turn,
            actor=group.id, content="", kind="roleplay",
            idempotency_key=idempotency_key,
            visible_to=visibility.compute_visible_to(
                r.state.characters, group_ids=visibility.active_group_ids(r.state)
            ), status="pending",
        )
        self.prepare_generation(pending)
        provenance = provenance or make_provenance(
            state_snapshot or r.state, reply_mode, participants, shared_scene=parallel_plan,
        )
        context_state = state_snapshot or r.state
        if provenance.baseline_memory_watermark is None and getattr(context_state, "_generation_frozen", False):
            provenance.baseline_memory_watermark = getattr(context_state, "_generation_memory_watermark", None)
        if (provenance.baseline_memory_watermark is None and not getattr(context_state, "_generation_frozen", False)
                and r.memory is not None and hasattr(r.memory, "current_watermark")):
            provenance.baseline_memory_watermark = r.memory.current_watermark(context_state.meta.id)
        if not pending_emitted:
            await r._emit("message.pending", {"message": pending.model_dump(mode="json"), "character_id": group.id, "turn": turn})
        try:
            content, usage, trace, emitted = await self.generate_group_with_bridge(
                state_snapshot or r.state, group, pending,
                exclude_message_id=target.id if target else None,
                parallel_plan=parallel_plan, reply_mode=reply_mode,
                participants=participants,
                provenance=provenance,
            )
            if not content:
                raise RuntimeError("群体回应为空")
        except Exception:
            await r._emit("message.error", {"message_id": pending.id, "turn": turn, "error": "群体回应生成失败，请重试"})
            raise
        token_sections = trace["tokens_by_section"]
        self.store_group_inspection(group, turn, trace)
        hygiene = None
        if r.hygiene_active:
            try:
                hygiene = await self.run_group_hygiene_check(group, trace, content, state=context_state)
            except Exception:  # noqa: BLE001 — hygiene failure never blocks a group reply
                logger.warning("group hygiene check failed (group=%s turn=%s)", group.id, turn, exc_info=True)
            if hygiene is not None and not hygiene.passed:
                try:
                    first_usage = usage
                    await self.announce_attempt(pending)
                    content, usage, trace, emitted = await self.generate_group_with_bridge(
                        state_snapshot or r.state, group, pending,
                        exclude_message_id=target.id if target else None,
                        feedback=self.group_hygiene_feedback_text(hygiene),
                        parallel_plan=parallel_plan, reply_mode=reply_mode,
                        participants=participants,
                        provenance=provenance,
                    )
                    if not content:
                        raise RuntimeError("群体回应重试为空")
                    usage = {
                        key: usage.get(key, 0) + first_usage.get(key, 0)
                        for key in ("input_tokens", "output_tokens", "cached_tokens")
                    }
                    token_sections = trace["tokens_by_section"]
                    self.store_group_inspection(group, turn, trace)
                    first_report = hygiene.model_copy(update={"attempts": 2, "corrected": False})
                    try:
                        report2 = await self.run_group_hygiene_check(group, trace, content, state=context_state)
                    except Exception:  # noqa: BLE001
                        logger.warning("group hygiene recheck failed (group=%s turn=%s)", group.id, turn, exc_info=True)
                        report2 = None
                    if report2 is not None:
                        hygiene = report2.model_copy(update={"attempts": 2, "corrected": report2.passed})
                        if report2.passed:
                            r.runtime.hygiene_stats["auto_corrected"] += 1
                    else:
                        hygiene = first_report
                except Exception:
                    await r._emit("message.error", {"message_id": pending.id, "turn": turn, "error": "群体回应重试失败，请重试"})
                    raise
        if r.state.meta.streaming_enabled:
            await self.emit_deltas(pending, group.id, turn, content, already_emitted=emitted)
        settings = getattr(getattr(r.group_responder, "container", None), "settings", None)
        meta = GenerationMeta(
            finish_reason=trace.get("finish_reason"),
            completion_state=completion_state(trace.get("finish_reason")),
            message_id=pending.id, generation_id=pending.generation_id,
            operation_id=pending.operation_id, attempt_id=pending.attempt_id,
            request_ids=trace["ctx"].request_ids if trace.get("ctx") else [],
            plan_id=trace["ctx"].plan_id if trace.get("ctx") else None,
            provenance=(trace["ctx"].provenance if trace.get("ctx") else
                        finish_provenance(provenance, state_snapshot or r.state,
                                          trace.get("visible_messages", []),
                                          recall_audit=trace.get("memory_recall", []), include_player_inner=False)),
            engine_session_id=f"{group.id}-{pending.id}",
            model=settings.model if settings else "shared-model",
            usage=usage,
            injected_entry_ids=list(dict.fromkeys([
                group.id, f"source:{group.source.get('archive_id', 'manual')}",
                *trace.get("injected_entry_ids", trace.get("memory_ids", [])),
                *(["shared_scene_frame"] if trace.get("shared_scene_frame") else []),
                *(["recent_public_context"] if trace.get("recent_story") else []),
                *(["parallel_scene_plan"] if trace.get("parallel_scene_plan") else []),
            ])),
            prompt_tokens_by_section=token_sections,
            response_style=trace.get("response_style"),
            memory_recall=trace.get("memory_recall", []),
        )
        if not defer_commit:
            group.last_spoke_turn = turn
        if target is not None:
            if not target.variants:
                target.variants = [MessageVariant(
                    content=target.content,
                    generation_meta=target.generation_meta,
                    hygiene=target.hygiene,
                )]
            target.variants.append(MessageVariant(content=content, generation_meta=meta, hygiene=hygiene))
            target.active_variant = len(target.variants) - 1
            target.content = content
            target.generation_meta = meta
            target.hygiene = hygiene
            target.status = "final"
            target.fingerprint = fingerprint(target.actor, target.seq, target.content)
            r._record_message_revision(target)
            message = target
        else:
            final_message = Message(
                id=pending.id, session_id=r.state.meta.id, seq=pending.seq, turn=turn,
                actor=group.id, content=content, kind="roleplay",
                visible_to=visibility.compute_visible_to(
                    r.state.characters, group_ids=visibility.active_group_ids(r.state)
                ), status="final", generation_meta=meta, hygiene=hygiene,
                generation_id=pending.generation_id,
                operation_id=pending.operation_id, attempt_id=pending.attempt_id,
            )
            final_message.scene_id = pending.scene_id or r.state.active_scene_id
            message = final_message if defer_commit else r._append_message(final_message)
        if trace.get("ctx") and trace.get("composed"):
            self.store_generation_inspection(message, trace["ctx"], trace["composed"], trace.get("memory_recall", []))
        else:
            cached = self.r.runtime.inspections.get((group.id, turn))
            if cached:
                for key in ("message_id", "generation_id", "operation_id", "attempt_id"):
                    setattr(cached["ctx"], key, getattr(meta, key))
                cached["ctx"].provenance = meta.provenance
            self.r.runtime.inspections.record_generation(message.id, pending.generation_id,
                cached)
        if emit_final:
            await r._emit("message.final", {"message": message.model_dump(mode="json")})
        await r._emit("cost.update", {"cost_by_model": r.runtime.cost_by_model, "by_purpose": r.runtime.cost_by_purpose})
        return message

    def store_group_inspection(self, group: GroupActor, turn: int, trace: dict) -> None:
        r = self.r
        if trace.get("ctx") is not None and trace.get("composed") is not None:
            r.runtime.inspections[(group.id, turn)] = {
                "ctx": trace["ctx"], "composed": trace["composed"],
                "memory_recall": trace.get("memory_recall", []),
            }
            return
        injection_id = trace["source_id"]
        injections = [Injection(
            source="system", entry_id=injection_id,
            content=trace["group_details"],
            reason="本群体的公开设定与来源快照",
        )]
        for entry_id, field, reason in (
            ("shared_scene_frame", "shared_scene_frame", "同轮并行发言者共享的公开场景基线"),
            ("recent_public_context", "recent_story", "当前场景最近的公开剧情"),
            ("parallel_scene_plan", "parallel_scene_plan", "同轮并行发言者共享的行动基线"),
        ):
            if trace.get(field):
                injections.append(Injection(
                    source="system", entry_id=entry_id, content=trace[field],
                    anchor="near", reason=reason,
                ))
        if trace.get("break_armor_prompt"):
            injections.append(Injection(
                source="system", entry_id="break_armor_prompt",
                content=f"[破甲 Prompt]\n{trace['break_armor_prompt']}",
                reason="故事开局或达到设定的重注入间隔", order=1200,
            ))
        injections.extend(Injection.model_validate(i) for i in trace.get("memory_injections", []))
        context = TurnContext(
            session_id=r.state.meta.id,
            character_id=group.id,
            turn=turn,
            visible_messages=trace["visible_messages"],
            actor_labels=trace.get("actor_labels", {}),
            reply_frame=trace.get("reply_frame"),
            injections=injections,
            budget_tokens=trace.get("input_limit"),
            capacity_source=trace.get("capacity_source", "unknown"),
            context_limit=trace.get("context_limit"),
            output_reserve=trace.get("output_reserve"),
        )
        composed = ComposedPrompt(
            text=f"[系统上下文]\n{trace['system']}\n\n{trace['user']}",
            tokens_by_section=trace["tokens_by_section"],
            total_tokens=sum(trace["tokens_by_section"].values()),
            included_entry_ids=[item.entry_id for item in injections],
            included_message_ids=[item.id for item in trace["visible_messages"]],
        )
        r.runtime.inspections[(group.id, turn)] = {"ctx": context, "composed": composed, "memory_recall": trace.get("memory_recall", [])}

    async def run_group_hygiene_check(self, group: GroupActor, trace: dict, reply_content: str, *, state=None):
        """群体专用输出审查：允许群体内部台词，但保护玩家与正式角色。"""
        from mrp.orchestrator.hygiene import JudgeInput
        from mrp.shared.prompt import player_name_from_persona

        r = self.r
        state = state or r.state
        scene = next((item for item in state.scenes if item.id == state.active_scene_id), None)
        present = [
            character.card.name for character in state.characters
            if scene and character.id in scene.member_ids and character.present
        ]
        public_texts = [
            message.content for message in trace["visible_messages"]
            if message.actor == "player" and message.kind == "roleplay"
        ][-2:]
        data = JudgeInput(
            reply=reply_content,
            character_name=group.label,
            player_name=player_name_from_persona(state.meta.player_persona),
            present_characters=present,
            player_public_texts=public_texts,
            group_response=True,
            addressed_inputs=trace['reply_frame'].addressed_inputs if trace.get('reply_frame') else [],
            other_addressed_inputs=trace['reply_frame'].other_addressed_inputs if trace.get('reply_frame') else [],
            identity_source=group.public_brief,
        )
        report = await asyncio.to_thread(r.hygiene_judge.judge, data)
        r.runtime.hygiene_stats["checks"] += 1
        if not report.passed:
            r.runtime.hygiene_stats["violations_found"] += 1
        self.track_judge_cost(report.usage)
        return report

    @staticmethod
    def group_hygiene_feedback_text(report) -> str:
        names = {"C1": "代替玩家行动", "C2": "代替正式角色行动或说话", "C4": "出戏或元话语"}
        lines = "\n".join(
            f"- {names.get(item.category, item.category)}：你写了「{item.evidence}」"
            for item in report.violations
        )
        return (
            "[上一版群体回应被审查，请重写]\n"
            f"问题：\n{lines}\n\n"
            "只写这个群体内部成员可观察的集体动作与短对白；不要替玩家或正式角色做决定、说话或行动。"
            "群体内部未命名成员之间可以有不同反应。只输出故事正文。"
        )

    # ---------- 生成 / 流式 ----------

    async def generate_with_bridge(self, character, ctx, bridge: _DeltaBridge | None):
        """调引擎（有桥则透传 on_delta）；无论成败都关桥，返回 (reply, 已发字数)。"""
        try:
            reply = await self.r.engines.generate(
                character, ctx, on_delta=bridge.feed if bridge is not None else None
            )
            # Charge each successful upstream call before any story commit or
            # output retry can fail. The request archive retains every attempt.
            for value in reply.usage_calls or [reply.usage]:
                self.track_cost(character.id, character.llm.model, value, ctx=ctx)
        except Exception as exc:
            if getattr(exc, "usage", None):
                value = exc.usage if isinstance(exc.usage, TokenUsage) else TokenUsage(**exc.usage)
                for known in getattr(exc, "usage_calls", None) or [value]:
                    known = known if isinstance(known, TokenUsage) else TokenUsage(**known)
                    self.track_cost(character.id, character.llm.model, known, ctx=ctx)
                exc._conversation_usage_charged = True
            raise
        finally:
            emitted = await bridge.close() if bridge is not None else 0
        return reply, emitted

    def make_delta_bridge(
        self, message_id: str, character_id: str, turn: int, initial_offset: int = 0
    ) -> _DeltaBridge:
        return _DeltaBridge(self.r._emit, message_id, character_id, turn, initial_offset)

    async def emit_deltas(
        self, msg: Message, character_id: str, turn: int, content: str,
        already_emitted: int = 0,
    ) -> None:
        """R33 伪流式：content 未发出部分切片为 message.delta（lossy 发布）。

        already_emitted 留给未来真流式（引擎已回调发出的前缀不重发，见
        design/v3/m8-r33 §2）。final 永远是权威整包——delta 丢了不影响正确性。
        """
        text = content[already_emitted:]
        offset = already_emitted
        for i in range(0, len(text), PSEUDO_STREAM_CHUNK):
            piece = text[i : i + PSEUDO_STREAM_CHUNK]
            await self.r._emit(
                "message.delta",
                {
                    "message_id": msg.id,
                    "turn": turn,
                    "character_id": character_id,
                    "delta": piece,
                    "offset": offset,
                    "delivery": "replay",
                },
                lossy=True,
            )
            offset += len(piece)

    # ---------- R34 输出卫生 ----------

    async def run_hygiene_check(self, character: Character, ctx, reply_content: str):
        """校验一次回复（to_thread 包裹同步 httpx；judge 自身 fail-open）。"""
        data = self.build_judge_input(character, ctx, reply_content)
        report = await asyncio.to_thread(self.r.hygiene_judge.judge, data)
        r = self.r
        r.runtime.hygiene_stats["checks"] += 1
        if not report.passed:
            r.runtime.hygiene_stats["violations_found"] += 1
        self.track_judge_cost(report.usage)
        return report

    def build_judge_input(self, character: Character, ctx, reply_content: str):
        """C1/C2/C3 证据源取数（口径对齐 build_injections 的 inner 选取）。"""
        from mrp.orchestrator.hygiene import JudgeInput

        r = self.r
        from mrp.shared.prompt import player_name_from_persona
        state = getattr(ctx, "_generation_state", r.state)
        player_name = player_name_from_persona(state.meta.player_persona)
        inner_texts = [
            m.content for m in state.messages
            if m.actor == "player" and m.kind == "inner" and m.status == "final"
            and current_inner(state, m)
        ][-2:]
        public_texts = [
            m.content for m in ctx.visible_messages
            if m.actor == "player" and m.kind in ("roleplay",) and m.status == "final"
            and current_inner(state, m)
        ][-2:]
        present = [
            c.card.name for c in state.characters
            if c.present and c.id != character.id
        ]
        return JudgeInput(
            reply=reply_content,
            character_name=character.card.name,
            player_name=player_name,
            present_characters=present,
            player_inner_texts=inner_texts,
            player_public_texts=public_texts,
            addressed_inputs=ctx.reply_frame.addressed_inputs if ctx.reply_frame else [],
            other_addressed_inputs=ctx.reply_frame.other_addressed_inputs if ctx.reply_frame else [],
            identity_source=character.card.description,
        )

    @staticmethod
    def hygiene_feedback_text(report) -> str:
        """违规反馈注入文本（重试 prompt 的一部分）。"""
        names = {"C1": "代替玩家言行", "C2": "代替其他角色发言",
                 "C3": "心灵感应（回应玩家未表露的内心）", "C4": "出戏（OOC/元话语）"}
        lines = "\n".join(
            f"- {names.get(v.category, v.category)}：你写了「{v.evidence}」"
            for v in report.violations
        )
        return (
            "[上一版回复被系统审查驳回，请重写]\n"
            f"违规内容：\n{lines}\n\n"
            "重写要求：只以你自己的角色身份输出言行；不要代替玩家或其他角色说话；"
            "不要回应玩家未表露的想法；不要输出任何系统说明。"
        )

    # ---------- 成本 ----------

    def track_cost(self, character_id: str, model: str, usage, *, ctx=None) -> None:
        from mrp.orchestrator.conversation_context import charge
        charge(usage, "generation")
        from mrp.orchestrator.usage import record_usage
        record_usage(self.r, "generation", usage, model=model, character_id=character_id, ctx=ctx)

    def track_purpose_cost(self, purpose: str, usage, *, generation_id=None) -> None:
        from mrp.orchestrator.conversation_context import charge
        from mrp.orchestrator.usage import ALIASES, record_usage, label_main_call
        # These labels describe calls already charged by track_cost.
        if purpose in ALIASES:
            label_main_call(self.r, purpose, generation_id=generation_id)
            return
        charge(usage, purpose)
        settings = self.r.app_settings
        model = getattr(settings, "auxiliary_model", "") or getattr(settings, "model", "")
        record_usage(self.r, purpose, usage, model=model)

    # 兼容别名（R34 既有调用点）
    def track_judge_cost(self, usage) -> None:
        self.track_purpose_cost("judge", usage)
