"""M12-R41 辅助候选取材：冷启动/对话中材料组装 + 手动生成入口（W3 拆分自 session.py）。

候选生成内核在 assist.py（结构已健康，不动）；此处只负责从会话状态取材。
"""
from __future__ import annotations

import asyncio
import logging
from mrp.shared.player_identity import player_key, message_label, before_control_boundary, current_inner

logger = logging.getLogger(__name__)


class AssistMaterialBuilder:
    """一个 SessionRunner 的候选取材器（无状态，一律实时读 runner）。"""

    def __init__(self, runner) -> None:
        self.r = runner

    def ready(self) -> bool:
        return self.r.state.meta.options_enabled and self.r.assist_generator is not None

    def display_name(self, actor: str) -> str:
        if actor == "player":
            return "玩家"
        c = self.r._characters_by_id().get(actor)
        if c is not None:
            return c.card.name
        group = next((item for item in self.r.state.groups if item.id == actor), None)
        return group.label if group is not None else "已退出角色"

    def _visible_final_messages(self):
        return [
            message for message in self.r.state.visible_messages_for(player_key(self.r.state))
            if message.status == "final"
        ]

    def _latest_anchor(self):
        visible = self._visible_final_messages()
        return max(visible, key=lambda message: message.seq, default=None)

    def _attach_anchor(self, material, anchor=None) -> None:
        anchor = anchor or self._latest_anchor()
        material.branch_id = self.r.state.meta.id
        material.branch_revision = self.r.state.meta.branch_revision
        material.player_identity_id = self.r.state.meta.player_identity_id
        if anchor is None:
            return
        material.anchor_message_id = anchor.id
        material.anchor_fingerprint = anchor.fingerprint
        material.anchor_actor = anchor.actor
        material.anchor_label = message_label(self.r.state, anchor, {anchor.actor: self.display_name(anchor.actor)})
        material.anchor_kind = anchor.kind
        material.anchor_text = anchor.content

    def _batch_metadata(self, material) -> dict:
        return {
            "player_identity_id": material.player_identity_id,
            "branch_id": material.branch_id,
            "branch_revision": material.branch_revision,
            "anchor_message_id": material.anchor_message_id,
            "anchor_fingerprint": material.anchor_fingerprint,
            "anchor_actor": material.anchor_actor,
            "anchor_label": material.anchor_label,
            "anchor_kind": material.anchor_kind,
            "anchor_excerpt": material.anchor_text[:120],
        }

    async def _material_is_current(self, material) -> bool:
        r = self.r
        async with r.runtime.turn_lock:
            anchor = self._latest_anchor()
            return (
                r.state.meta.id == material.branch_id
                and r.state.meta.branch_revision == material.branch_revision
                and r.state.meta.player_identity_id == material.player_identity_id
                and anchor is not None
                and anchor.id == material.anchor_message_id
                and anchor.fingerprint == material.anchor_fingerprint
            )

    async def _capture(self, *, intent: str | None = None):
        r = self.r
        async with r.runtime.turn_lock:
            anchor = self._latest_anchor()
            if anchor is not None and before_control_boundary(r.state, anchor):
                return None
            material = self.cold_start_material() or self.turn_material()
            if material is None:
                return None
            if intent is not None:
                material.kind = "draft"
                material.intent = intent.strip()
            return material

    def _empty_reason(self):
        anchor = self._latest_anchor()
        return "control_boundary" if anchor is not None and before_control_boundary(self.r.state, anchor) else "empty"

    def roster(self) -> list:
        from mrp.orchestrator.assist import AssistCharacter, clip_line

        return [
            AssistCharacter(
                id=c.id,
                name=c.card.name,
                note=clip_line(c.card.personality or c.card.description or "", 60),
                aliases=list(c.mention_names),
            )
            for c in self.r.state.characters
            if c.present
        ]

    def cold_start_material(self):
        """冷启动材料（开场/切场景）；None = 不在冷启动点。

        冷启动事件 = 最后一条 actor=director 且 kind=scene 的消息（场景过渡）；
        找不到则为会话起点（开场）。在冷启动点 ⟺ 事件之后（tail，含过渡本身）
        非空且无 player 消息（内心消息也算已发言；离席/回归同样阻断）。
        """
        from mrp.orchestrator.assist import (
            FIRST_MES_MAX,
            RECENT_MAX,
            SAMPLES_MAX,
            AssistMaterial,
            clip_line,
            opening_lines_with_budget,
        )

        r = self.r
        msgs = self._visible_final_messages()
        start = -1  # 冷启动事件下标；-1 = 会话起点
        for i, m in enumerate(msgs):
            if m.kind == "scene" and m.actor == "director":
                start = i
        # tail 含冷启动事件本身（过渡描写也是玩家的"抓手"；无首发言的切场景同样算冷启动点）
        tail = msgs if start < 0 else msgs[start:]
        if not tail or any(m.actor == "player" for m in tail):
            return None

        # Once a character or group has replied after the scene transition, this
        # is an ongoing exchange even if the player has not spoken yet.
        if start >= 0 and any(m.actor not in {"director", "player"} for m in tail[1:]):
            return None

        kind = "scene" if start >= 0 else "open"
        opening_items = (
            [(self.display_name(m.actor), m.content) for m in tail if m.actor != "player" and m.kind == "roleplay"]
            if kind == "open"
            else []
        )
        scene = r.active_scene()
        anchor = max(tail, key=lambda message: message.seq, default=None)
        material = AssistMaterial(
            kind=kind,
            scene_id=r.state.active_scene_id or "",
            event_turn=tail[-1].turn,
            persona=r.state.meta.player_persona,
            characters=self.roster(),
            opening_lines=opening_lines_with_budget(opening_items),
            scene_title=scene.title if scene is not None else "",
            scene_description=scene.description if scene is not None else "",
            transition=clip_line(msgs[start].content, FIRST_MES_MAX) if start >= 0 else "",
            recent_lines=[
                f"{message_label(r.state, m, {m.actor: self.display_name(m.actor)})}: {clip_line(m.content)}"
                for m in tail[-RECENT_MAX:] if anchor is None or m.id != anchor.id
            ],
            player_samples=[
                clip_line(m.content) for m in msgs[: start + 1] if m.actor == "player" and current_inner(r.state, m)
            ][-SAMPLES_MAX:],
        )
        self._attach_anchor(material, anchor)
        return material

    def turn_material(self):
        """对话进行中（非冷启动点）的材料：最近对话 + 在场名单 + persona + 风格。"""
        from mrp.orchestrator.assist import RECENT_MAX, SAMPLES_MAX, AssistMaterial, clip_line

        r = self.r
        msgs = r.state.messages
        visible = [m for m in self._visible_final_messages()]
        tail = visible[-RECENT_MAX:]
        if not tail:
            return None  # 空会话：无对话可接
        scene = r.active_scene()
        anchor = max(visible, key=lambda message: message.seq, default=None)
        material = AssistMaterial(
            kind="turn",
            scene_id=r.state.active_scene_id or "",
            event_turn=r.state.current_turn(),
            style=r.state.meta.options_style,
            persona=r.state.meta.player_persona,
            characters=self.roster(),
            scene_title=scene.title if scene is not None else "",
            scene_description=scene.description if scene is not None else "",
            recent_lines=[
                f"{message_label(r.state, m, {m.actor: self.display_name(m.actor)})}: {clip_line(m.content)}"
                for m in tail if anchor is None or m.id != anchor.id
            ],
            player_samples=[
                clip_line(m.content) for m in visible if m.actor == "player" and current_inner(r.state, m)
            ][-SAMPLES_MAX:],
        )
        self._attach_anchor(material, anchor)
        return material

    async def generate_candidates(self) -> dict:
        """手动生成一批辅助候选（唯一入口；POST /assist/candidates）。

        - 上下文自选：冷启动点 → open/scene 材料；否则 → turn 材料（对话接话）
        - 每次调用都是全新生成（无缓存、无后台任务、无额度——点击即一次生成）
        - 失败/未启用/无上下文一律静默：options 为空 + reason（绝不抛错、不阻塞任何流程）
        """
        r = self.r
        if not r._ensure_open():  # B5
            return {"options": [], "reason": "closed"}
        if not self.ready():
            return {"options": [], "reason": "disabled"}
        material = await self._capture()
        if material is None:
            return {"options": [], "reason": self._empty_reason()}
        choices = await asyncio.to_thread(r.assist_generator.generate, material)
        usage = getattr(r.assist_generator, "usage", None)
        if usage is not None:
            r.turns.track_purpose_cost("assist", usage)
        if not await self._material_is_current(material):
            return {"options": [], "reason": "stale", **self._batch_metadata(material)}
        if not choices:
            return {"options": [], "reason": "failed", **self._batch_metadata(material)}
        payload = {
            "options": [c.model_dump() for c in choices],
            "source": "turn" if material.kind == "turn" else "kickoff",
            "turn": material.event_turn,
            "scene_id": material.scene_id,
            **self._batch_metadata(material),
        }
        if material.kind != "turn":
            payload["kickoff_kind"] = material.kind
        return payload

