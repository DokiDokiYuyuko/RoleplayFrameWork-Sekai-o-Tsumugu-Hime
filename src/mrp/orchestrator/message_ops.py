"""消息操作：swipe/编辑/续写/候选切换/删除/重跑/点名/在场/开场（W3 拆分自 session.py）。

本模块携带两条审计修复：
- B20：`swipe` 不再自打 `message.pending`——统一由 `TurnEngine.run_character_turn`
  的单一漏斗发出（避免同一次重roll 前端收到两条 pending；payload 逐字节一致）；
- B21：`set_presence` 纳入 `turn_lock`——在场名单变更不再与进行中的回合竞争
  （`_execute_decision` 遍历 chosen 时读到的 present 快照因此稳定）。
"""
from __future__ import annotations

from mrp.orchestrator.completion import completion_state

import asyncio
import logging

from mrp.orchestrator import visibility
from mrp.orchestrator.context_plan import plan_context
from mrp.orchestrator.generation_sources import make_provenance, finish_provenance
from mrp.orchestrator.memory_recall import finish_recall_audit
from mrp.orchestrator.worldline_state import reconcile_revisions
from mrp.shared.actor_labels import build_actor_labels
from mrp.shared.models import (
    GenerationMeta,
    Injection,
    Message,
    MessageVariant,
    TurnContext,
    fingerprint,
    new_id,
)
from mrp.shared.prompt import persona_from_card, player_name_for_context, player_name_from_persona, expand_prompt_macros
from mrp.shared.player_identity import before_control_boundary, identity_source

logger = logging.getLogger(__name__)


class MessageEditConflict(Exception):
    """The branch or target message changed after the editor was opened."""


class MessageOps:
    """一个 SessionRunner 的消息操作集（无状态，一律实时读 runner）。"""

    def __init__(self, runner) -> None:
        self.r = runner

    async def invalidate_memory_sources(self, message_ids: set[str]) -> list[str]:
        from mrp.application.branch_commit import stage_memory_invalidation
        if stage_memory_invalidation(self.r, message_ids):
            return []
        memory = self.r.memory
        if memory is None or not hasattr(memory, "invalidate_sources"):
            return []
        return await asyncio.to_thread(
            memory.invalidate_sources, self.r.state.meta.id, message_ids
        )

    async def force_turn(self, character_id: str) -> Message | None:
        """点名直通：不经导演，立即让该角色说一句（无玩家消息）。"""
        r = self.r
        if not r._ensure_open():  # B5
            return None
        async with r.runtime.turn_lock:
            character = r.state.character(character_id)
            if character is None or not character.present or character.muted:
                return None
            return await r.turns.run_character_turn(character, r.state.current_turn() + 1)

    async def swipe(self, message_id: str) -> Message | None:
        """Compatibility wrapper for the versioned one-node regeneration service."""
        r = self.r
        if not r._ensure_open():
            return None
        msg = next((m for m in r.state.messages if m.id == message_id), None)
        if msg is None:
            return None
        from mrp.orchestrator.message_regeneration import RegenerationRequest, RegenerationConflict, RegenerationFailure
        try:
            await r.regeneration.run("one", message_id, RegenerationRequest(
                operation_id=new_id("op"), expected_branch_revision=r.state.meta.branch_revision,
                expected_fingerprint=msg.fingerprint,
                expected_player_identity_id=r.state.meta.player_identity_id))
        except (RegenerationConflict, RegenerationFailure):
            return None
        return next((m for m in r.state.messages if m.id == message_id), None)

    async def edit_message(self, message_id: str, content: str) -> Message | None:
        """R32.2 编辑任意消息：改 content 即改下回合上下文（逐回合重组红利）。"""
        r = self.r
        if not r._ensure_open():  # B5
            return None
        async with r.runtime.turn_lock:
            msg = await self.edit_message_locked(message_id, content)
            if msg is None:
                return None
            await r._emit("message.updated", {"message": msg.model_dump(mode="json")})
            return msg

    async def edit_message_locked(
        self, message_id: str, content: str, *, expected_fingerprint: str | None = None,
    ) -> Message | None:
        """Edit one message while the caller holds ``turn_lock``.

        HTTP handlers use this variant so validation, mutation, and persistence
        happen under one branch lock. The public ``edit_message`` remains the
        lock-owning compatibility wrapper for existing callers.
        """
        r = self.r
        if not r._ensure_open():
            return None
        msg = next((m for m in r.state.messages if m.id == message_id), None)
        if msg is None or msg.control_event or msg.status != "final" or not content.strip():
            return None
        if expected_fingerprint is not None and msg.fingerprint != expected_fingerprint:
            raise MessageEditConflict("消息已被其他操作修改，请重新载入后再编辑")
        if msg.content != content:
            await self.invalidate_memory_sources({msg.id})
        msg.content = content
        msg.edited = True
        msg.fingerprint = fingerprint(msg.actor, msg.seq, msg.content)
        return msg

    async def edit_input_group_locked(
        self,
        target_message_id: str,
        updates: list[tuple[str, str, str]],
        *,
        expected_branch_revision: int,
    ) -> list[Message]:
        """Atomically edit all segments from one player submission.

        ``updates`` are ordered ``(message_id, expected_fingerprint, content)``
        tuples. The caller holds ``turn_lock`` through persistence.
        """
        r = self.r
        if not r._ensure_open():
            return []
        if r.state.meta.branch_revision != expected_branch_revision:
            raise MessageEditConflict("路线内容已变化，请重新载入后再编辑")

        messages = r.state.messages
        target_index = next((i for i, m in enumerate(messages) if m.id == target_message_id), None)
        if target_index is None:
            return []
        target = messages[target_index]
        if target.actor != "player" or target.status != "final":
            return []

        group_id = target.input_group_id
        if group_id:
            expected_indices = [
                i for i, m in enumerate(messages)
                if m.actor == "player" and m.status == "final" and m.input_group_id == group_id
            ]
        else:
            # Old saves have no explicit group id. A single submission was
            # persisted as adjacent player segments sharing the same turn.
            left = target_index
            while left > 0 and messages[left - 1].actor == "player" and messages[left - 1].turn == target.turn:
                left -= 1
            right = target_index + 1
            while right < len(messages) and messages[right].actor == "player" and messages[right].turn == target.turn:
                right += 1
            expected_indices = [
                i for i in range(left, right)
                if messages[i].actor == "player" and messages[i].status == "final"
            ]

        group_messages = [messages[i] for i in expected_indices]
        if not group_messages or target not in group_messages:
            return []
        if expected_indices != list(range(expected_indices[0], expected_indices[-1] + 1)):
            raise MessageEditConflict("逻辑输入段落不连续，无法安全地整组编辑")
        if any(msg.turn != target.turn or msg.actor != "player" for msg in group_messages):
            raise MessageEditConflict("逻辑输入段落不属于同一轮玩家输入")
        if [item[0] for item in updates] != [m.id for m in group_messages]:
            raise MessageEditConflict("这条逻辑输入的段落已变化，请重新载入后再编辑")
        for msg, (_, expected_fingerprint, content) in zip(group_messages, updates, strict=True):
            if msg.status != "final" or msg.fingerprint != expected_fingerprint:
                raise MessageEditConflict("消息已被其他操作修改，请重新载入后再编辑")
            if not content.strip():
                raise ValueError("每个消息段落都必须填写内容")
            if "（内心：" in content or "（旁白：" in content:
                raise ValueError("此处只编辑当前段落正文；不能在段落中新增内心或旁白标记")

        changed_ids = {msg.id for msg, (_, _, content) in zip(group_messages, updates, strict=True) if msg.content != content}
        if changed_ids:
            await self.invalidate_memory_sources(changed_ids)
        for msg, (_, _, content) in zip(group_messages, updates, strict=True):
            msg.content = content
            msg.edited = True
            msg.fingerprint = fingerprint(msg.actor, msg.seq, msg.content)
        return group_messages

    async def continue_message(self, message_id: str) -> Message | None:
        """R37.3 续写：最后一条角色消息自然接续——拼接语义，计入同一条 variants。

        与 swipe 的关键差异：上下文**包含** target 自身（要看到已写内容才能接续）；
        新候选 content = 旧内容 + 续写段（切回 variants[0] 即撤销续写）。
        走角色自己的引擎（人格一致性；usage 进主账 + by_purpose["continue"]）。
        """
        r = self.r
        if not r._ensure_open():  # B5
            return None
        async with r.runtime.turn_lock:
            msg = next((m for m in r.state.messages if m.id == message_id), None)
            if msg is not None and before_control_boundary(r.state, msg):
                return None
            if (
                msg is None
                or msg.actor == "player"
                or msg.actor == "director"
                or msg.status != "final"
                or (r.state.messages and msg is not r.state.messages[-1])
            ):
                return None
            character = r.state.character(msg.actor)
            if character is None:
                return None

            capacity = await r.context_builder.model_capacity(character)
            raw_visible = r.state.visible_messages_for(character.id)
            visible = r.context_builder.compress_history(
                character, raw_visible,
                input_limit=capacity.input_limit,
            )  # 含 target 自身
            recall_audit = []
            injections = await r.context_builder.build_injections(character, msg.turn, visible=visible,
                recall_audit=recall_audit)
            injections.append(
                Injection(
                    source="system",
                    entry_id="continue_directive",
                    content="从最后一条发言的结尾处自然续写。只输出续写部分，不要重复已有内容。",
                    anchor="near",
                    order=50,
                )
            )
            ctx = TurnContext(
                session_id=r.state.meta.id,
                character_id=character.id,
                turn=msg.turn,
                message_id=msg.id, generation_id=new_id("gen"),
                operation_id=new_id("op"), attempt_id=new_id("attempt"),
                visible_messages=visible,
                actor_labels=build_actor_labels(r.state, visible, speaker_id=character.id),
                injections=injections,
                budget_tokens=capacity.input_limit,
                capacity_source=capacity.source,
                capacity_fetched_at=capacity.fetched_at,
                model_id=character.llm.model,
                gateway=character.llm.base_url,
                model_provider=character.llm.effective_provider,
                context_limit=capacity.context_limit,
                output_reserve=capacity.output_reserve,
                world_core_brief=r.state.meta.world_core_brief,
                world_runtime_policy=r.state.meta.world_runtime_policy,
                player_persona=r.state.meta.player_persona,
                player_identity_source=identity_source(r.state),
                reply_max_tokens=r.state.meta.reply_max_tokens,
                style=r.context_builder.narrative_style(),
                prompt_transforms=r.context_builder.prompt_transforms(),
            )
            plan = plan_context(ctx, model=character.llm.model, persona=persona_from_card(character.card, player_name_for_context(ctx)))
            composed = plan.prompt
            ctx.planned_prompt = composed
            ctx.plan_id = plan.plan_id
            finish_recall_audit(recall_audit, composed.included_entry_ids, composed.omitted_reasons)
            provenance = make_provenance(r.state, participants=[character.id])
            provenance.baseline_state = r.context_builder.generation_material_projection()
            if r.memory is not None and hasattr(r.memory, "current_watermark"):
                provenance.baseline_memory_watermark = r.memory.current_watermark(r.state.meta.id)
            ctx.provenance = finish_provenance(provenance, r.state, raw_visible,
                recall_audit=recall_audit,
                include_player_inner=any(i.entry_id == "player_inner_subtext" for i in injections))
            # The continuation reads the previous text of this same node. Its
            # combined candidate must not depend on an obsolete fingerprint of
            # itself and become stale immediately after the suffix is appended.
            ctx.provenance.sources = [ref for ref in ctx.provenance.sources if ref.message_id != msg.id]
            r.runtime.inspections[(character.id, msg.turn)] = {
                "ctx": ctx, "composed": composed, "memory_recall": recall_audit,
            }

            old_content = msg.content
            snap = (
                msg.content, msg.status, msg.active_variant,
                msg.generation_meta, msg.hygiene,
                msg.generation_id, msg.operation_id, msg.attempt_id,
            )
            msg.status = "pending"
            msg.generation_id, msg.operation_id, msg.attempt_id = ctx.generation_id, ctx.operation_id, ctx.attempt_id
            await r._emit(
                "message.pending",
                {"message": msg.model_dump(mode="json"), "character_id": msg.actor, "turn": msg.turn},
            )
            stream_bridge = (
                r.turns.make_delta_bridge(
                    msg.id, character.id, msg.turn, initial_offset=len(old_content)
                )
                if r.state.meta.streaming_enabled
                else None
            )
            try:
                reply, emitted = await r.turns.generate_with_bridge(character, ctx, stream_bridge)
            except Exception:
                (
                    msg.content, msg.status, msg.active_variant,
                    msg.generation_meta, msg.hygiene,
                    msg.generation_id, msg.operation_id, msg.attempt_id,
                ) = snap
                logger.warning("continue failed, restored previous content (message=%s)", msg.id, exc_info=True)
                await r._emit(
                    "message.error",
                    {"message_id": msg.id, "turn": msg.turn, "error": "续写失败，原内容已保留"},
                )
                return None

            # R34：对续写部分跑校验（证据句落在新增段）
            hygiene = None
            if r.hygiene_active:
                try:
                    hygiene = await r.turns.run_hygiene_check(character, ctx, reply.content)
                except Exception:  # noqa: BLE001 —— 双保险 fail-open
                    logger.warning(
                        "continue hygiene check failed (message=%s)", msg.id, exc_info=True
                    )
                    hygiene = None

            # 流式：真流式引擎已从旧内容尾部续发（桥 initial_offset）；DSH 走整段切片
            if r.state.meta.streaming_enabled:
                await r.turns.emit_deltas(
                    msg, character.id, msg.turn, old_content + reply.content,
                    already_emitted=emitted,
                )

            meta = GenerationMeta(
                message_id=msg.id, generation_id=ctx.generation_id, operation_id=ctx.operation_id,
                attempt_id=ctx.attempt_id, request_ids=ctx.request_ids, plan_id=ctx.plan_id,
                provenance=ctx.provenance, memory_recall=recall_audit,
                finish_reason=reply.finish_reason,
                completion_state=completion_state(reply.finish_reason),
                engine_session_id=reply.engine_session_id,
                model=character.llm.model,
                usage=reply.usage,
                injected_entry_ids=composed.included_entry_ids,
                prompt_tokens_by_section=composed.tokens_by_section,
            )
            r.turns.track_purpose_cost("continue", reply.usage, generation_id=ctx.generation_id)
            if not msg.variants:
                msg.variants = [MessageVariant(
                    content=old_content,
                    generation_meta=msg.generation_meta,
                    hygiene=msg.hygiene,
                )]
            await self.invalidate_memory_sources({msg.id})
            msg.variants.append(
                MessageVariant(content=old_content + reply.content, generation_meta=meta, hygiene=hygiene)
            )
            msg.active_variant = len(msg.variants) - 1
            msg.content = old_content + reply.content
            msg.generation_meta = meta
            msg.generation_id = meta.generation_id
            msg.operation_id = meta.operation_id
            msg.attempt_id = meta.attempt_id
            msg.hygiene = hygiene
            msg.fingerprint = fingerprint(msg.actor, msg.seq, msg.content)
            msg.status = "final"
            r._record_message_revision(msg)
            await r._emit("message.final", {"message": msg.model_dump(mode="json")})
            await r._emit(
                "cost.update",
                {"cost_by_model": r.runtime.cost_by_model, "by_purpose": r.runtime.cost_by_purpose},
            )
            return msg

    async def switch_variant(self, message_id: str, index: int) -> Message | None:
        """R32.1 候选切换：active_variant 指向的候选成为当前生效内容。"""
        r = self.r
        if not r._ensure_open():  # B5
            return None
        async with r.runtime.turn_lock:
            msg = next((m for m in r.state.messages if m.id == message_id), None)
            if (
                msg is None
                or msg.status == "pending"
                or not msg.variants
                or not 0 <= index < len(msg.variants)
            ):
                return None
            variant = msg.variants[index]
            if msg.content != variant.content:
                await self.invalidate_memory_sources({msg.id})
            msg.active_variant = index
            msg.content = variant.content
            msg.generation_meta = variant.generation_meta
            msg.hygiene = variant.hygiene
            msg.fingerprint = fingerprint(msg.actor, msg.seq, msg.content)
            msg.accepted_dependency_sources = list(variant.accepted_dependency_sources)
            for key in ("generation_id", "operation_id", "attempt_id"):
                setattr(msg, key, getattr(variant.generation_meta, key, None))
            from mrp.orchestrator.generation_sources import recompute_dependency_state
            changed = recompute_dependency_state(r.state)
            for downstream in changed:
                await self.invalidate_memory_sources({downstream.id})
                await r._emit("message.updated", {"message": downstream.model_dump(mode="json")})
            await r._emit("message.updated", {"message": msg.model_dump(mode="json")})
            return msg

    async def delete_message(self, message_id: str) -> bool:
        """R32.3 真删除：从历史物理移除（区别于 retract 的"全员不可见但保留"）。"""
        r = self.r
        if not r._ensure_open():  # B5
            return False
        async with r.runtime.turn_lock:
            msg = next((m for m in r.state.messages if m.id == message_id), None)
            if msg is None or msg.control_event or msg.status == "pending":
                return False
            if any(event.anchor_message_id == message_id for event in r.state.story_events):
                return False
            if any(fact.source_message_id == message_id for fact in r.state.pinned_facts):
                return False
            await self.invalidate_memory_sources({message_id})
            r.state.messages.remove(msg)
            reconcile_revisions(r.state)
            await r._emit("message.deleted", {"message_id": msg.id, "turn": msg.turn})
            return True

    async def regenerate_turn(self, message_id: str) -> list[Message] | None:
        """R45 重跑最后一轮：编辑"我的消息"后，基于改后文本重新生成角色回复。

        语义（用户拍板 2026-09-24）：
        - 仅限**最后一轮**：目标必须是最后一条玩家消息（其后只有同回合的非玩家消息），
          历史消息不可重跑（前端置灰并提示）；其后暂无回复时也允许（=让角色回应这条）；
        - 旧回复**物理删除**（与 R32.3 同语义：不再进入后续上下文与记忆固化）；
        - 生成失败**整体回滚**：清掉本轮半成品、恢复被删回复并重新落账（防死局）；
        - 路由复用消息上持久化的显式 mentions（R30），导演 rng_seed 同 turn → 路由稳定；
        - 后续选项生成由 execute_decision 尾部自动重跑。
        """
        r = self.r
        if not r._ensure_open():  # B5
            return None
        async with r.runtime.turn_lock:
            r.runtime.turn_errors = []
            idx = next(
                (i for i, m in enumerate(r.state.messages) if m.id == message_id), None
            )
            if idx is None:
                return None
            msg = r.state.messages[idx]
            if before_control_boundary(r.state, msg):
                return None
            if msg.actor != "player" or msg.status != "final" or msg.kind not in ("roleplay", "scene"):
                return None
            tail = list(r.state.messages[idx + 1:])
            if any(m.actor == "player" or m.turn != msg.turn for m in tail):
                return None  # 非最后一轮（或混合通道下非末条玩家消息）
            # Replaying a scene transition needs a full scene-state rollback.
            # Until that operation is available, reject instead of replaying
            # against the later scene and recording a false checkpoint.
            if any(m.scene_id != msg.scene_id for m in tail):
                return None
            removed_ids = {item.id for item in tail}
            if any(event.anchor_message_id in removed_ids for event in r.state.story_events):
                return None
            if any(fact.source_message_id in removed_ids for fact in r.state.pinned_facts):
                return None
            old_revisions = list(r.state.state_revisions)
            old_head_revision_id = r.state.head_state_revision_id
            old_director_log = list(r.state.director_log)
            old_reply_plan = (
                msg.executed_reply_mode, list(msg.reply_order), msg.reply_reason,
                msg.reply_basis_fingerprint, msg.reply_shared_scene,
            )
            old_group_turns = {group.id: group.last_spoke_turn for group in r.state.groups}
            invalidated = await self.invalidate_memory_sources(removed_ids)
            for m in tail:
                r.state.messages.remove(m)
                await r._emit("message.deleted", {"message_id": m.id, "turn": m.turn})
            r.state.director_log = [item for item in r.state.director_log if item.turn != msg.turn]
            for group in r.state.groups:
                if group.last_spoke_turn == msg.turn or any(item.actor == group.id for item in tail):
                    group.last_spoke_turn = next(
                        (item.turn for item in reversed(r.state.messages)
                         if item.actor == group.id and item.status == "final"),
                        None,
                    )
            snapshot = len(r.state.messages)  # 回滚基线：其后皆为本轮新产物

            channel = "narration" if msg.kind == "scene" else "dialogue"
            try:
                created: list[Message] = []
                pad_msg = await r.turns.maybe_pad(msg.content, channel, msg.content, msg.turn)
                if pad_msg is not None:
                    created.append(pad_msg)
                active_groups, explicit_groups, group_targets, character_mentions = (
                    r.turns.resolve_group_targets(msg.content, msg.turn, msg.mentions or None)
                )
                chars = r._characters_by_id()
                ordered = list(dict.fromkeys(msg.mentions or []))
                ordered = [actor_id for actor_id in ordered if (
                    (actor_id in chars and chars[actor_id].present and not chars[actor_id].muted)
                    or actor_id in active_groups
                )]
                if msg.reply_mode is not None and len(ordered) >= 2:
                    if not msg.edited and msg.executed_reply_mode in ("parallel", "serial") and set(msg.reply_order) == set(ordered):
                        mode, order = msg.executed_reply_mode, list(msg.reply_order)
                        shared_scene = msg.reply_shared_scene
                    else:
                        mode, order, reason, shared_scene = await r.turns.decide_reply_plan(msg.reply_mode, ordered, msg.content)
                        msg.executed_reply_mode = mode
                        msg.reply_order = order
                        msg.reply_reason = reason
                        msg.reply_basis_fingerprint = msg.fingerprint
                        msg.reply_shared_scene = shared_scene
                    created.extend(await r.turns.run_ordered_turn(order, chars, active_groups, msg.turn, mode,
                                                                  parallel_plan=shared_scene, skip_scene_plan=msg.reply_mode == "auto",
                                                                  raise_on_error=True))
                    reconcile_revisions(r.state)
                    return created
                decision = await r.director_flow.decide_with_hints(
                    msg.content, channel, msg.turn,
                    character_mentions or (explicit_groups if explicit_groups else None),
                    None, group_mentions=group_targets,
                )
                if (
                    r.state.meta.director_mode == "confirm"
                    and decision.trigger.startswith("llm")
                ):
                    for group_id in group_targets:
                        created.append(await r.turns.run_group_turn(active_groups[group_id], msg.turn))
                    r.pending_director = decision
                    await r._emit(
                        "director.pending", {"decision": decision.model_dump(mode="json")}
                    )
                    reconcile_revisions(r.state)
                    return created
                created.extend(await r.turns.execute_routed_turn(
                    decision, msg.turn, group_targets, active_groups,
                ))
                reconcile_revisions(r.state)
                return created
            except Exception:
                # 回滚：清掉本轮半成品，恢复被删回复（前端需重新落账）
                logger.warning(
                    "regenerate failed, rolling back (message=%s)", message_id, exc_info=True
                )
                for m in list(r.state.messages[snapshot:]):
                    r.state.messages.remove(m)
                    await r._emit("message.deleted", {"message_id": m.id, "turn": m.turn})
                for m in tail:
                    r.state.messages.append(m)
                    await r._emit("message.final", {"message": m.model_dump(mode="json")})
                r.state.state_revisions = old_revisions
                r.state.head_state_revision_id = old_head_revision_id
                r.state.director_log = old_director_log
                (msg.executed_reply_mode, msg.reply_order, msg.reply_reason,
                 msg.reply_basis_fingerprint, msg.reply_shared_scene) = old_reply_plan
                for group in r.state.groups:
                    group.last_spoke_turn = old_group_turns[group.id]
                if invalidated and r.memory is not None and hasattr(r.memory, "restore_invalidated"):
                    await asyncio.to_thread(
                        r.memory.restore_invalidated, r.state.meta.id, invalidated
                    )
                raise

    async def set_presence(self, character_id: str, present: bool) -> Message | None:
        """离席/回归：翻在场位 + 插入一条场景消息（全员可见）。

        B21：纳入 turn_lock——回合进行中变更在场名单会等回合结束，
        `_execute_decision` 的角色名单快照因此一致（离席者不会在回合中途被选出）。
        """
        r = self.r
        if not r._ensure_open():  # B5
            return None
        async with r.runtime.turn_lock:
            character = r.state.character(character_id)
            if character is None:
                return None
            character.present = present
            text = f"{character.card.name} {'回到了场景中' if present else '离开了场景'}"
            msg = r._append_message(
                Message(
                    session_id=r.state.meta.id,
                    seq=r.state.next_seq(),
                    turn=r.state.current_turn(),
                    actor="player",
                    content=f"（{text}）",
                    kind="scene",
                    visible_to="all",
                    input_group_id=new_id("input"),
                )
            )
            await r._emit("message.final", {"message": msg.model_dump(mode="json")})
            return msg

    def set_muted(self, character_id: str, muted: bool) -> None:
        character = self.r.state.character(character_id)
        if character is not None:
            character.muted = muted

    async def open_round(self) -> list[Message]:
        """开场：按加入顺序出 first_mes。"""
        r = self.r
        if not r._ensure_open():  # B5
            return []
        async with r.runtime.turn_lock:
            turn = r.state.current_turn() + 1
            decision = r.director.decide(
                trigger="open_round",
                text="",
                characters=r.state.characters,
                history=r.state.messages,
                rng_seed=r.runtime.rng(turn),
            )
            r.state.director_log.append(decision)
            created: list[Message] = []
            for cid in decision.chosen:
                character = r._characters_by_id().get(cid)
                if character is None:
                    continue
                first = character.card.first_mes or next(
                    (g for g in character.card.alternate_greetings if g), ""
                )
                if not first:
                    continue
                from mrp.shared.player_identity import current_identity
                identity = current_identity(r.state)
                first = expand_prompt_macros(first, character_name=character.card.name,
                    player_name=identity.name if identity else player_name_from_persona(r.state.meta.player_persona),
                    story=r.state.meta.title)
                msg = r._append_message(
                    Message(
                        session_id=r.state.meta.id,
                        seq=r.state.next_seq(),
                        turn=turn,
                        actor=character.id,
                        content=first,
                        kind="roleplay",
                        visible_to=visibility.compute_visible_to(r.state.characters, group_ids=visibility.active_group_ids(r.state)),
                    )
                )
                created.append(msg)
                await r._emit("message.final", {"message": msg.model_dump(mode="json")})
            return created
