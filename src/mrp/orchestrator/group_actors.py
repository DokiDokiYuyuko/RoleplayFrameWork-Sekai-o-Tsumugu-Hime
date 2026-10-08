"""Shared LLM adapter for scene group actors; no per-group process is created."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from mrp.llm import LlmConfig, chat_stream_with_usage, chat_text_with_usage, extract_json
from mrp.orchestrator.scene_frame import build_shared_scene_frame, recent_public_scene_context
from mrp.shared.actor_labels import build_reply_frame
from mrp.shared.player_identity import identity_source
from mrp.shared.models import GroupActor, Injection, SessionState, TurnContext
from mrp.shared.prompt import estimate_tokens

logger = logging.getLogger(__name__)


class GroupResponder:
    """Draft and speak for session-local groups through the configured gateways."""

    uses_shared_context_builder = True

    def __init__(self, container) -> None:
        self.container = container

    def _config(self, *, auxiliary: bool = False) -> LlmConfig:
        settings = self.container.settings
        return LlmConfig(
            model=(settings.auxiliary_model or settings.model) if auxiliary else settings.model,
            base_url=settings.gateway.rstrip("/"),
            api_key_env=self.container.config.api_key_env,
            max_tokens=1000 if auxiliary else (settings.generation.max_output_tokens or 0),
            provider=settings.auxiliary_provider if auxiliary else settings.model_provider,
            provider_allow_fallbacks=settings.provider_allow_fallbacks,
        )

    @staticmethod
    def biology_excerpt(record) -> str:
        """Make a compact, public-only excerpt suitable for preview and prompting."""
        parts = [f"生物设定：{record.title}"]
        if record.summary.strip():
            parts.append(f"摘要：{record.summary.strip()}")
        data = record.kind_data or {}
        labels = {
            "classification": "类别", "appearance": "外形", "habitat": "栖息地",
            "culture": "习性与文化", "abilities": "能力", "limitations": "限制",
        }
        for key, label in labels.items():
            value = data.get(key)
            if value and str(value).strip():
                parts.append(f"{label}：{str(value).strip()}")
        if record.aliases:
            parts.append("别名：" + "、".join(record.aliases))
        return "\n".join(parts)[:6000]

    @staticmethod
    def fallback_draft(record) -> dict[str, Any]:
        data = record.kind_data or {}
        public_parts = [
            str(data.get(key, "")).strip()
            for key in ("appearance", "habitat", "culture", "abilities", "limitations")
            if str(data.get(key, "")).strip()
        ]
        public_brief = "；".join(public_parts) or record.summary.strip()
        return {
            "label": f"在场的{record.title}"[:120], "aliases": [str(item)[:120] for item in record.aliases[:30]], "count": None,
            "public_brief": public_brief[:1800],
            "current_state": "在当前场景中活动，具体行动随剧情发展。",
            "director_note": "遵循来源生物设定和已确认剧情，不替玩家或正式角色决定行动。",
            "participation": "on_cue",
        }

    async def draft(
        self, record, scene_title: str, scene_description: str,
        recent_context: str = "",
    ) -> tuple[dict[str, Any], dict[str, int], str]:
        excerpt = self.biology_excerpt(record)
        fallback = self.fallback_draft(record)
        if self.container.fake_mode:
            trace["finish_reason"] = "stop"
            return fallback, {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}, excerpt
        prompt = (
            "根据公开生物设定，为当前角色扮演故事创建一个可编辑的临时群体参与者草稿。"
            "只输出 JSON 对象，字段为 label, aliases, count, public_brief, current_state, director_note, participation。"
            "count 可为 null；participation 只能是 on_cue 或 occasional。"
            "public_brief 只写角色在场可观察的事实；director_note 仅供群体生成器参考。"
            "current_state 必须与最近公开剧情的地点和行动一致；生物习性不是此刻已发生的行动。"
            "最近剧情没有明确地点时，只写中性的在场状态，不要自行安排伏击地点。"
            "不可添加来源中没有的世界观定论，不要生成永久个体姓名。"
        )
        user = (
            f"当前场景：{scene_title}\n{scene_description}\n\n"
            f"最近公开剧情（优先于生物习性）：\n{recent_context or '暂无'}\n\n{excerpt}"
        )
        try:
            text, usage = await asyncio.to_thread(
                chat_text_with_usage,
                [{"role": "system", "content": prompt}, {"role": "user", "content": user}],
                self._config(auxiliary=True), max_tokens=700, timeout=30, no_thinking=True,
            )
            parsed = extract_json(text)
            if isinstance(parsed, dict):
                draft = {**fallback, **parsed}
                draft["label"] = str(draft.get("label") or fallback["label"])[:120]
                draft["aliases"] = [str(item)[:120] for item in draft.get("aliases", []) if str(item).strip()][:30]
                for field, limit in (("public_brief", 1800), ("current_state", 1000), ("director_note", 1800)):
                    draft[field] = str(draft.get(field) or "")[:limit]
                if draft.get("participation") not in ("on_cue", "occasional"):
                    draft["participation"] = "on_cue"
                count = draft.get("count")
                draft["count"] = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
                return draft, usage, excerpt
        except Exception:  # noqa: BLE001 — draft failure never blocks manual editing
            logger.info("group actor draft generation failed; using deterministic draft", exc_info=True)
        return fallback, {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}, excerpt

    async def plan_parallel_scene(
        self, state: SessionState, turn: int, participants: dict[str, str],
    ) -> tuple[str, dict[str, int]]:
        """One short shared stage plan before independent parallel replies."""
        empty_usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
        if self.container.fake_mode or not participants:
            return "", empty_usage
        scene = next((item for item in state.scenes if item.id == state.active_scene_id), None)
        current_input_ids = {
            message.id for message in state.messages
            if message.turn == turn and message.actor == "player"
            and message.kind in ("roleplay", "scene") and message.status == "final"
        }
        public = recent_public_scene_context(
            state, turn, limit=12, exclude_message_ids=current_input_ids,
            viewer_ids=list(participants),
        )
        system = (
            "你是多人并行回复前的场面事实整理器。只输出 JSON："
            '{"stage":"已经发生的共同地点、时间和局势"}。'
            "只总结输入中已经发生或明确给出的事实，不设计任何参与者接下来要说的话、动作、决定、情绪或关系。"
            "最近公开剧情优先；群体旧登记状态可能已经过期，不能拿旧地点覆盖新剧情。"
            "不凭空切换地点。stage 不超过 100 字；没有需要补充的共同事实时 stage 可以为空字符串。"
        )
        user = (
            f"场景：{scene.title if scene else '未命名'}；{scene.description if scene else ''}\n"
            f"参与者名称：{json.dumps(list(participants.values()), ensure_ascii=False)}\n"
            f"最近公开剧情（时间顺序）：\n{public or '暂无'}\n"
        )
        try:
            text, usage = await asyncio.to_thread(
                chat_text_with_usage,
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                self._config(auxiliary=True), max_tokens=320, timeout=12, no_thinking=True,
            )
            parsed = extract_json(text)
            if not isinstance(parsed, dict):
                return "", usage
            stage = str(parsed.get("stage") or "").strip()[:200]
            if not stage:
                return "", usage
            return (
                "[本轮共同场面事实]\n" + stage
                + "\n这里只提供生成前已知事实，不代表任何参与者本轮尚未生成的行动或台词。"
            ), usage
        except Exception:  # noqa: BLE001 — planning is optional, replies still proceed
            logger.warning("parallel scene planning failed; using continuity fallback", exc_info=True)
            return "", empty_usage

    async def generate(
        self, state: SessionState, group: GroupActor, exclude_message_id: str | None = None,
        feedback: str = "",
        on_delta: Callable[[str], None] | None = None,
        parallel_plan: str = "",
        reply_mode: str = "single",
        participants: list[str] | None = None,
        context_builder=None,
        generation=None,
    ) -> tuple[str, dict[str, int], dict[str, Any]]:
        context_builder = context_builder or self._make_context_builder(state)
        return await self._generate_with_shared_context(
            state, group, context_builder, exclude_message_id=exclude_message_id,
            feedback=feedback, on_delta=on_delta, parallel_plan=parallel_plan,
            reply_mode=reply_mode, participants=participants,
            generation=generation,
        )

    def _make_context_builder(self, state: SessionState):
        """Standalone adapter for tests and callers outside SessionRunner."""
        from types import SimpleNamespace

        from mrp.orchestrator.context import ContextBuilder
        from mrp.orchestrator.lorebook import LorebookEngine

        container = self.container
        world_registry = getattr(container, "worlds", None)
        preset_store = getattr(container, "prompt_presets", None)
        seed = sum(state.meta.id.encode("utf-8"))
        runner = SimpleNamespace(
            state=state,
            app_settings=container.settings,
            memory=getattr(container, "memory_store", None),
            lorebooks=state.lorebooks,
            lorebook_engine=LorebookEngine(),
            runtime=SimpleNamespace(rng=lambda turn: (seed + turn * 7919) % 2**31),
            world_resolver=(lambda world_id: world_registry.get(world_id)) if world_registry else None,
            prompt_preset_resolver=(lambda preset_id: preset_store.get(preset_id)) if preset_store else None,
        )
        return ContextBuilder(runner)

    async def _generate_with_shared_context(
        self, state: SessionState, group: GroupActor, context_builder, *,
        exclude_message_id: str | None, feedback: str, on_delta,
        parallel_plan: str, reply_mode: str, participants: list[str] | None,
        generation=None,
    ) -> tuple[str, dict[str, int], dict[str, Any]]:
        """Use the same world, preset, lore, memory and prompt planner as characters."""
        from types import SimpleNamespace

        from mrp.orchestrator.context_plan import plan_context
        from mrp.orchestrator.memory_recall import finish_recall_audit
        from mrp.orchestrator.model_capacity import resolve_model_capacity
        current_turn = getattr(state, "_conversation_turn", state.current_turn())
        visible = [
            message for message in state.messages
            if message.status == "final"
            and not message.dependency_stale
            and message.seq >= group.joined_seq
            and message.id != exclude_message_id
            and message.scene_id == group.scene_id
            and message.kind in ("roleplay", "scene")
            and message.can_see(group.id)
        ]
        reply_frame = build_reply_frame(
            state, group.id, current_turn, reply_mode, visible, participants,
            provenance=(generation or {}).get("provenance"),
        )
        actor = SimpleNamespace(id=group.id, card=SimpleNamespace(name=group.label))
        recall_audit: list[dict] = []
        injections = await context_builder.build_injections(
            actor, current_turn, visible=visible, participants=participants,
            recall_audit=recall_audit, state=state, include_player_inner=False,
        )
        count = "人数不定" if group.count is None else f"{group.count} 名成员"
        group_details = "\n".join(part for part in (
            f"群体：{group.label}（{count}）",
            f"可观察设定：{group.public_brief or '无额外资料'}",
            f"建卡时记录的状态（可能滞后）：{group.current_state or '在场'}",
            f"建卡时幕后意图（仅供行为参考，不可直接泄露）：{group.director_note or '无'}",
            f"来源生物设定：{group.source_snapshot}" if group.source.get("kind") == "biology" and group.source_snapshot else "",
        ) if part)
        injections.append(Injection(
            source="system", entry_id=f"group:{group.id}",
            content=f"[群体设定]\n{group_details}", anchor="system", order=50,
            placement="current", reason="本群体的公开设定与来源快照",
        ))
        shared_frame = build_shared_scene_frame(
            state, current_turn, exclude_message_id=exclude_message_id,
            exclude_message_ids=set(reply_frame.trigger_message_ids),
            exclude_group_ids={group.id}, include_world_core=False,
            viewer_ids=[group.id],
        )
        if shared_frame:
            injections.append(Injection(
                source="system", entry_id="shared_scene_frame", content=shared_frame,
                anchor="near", order=90, placement="current",
                reason="同轮参与者共享的生成前公开场面",
            ))
        directive = getattr(state, "_conversation_directive", getattr((generation or {}).get("provenance"), "director_directive", ""))
        if directive:
            injections.append(Injection(source="system", entry_id="conversation_directive",
                content="[作者幕后要求；这不是人物听见的话，也不是已发生的事实]\n" + directive,
                anchor="near", order=95, placement="current", reason="当前有限对话的作者引导"))
        if parallel_plan:
            injections.append(Injection(
                source="system", entry_id="parallel_scene_plan", content=parallel_plan,
                anchor="near", order=100, placement="current",
                reason="同轮并行发言者共享的行动基线",
            ))
        if feedback:
            injections.append(Injection(
                source="system", entry_id="group_retry_feedback", content=feedback,
                anchor="near", order=310, placement="instructions",
                reason="本轮输出修正要求",
            ))

        settings = self.container.settings
        system = (
            f"你扮演临时群体“{group.label}”。第一人称指本群体成员。"
            "成员可以匿名发言，不为其创建永久个体身份；只依据群体能够知道的资料和可见剧情回应。"
        )
        capacity = await resolve_model_capacity(
            settings, settings.model, reply_max_tokens=state.meta.reply_max_tokens,
            base_url=settings.gateway,
        )
        input_limit = capacity.input_limit
        if input_limit is not None:
            input_limit = max(0, input_limit - estimate_tokens(system))
        ctx = TurnContext(
            session_id=state.meta.id, character_id=group.id, turn=current_turn,
            visible_messages=visible, actor_labels=reply_frame.actor_labels,
            reply_frame=reply_frame, injections=injections,
            budget_tokens=input_limit, capacity_source=capacity.source,
            context_limit=capacity.context_limit, output_reserve=capacity.output_reserve,
            world_core_brief=state.meta.world_core_brief,
            world_runtime_policy=state.meta.world_runtime_policy,
            player_persona=state.meta.player_persona,
            player_identity_source=identity_source(state),
            reply_max_tokens=state.meta.reply_max_tokens,
            model_id=settings.model, gateway=settings.gateway,
            model_provider=settings.model_provider,
            prompt_transforms=context_builder.prompt_transforms(state=state),
            **(generation or {}),
        )
        plan = plan_context(ctx, model=settings.model, persona=system)
        composed = plan.prompt
        ctx.planned_prompt = composed
        ctx.plan_id = plan.plan_id
        finish_recall_audit(recall_audit, composed.included_entry_ids, composed.omitted_reasons)
        from mrp.orchestrator.generation_sources import make_provenance, finish_provenance
        provenance = ctx.provenance or make_provenance(state, reply_mode, participants, shared_scene=parallel_plan)
        provenance.baseline_state = context_builder.generation_material_projection(state=state)
        from mrp.orchestrator.scene_frame import shared_scene_source_messages
        shared_sources = shared_scene_source_messages(state, current_turn,
            exclude_message_id=exclude_message_id, exclude_message_ids=set(reply_frame.trigger_message_ids),
            viewer_ids=[group.id]) if shared_frame else []
        ctx.provenance = finish_provenance(
            provenance,
            state, list({m.id: m for m in [*visible, *shared_sources]}.values()),
            recall_audit=recall_audit, include_player_inner=False,
        )
        response_style = next((json.loads(item.reason) for item in ctx.injections
                               if item.entry_id.startswith("response_style:")), None)
        trace = {
            "system": system, "user": composed.text,
            "visible_messages": visible, "actor_labels": reply_frame.actor_labels,
            "reply_frame": reply_frame, "omitted_message_ids": [
                message_id for message_id in [m.id for m in visible]
                if message_id not in composed.included_message_ids
            ],
            "capacity_source": capacity.source, "context_limit": capacity.context_limit,
            "input_limit": input_limit, "output_reserve": capacity.output_reserve,
            "group_details": group_details,
            "source_id": str(group.source.get("archive_id") or group.id),
            "shared_scene_frame": shared_frame,
            "parallel_scene_plan": parallel_plan,
            "response_style": response_style,
            "memory_recall": recall_audit,
            "memory_ids": [item.entry_id for item in injections if item.source == "memory" and item.entry_id in composed.included_entry_ids],
            "memory_injections": [item.model_dump(mode="json") for item in injections if item.source == "memory" and item.entry_id in composed.included_entry_ids],
            "injected_entry_ids": composed.included_entry_ids,
            "tokens_by_section": {**composed.tokens_by_section, "group_system": estimate_tokens(system)},
            "ctx": ctx, "composed": composed,
        }
        if self.container.fake_mode:
            trace["finish_reason"] = "stop"
            count_label = f" ×{group.count}" if group.count is not None else ""
            return f"{group.label}{count_label}注意到你的举动，几名成员彼此交换眼神，等待你的下一步。", {
                "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
            }, trace
        messages = [{"role": "system", "content": system}, {"role": "user", "content": composed.text}]
        def archive_request(body):
            request_id = self.container.request_archive.write(
                state.meta.id, group.id, current_turn, "group", body,
                plan_id=ctx.plan_id, player_identity_source=ctx.player_identity_source,
                message_id=ctx.message_id, generation_id=ctx.generation_id,
                operation_id=ctx.operation_id, attempt_id=ctx.attempt_id,
            )
            if request_id:
                ctx.request_ids.append(request_id)
        # Match character generation: an unset story limit inherits the global
        # setting; zero omits max_tokens and lets the upstream choose.
        max_tokens = state.meta.reply_max_tokens
        if on_delta is not None:
            text, usage = await asyncio.to_thread(
                chat_stream_with_usage, messages, self._config(), on_delta,
                max_tokens=max_tokens, timeout=60, no_thinking=True, on_request=archive_request,
            )
        else:
            text, usage = await asyncio.to_thread(
                chat_text_with_usage, messages, self._config(),
                max_tokens=max_tokens, timeout=60, no_thinking=True, on_request=archive_request,
            )
        trace["finish_reason"] = usage.get("finish_reason")
        return text.strip(), usage, trace
