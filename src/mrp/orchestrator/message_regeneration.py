"""Single-node alternatives and explicit atomic dependency replay.

The turn lock includes CAS, persistence and compensation. Model calls consume
real tokens even if the story commit fails; cost/request audit is never undone.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Awaitable, Callable

from pydantic import BaseModel, Field

from mrp.shared.models import Message, MessageVariant, SessionState, new_id, fingerprint
from mrp.shared.player_identity import before_control_boundary, player_key
from mrp.orchestrator.generation_sources import (
    source_ref, generation_baseline, generation_provenance, finish_provenance,
    dependency_descendants, recompute_dependency_state,
)

logger = logging.getLogger(__name__)


class RegenerationRequest(BaseModel):
    operation_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    expected_branch_revision: int = Field(ge=0)
    expected_fingerprint: str = Field(min_length=1, max_length=128)
    expected_player_identity_id: str | None


from mrp.shared.story_errors import RegenerationConflict


class RegenerationFailure(RuntimeError):
    pass


from mrp.shared.state_restore import restore_state_in_place

class MessageRegeneration:
    def __init__(self, runner):
        self.r = runner

    def _validate(self, message_id, req, *, validate_node=True):
        state = self.r.state
        if state.meta.branch_revision != req.expected_branch_revision:
            raise RegenerationConflict("路线内容已变化，请重新载入后再操作")
        if state.meta.player_identity_id != req.expected_player_identity_id:
            raise RegenerationConflict("控制身份已变化，请重新载入后再操作")
        message = next((m for m in state.messages if m.id == message_id), None)
        if message is None:
            raise RegenerationConflict("消息不在当前路线中")
        if message.fingerprint != req.expected_fingerprint:
            raise RegenerationConflict("消息内容已变化，请重新载入后再操作")
        if validate_node:
            self._validate_node(message)
        elif message.status != "final":
            raise RegenerationConflict("消息正在生成，请稍后切换候选")
        return message

    def _validate_node(self, message):
        state = self.r.state
        if (message.actor in {"player", "director", player_key(state)}
                or message.kind != "roleplay" or message.status != "final"
                or message.control_event or before_control_boundary(state, message)
                or message.turn != state.current_turn() or message.scene_id != state.active_scene_id
                or any(m.seq > message.seq and (m.control_event or
                    (m.actor == "player" and m.kind in {"roleplay", "scene"})) for m in state.messages)):
            raise RegenerationConflict("只能操作当前控制阶段和场景中最新交互段的 NPC 回应")
        character = state.character(message.actor)
        group = next((g for g in state.groups if g.id == message.actor), None)
        if character is None and group is None:
            raise RegenerationConflict("回应对象已不在当前故事中")
        if character is not None and (not character.present or character.muted):
            raise RegenerationConflict("该角色当前不参与回应")
        if group is not None and (group.status != "active" or group.scene_id != state.active_scene_id):
            raise RegenerationConflict("该群体已离开当前场景")

    def _legacy_sources(self):
        # Only current-segment unknown nodes are reconstructed. Existing exact
        # provenance, including same-speaker repeated nodes, stays untouched.
        for message in self.r.state.messages:
            if (message.actor not in {"player", "director"} and message.status == "final"
                    and message.turn == self.r.state.current_turn() and generation_provenance(message) is None):
                snap, provenance = generation_baseline(self.r.state, message)
                visible = snap.visible_messages_for(message.actor)
                from mrp.shared.models import GenerationMeta
                if message.generation_meta is None:
                    message.generation_meta = GenerationMeta()
                message.generation_meta.provenance = finish_provenance(provenance, snap, visible)

    async def _draft(self, target, operation_id, baseline_transform=None):
        snap, provenance = generation_baseline(self.r.state, target)
        if baseline_transform is not None:
            snap, provenance = await baseline_transform(snap, provenance, target)
        target.status = "pending"
        pending = target.model_copy(deep=True)
        pending.content = ""
        pending.status = "pending"
        pending.generation_id = new_id("gen")
        pending.operation_id = operation_id
        pending.attempt_id = new_id("attempt")
        pending.variants = []
        pending.active_variant = None
        self.r.turns.prepare_generation(pending)
        await self.r._emit("message.pending", {"message": pending.model_dump(mode="json"),
                           "character_id": target.actor, "turn": target.turn})
        character = snap.character(target.actor)
        if character is not None:
            return await self.r.turns.run_character_turn(
                character, target.turn, pending_override=pending, pending_emitted=True,
                defer_commit=True, context_state=snap, provenance=provenance,
                reply_mode=provenance.mode, participants=provenance.participants,
                parallel_plan=provenance.shared_scene, shared_frame=provenance.shared_frame,
                emit_final=False, previous_candidate_content=target.content,
            )
        group = next(g for g in snap.groups if g.id == target.actor)
        return await self.r.turns.run_group_turn(
            group, target.turn, pending_override=pending, pending_emitted=True,
            defer_commit=True, state_snapshot=snap, provenance=provenance,
            reply_mode=provenance.mode, participants=provenance.participants,
            parallel_plan=provenance.shared_scene, emit_final=False,
        )

    def _install(self, target, draft):
        if not target.variants:
            target.variants.append(MessageVariant(content=target.content,
                generation_meta=target.generation_meta.model_copy(deep=True) if target.generation_meta else None,
                hygiene=target.hygiene,
                accepted_dependency_sources=list(target.accepted_dependency_sources)))
        target.variants.append(MessageVariant(content=draft.content, generation_meta=draft.generation_meta,
                                              hygiene=draft.hygiene))
        target.active_variant = len(target.variants) - 1
        target.content = draft.content
        target.generation_meta = draft.generation_meta
        target.hygiene = draft.hygiene
        target.status = "final"
        target.edited = False
        target.accepted_dependency_sources = []
        for key in ("generation_id", "operation_id", "attempt_id"):
            setattr(target, key, getattr(draft, key))
        target.fingerprint = fingerprint(target.actor, target.seq, target.content)
        self.r._record_message_revision(target)

    async def run(self, action: str, message_id: str, req: RegenerationRequest, *,
                  persist: Callable[[], Awaitable] | None = None,
                  rollback_persist: Callable[[], Awaitable] | None = None,
                  protect: Callable[[set[str]], Awaitable] | None = None,
                  baseline_transform: Callable | None = None,
                  variant_index: int | None = None) -> dict:
        r = self.r
        if r.runtime.active_conversation_run_id:
            raise RegenerationConflict("对话正在自动推进，请先暂停或停止")
        if not r._ensure_open():
            raise RegenerationConflict("故事已关闭")
        request_hash = hashlib.sha256(json.dumps({"action": action, "message_id": message_id,
            "variant_index": variant_index,
            **req.model_dump(mode="json")}, sort_keys=True).encode()).hexdigest()
        async with r.runtime.turn_lock:
            existing = r.state.generation_operations.get(req.operation_id)
            if existing:
                if existing["request_hash"] != request_hash:
                    raise RegenerationConflict("操作 ID 已被另一个请求使用")
                return existing["response"]
            target = self._validate(message_id, req, validate_node=action != "switch")
            before = r.state.model_copy(deep=True)
            memory_prepared = False
            persistence_attempted = False
            regenerated = []
            attempted_ids = []
            try:
                self._legacy_sources()
                recompute_dependency_state(r.state)
                descendants = dependency_descendants(r.state, message_id)
                nodes = ([target] if action in {"one", "switch"} else
                         ([target] if target.dependency_stale else []) +
                         [m for m in descendants if m.dependency_stale])
                if action not in {"one", "dependents", "accept", "switch"}:
                    raise ValueError("Unknown regeneration action")
                if action != "switch":
                    for node in nodes:
                        self._validate_node(node)
                if protect is not None:
                    await protect({m.id for m in nodes})
                if action == "switch":
                    if variant_index is None or not 0 <= variant_index < len(target.variants):
                        raise RegenerationConflict("候选不存在，请重新载入")
                    variant = target.variants[variant_index]
                    target.active_variant = variant_index
                    target.content = variant.content
                    target.generation_meta = variant.generation_meta
                    target.hygiene = variant.hygiene
                    target.accepted_dependency_sources = list(variant.accepted_dependency_sources)
                    for key in ("generation_id", "operation_id", "attempt_id"):
                        setattr(target, key, getattr(variant.generation_meta, key, None))
                    target.fingerprint = fingerprint(target.actor, target.seq, target.content)
                elif action == "accept":
                    for node in nodes:
                        provenance = generation_provenance(node)
                        refs = [*provenance.sources, *provenance.scheduling_sources] if provenance else []
                        live = {m.id: m for m in r.state.messages}
                        node.accepted_dependency_sources = [source_ref(live[ref.message_id])
                                                           for ref in refs if ref.message_id in live]
                        if node.active_variant is not None:
                            node.variants[node.active_variant].accepted_dependency_sources = list(node.accepted_dependency_sources)
                else:
                    for node in nodes:
                        attempted_ids.append(node.id)
                        draft = await self._draft(node, req.operation_id, baseline_transform)
                        self._install(node, draft)
                        regenerated.append(node)
                        recompute_dependency_state(r.state)
                recompute_dependency_state(r.state)
                old_by_id = {m.id: m for m in before.messages}
                changed = [m for m in r.state.messages if m.model_dump() != old_by_id[m.id].model_dump()]
                invalid_ids = {m.id for m in changed if m.fingerprint != old_by_id[m.id].fingerprint
                               or m.dependency_stale != old_by_id[m.id].dependency_stale
                               or m.accepted_dependency_sources != old_by_id[m.id].accepted_dependency_sources}
                if invalid_ids and r.memory is not None and hasattr(r.memory, "prepare_generation_operation"):
                    await asyncio.to_thread(r.memory.prepare_generation_operation, r.state.meta.id, req.operation_id)
                    memory_prepared = True
                if invalid_ids:
                    await r.message_ops.invalidate_memory_sources(invalid_ids)
                resulting_revision = r.state.meta.branch_revision + (1 if persist else 0)
                response = {"messages": [m.model_dump(mode="json") for m in changed],
                    "branch_revision": resulting_revision,
                    "affected_message_ids": [m.id for m in descendants], "operation_id": req.operation_id}
                r.state.generation_operations[req.operation_id] = {"request_hash": request_hash, "response": response}
                if persist is not None:
                    persistence_attempted = True
                    await persist()
                response["branch_revision"] = r.state.meta.branch_revision
            except BaseException as exc:
                attempted_ids = list(dict.fromkeys(attempted_ids))
                restore_state_in_place(r.state, before)
                if persistence_attempted and rollback_persist is not None:
                    try:
                        await rollback_persist()
                    except Exception as restore_exc:
                        logger.exception("generation state compensation failed")
                        raise RegenerationFailure("保存及恢复失败，请重新载入故事检查保存状态") from restore_exc
                if memory_prepared:
                    await asyncio.to_thread(r.memory.finish_generation_operation,
                        r.state.meta.id, req.operation_id, rollback=True)
                for mid in attempted_ids:
                    original = next((m for m in r.state.messages if m.id == mid), None)
                    if original is not None:
                        await r._emit("message.error", {"message_id": mid, "turn": original.turn,
                            "error": "重新生成失败，原回复和候选已保留", "message": original.model_dump(mode="json"),
                            "operation_id": req.operation_id, "branch_revision": r.state.meta.branch_revision})
                if isinstance(exc, (RegenerationConflict, asyncio.CancelledError)):
                    raise
                raise RegenerationFailure("重新生成失败，原回复和候选已保留") from exc
            if memory_prepared:
                try:
                    await asyncio.to_thread(r.memory.finish_generation_operation, r.state.meta.id, req.operation_id)
                except Exception:
                    # The JSON marker is committed. Startup safely finalizes this
                    # journal; never report failure after a successful story commit.
                    logger.warning("generation recovery journal cleanup deferred", exc_info=True)
            for message in changed:
                await r._emit("message.final" if message in regenerated else "message.updated", {
                    "message": message.model_dump(mode="json"), "operation_id": req.operation_id,
                    "branch_revision": r.state.meta.branch_revision})
            return response
