"""上下文构建：注入（场景卡/世界书/记忆/潜台词）、远期历史压缩、叙事风格。

W3 拆分自 session.py。C4：`memory.search` 为同步调用（sqlite），
统一用 `asyncio.to_thread` 包裹，避免慢检索阻塞事件循环（签名不变）。
"""
from __future__ import annotations

import asyncio
import logging
import json
import os
from mrp.response_styles import resolve_response_style, response_style_instruction

from mrp.shared.models import Character, Injection, Message, NarrativeStyle
from mrp.shared.prompt import estimate_tokens
from mrp.shared.prompt import player_name_from_persona, expand_prompt_macros
from mrp.shared.player_identity import current_inner, current_identity

logger = logging.getLogger(__name__)


def break_armor_dialogue_count(state, turn: int) -> int:
    """Count player dialogue turns through this turn, excluding inner thoughts."""
    return len({
        message.turn for message in state.messages
        if message.actor == "player"
        and message.kind in ("roleplay", "scene")
        and message.status == "final"
        and message.turn <= turn
    })


def should_inject_break_armor(state, turn: int, mode: str, interval: int) -> bool:
    """Inject at the opening model reply or on every Nth player dialogue turn."""
    if mode == "interval":
        count = break_armor_dialogue_count(state, turn)
        return count > 0 and count % max(1, interval) == 0

    generated_turns = [
        message.turn for message in state.messages
        if message.generation_meta is not None
    ]
    if not generated_turns:
        return True
    first_generated_turn = min(generated_turns)
    return turn == first_generated_turn


class ContextBuilder:
    """一个 SessionRunner 的上下文构建器（无状态，一律实时读 runner）。"""

    def __init__(self, runner) -> None:
        self.r = runner

    def active_prompt_preset(self, *, state=None):
        from mrp.storage.prompt_presets import PromptPreset
        r = self.r
        state = state or r.state
        data = state.meta.prompt_preset_snapshot
        app_settings = getattr(r, "app_settings", None)
        resolver = getattr(r, "prompt_preset_resolver", None)
        if (data is None and not getattr(state, "_generation_frozen", False)
                and app_settings is not None and resolver is not None):
            active_id = getattr(app_settings, "active_prompt_preset_id", None)
            item = resolver(active_id) if active_id else None
            data = item.model_dump(mode="json") if item is not None else None
        return PromptPreset.model_validate(data) if data else None

    def prompt_transforms(self, *, state=None) -> list[dict]:
        preset = self.active_prompt_preset(state=state)
        return [item.model_dump(mode="json") for item in preset.transforms] if preset else []

    def generation_material_projection(self, *, state=None) -> dict:
        """Capture the material actually used, without message bodies or history."""
        from mrp.orchestrator.worldline_state import current_state_projection
        state = state or self.r.state
        projection = current_state_projection(state)
        preset = self.active_prompt_preset(state=state)
        projection["meta"]["prompt_preset_snapshot"] = preset.model_dump(mode="json") if preset else None
        if state is self.r.state:
            projection["lorebooks"] = [book.model_dump(mode="json") for book in self.r.lorebooks]
        overrides = getattr(state, "_generation_memory_overrides", {})
        if overrides:
            projection["_corrected_memory_records"] = [r.model_dump(mode="json") for r in overrides.values()]
        origin = getattr(state, "_correction_origin", None)
        if origin:
            projection["_correction_origin"] = origin
        policies = getattr(state, "_generation_policy_injections", None)
        if policies is not None:
            projection["_generation_policy_injections"] = policies
        return projection

    async def model_capacity(self, character: Character, *, state=None):
        from mrp.orchestrator.model_capacity import ModelCapacity, resolve_model_capacity

        settings = self.r.app_settings
        if settings is None:
            return ModelCapacity(None, None, 8192, "unknown")
        capacity = await resolve_model_capacity(
            settings, character.llm.model,
            reply_max_tokens=(state or self.r.state).meta.reply_max_tokens,
            base_url=character.llm.base_url,
            model_provider=character.llm.effective_provider,
            api_key=os.environ.get(character.llm.api_key_env, ""),
        )
        if capacity.input_limit is None:
            return capacity
        from mrp.shared.prompt import persona_from_card
        source = state or self.r.state
        identity = current_identity(source)
        name = identity.name if identity else player_name_from_persona(source.meta.player_persona)
        persona_tokens = estimate_tokens(persona_from_card(character.card, name))
        return ModelCapacity(capacity.context_limit,
                             max(0, capacity.input_limit - persona_tokens),
                             capacity.output_reserve, capacity.source, capacity.fetched_at)

    def narrative_style(self):
        """旧模式退役；回应风格通过独立注入加入上下文。"""
        # Retain legacy fields on disk, but do not layer their retired mode
        # instructions over the new response preferences.
        return None

    def compress_history(self, character: Character, visible: list[Message],
                         *, input_limit: int | None = None, state=None) -> list[Message]:
        """远期历史压缩：已结束且超过 horizon 的场景，整块替换为场景摘要伪消息。

        伪消息不持久化（prompt 层每回合重算，逐回合重组红利）；无摘要时保留原文
        （瞬态，下次场景关闭触发补摘要）；当前场景 + horizon 内永远原文。
        """
        r = self.r
        state = state or r.state
        # A long-context model should receive the original scene while it fits.
        # Leave some space for cards, world information, and the current request.
        if input_limit is None or sum(estimate_tokens(m.content) for m in visible) < input_limit * 0.7:
            return visible
        horizon = state.meta.memory_compress_horizon_turns
        if horizon <= 0 or not state.scenes:
            return visible
        current_turn = state.current_turn()
        active_id = state.active_scene_id
        # scene_id -> 该场景消息块（保持原序）
        order: list[str | None] = []
        blocks: dict[str | None, list[Message]] = {}
        for m in visible:
            key = m.scene_id
            if key not in blocks:
                blocks[key] = []
                order.append(key)
            blocks[key].append(m)
        scene_by_id = {s.id: s for s in state.scenes}
        out: list[Message] = []
        for key in order:
            scene = scene_by_id.get(key) if key else None
            if (
                scene is None  # 无场景归属的散消息（v1 残留/伪消息）原样
                or key == active_id  # 当前场景永不压缩
                or scene.turn_end is None  # 进行中
                or current_turn - scene.turn_end <= horizon
            ):
                out.extend(blocks[key])
                continue
            summary = (
                r.memory.scene_summary(character.id, scene.id, state.meta.id)
                if r.memory is not None and hasattr(r.memory, "scene_summary")
                else None
            )
            if summary is not None:
                from mrp.orchestrator.generation_sources import memory_matches_baseline
                sources = {m.id: m for m in visible}
                if (not memory_matches_baseline(state, summary)
                        or summary.source_changed or summary.invalidated
                        or any(mid not in sources or sources[mid].dependency_stale
                               for mid in summary.source_message_ids)
                        or any(mid not in sources or sources[mid].fingerprint != fp
                               for mid, fp in summary.source_fingerprints.items())
                        or (getattr(state, "_generation_frozen", False) and not summary.source_message_ids)):
                    summary = None
            if summary is None:
                out.extend(blocks[key])  # 摘要未生成——保留原文
                continue
            out.append(
                Message(
                    id=f"summary-{scene.id}",
                    session_id=state.meta.id,
                    seq=blocks[key][0].seq,
                    turn=scene.turn_start,
                    actor="director",
                    content=(
                        f"[早期场景·{scene.title}（回合 {scene.turn_start}-{scene.turn_end}）]\n"
                        f"{summary.content}"
                    ),
                    kind="system_event",
                    visible_to="all",
                )
            )
        return out

    async def build_injections(
        self, character: Character, turn: int, *, visible: list[Message] | None = None,
        participants: list[str] | None = None, recall_audit: list[dict] | None = None,
        state=None, include_player_inner: bool = True,
    ) -> list[Injection]:
        """世界书扫描 + 记忆检索 → 注入列表。

        visible：调用方已过滤的可见消息（swipe 重生成时排除 target 自身，§3.3）；
        缺省时现算。
        """
        r = self.r
        state = state or r.state
        injections: list[Injection] = []
        meta = state.meta
        entry_brief = state.character_entry_briefs.get(character.id, "").strip()
        if entry_brief:
            injections.append(Injection(
                source="system",
                entry_id=f"character-entry:{character.id}",
                content="[加入故事时的情况]\n" + entry_brief,
                anchor="system",
                order=35,
                reason="角色入场简报",
            ))
        # World archives are materialized into the story at creation. Rebuild the
        # prompt every turn, so archive text appears once in the current request
        # and never accumulates as historical injected messages.
        recent = visible if visible is not None else state.visible_messages_for(character.id)
        scene_for_archive = next((item for item in state.scenes if item.id == state.active_scene_id), None)
        archive_query = "\n".join(m.content for m in recent[-6:])
        if scene_for_archive is not None:
            archive_query += "\n" + scene_for_archive.title + "\n" + scene_for_archive.description
        archive_query = archive_query.casefold()
        for record in meta.world_archive_records:
            if meta.world_runtime_policy == 'compiled':
                continue
            if record.get("visibility", "public") != "public":
                continue
            kind = record.get("kind")
            if kind not in ("background", "biology"):
                continue
            title = str(record.get("title", "")).strip()
            if kind == "biology":
                terms = [title, *(record.get("aliases") or []), *(record.get("tags") or [])]
                if not any(str(term).strip().casefold() in archive_query
                           for term in terms if str(term).strip()):
                    continue
            body = str(record.get("body", "")).strip()
            summary = str(record.get("summary", "")).strip()
            data = record.get("kind_data") or {}
            if not body and not summary and (kind != "biology" or not any(
                str(data.get(field, "")).strip() for field in
                ("appearance", "habitat", "culture", "abilities", "limitations")
            )):
                continue
            details = [f"[{'背景设定' if kind == 'background' else '生物设定'}：{title}]"]
            if summary and summary not in body:
                details.append(summary)
            if kind == "biology":
                for field, label in (("appearance", "外形与寿命"), ("habitat", "栖息环境"),
                                     ("culture", "社会与文化"), ("abilities", "能力"),
                                     ("limitations", "限制与弱点")):
                    value = str(data.get(field, "")).strip()
                    if value and value not in body:
                        details.append(f"{label}：{value}")
            if body:
                details.append(body)
            injections.append(Injection(
                source="archive", entry_id=str(record.get("id", title)),
                content="\n".join(details), anchor="system", order=40,
                reason="背景档案常驻" if kind == "background" else "场景提及生物设定",
            ))
        app_settings = getattr(r, "app_settings", None)
        preserved_policy = (getattr(state, "_generation_policy_injections", None)
                            if getattr(state, "_correction_origin", None) else None)
        preset = self.active_prompt_preset(state=state)
        if preset:
            for segment in sorted(preset.segments, key=lambda item: item.order):
                if segment.enabled and segment.content.strip():
                    identity = current_identity(state)
                    content = expand_prompt_macros(segment.content, character_name=character.card.name,
                        player_name=identity.name if identity else player_name_from_persona(meta.player_persona),
                        story=meta.title)
                    injections.append(Injection(
                        source="system", entry_id=f"preset:{preset.id}:{segment.id}",
                        content=content, anchor=segment.anchor, depth=segment.depth,
                        order=segment.order, reason=f"提示词方案：{preset.name} / {segment.name}",
                    ))
        break_armor_prompt = str(getattr(app_settings, "break_armor_prompt", "") or "").strip()
        break_armor_mode = str(getattr(app_settings, "break_armor_mode", "opening") or "opening")
        break_armor_interval = max(1, int(getattr(app_settings, "break_armor_interval", 10) or 1))
        if preserved_policy is None and break_armor_prompt and should_inject_break_armor(
            state, turn, break_armor_mode, break_armor_interval
        ):
            reason = (
                "故事首次模型回复" if break_armor_mode == "opening"
                else f"第 {break_armor_dialogue_count(state, turn)} 个对话轮次（每 {break_armor_interval} 轮）"
            )
            injections.append(
                Injection(
                    source="system",
                    entry_id="break_armor_prompt",
                    content=f"[破甲 Prompt]\n{break_armor_prompt}",
                    anchor="system",
                    order=1200,
                    reason=reason,
                )
            )
        # R35 场景卡：当前场景注入（order=80——排在 subtext 200/feedback 300 之前）
        scene = next((item for item in state.scenes if item.id == state.active_scene_id), None)
        if scene is not None:
            names_list = [
                c.card.name for c in state.characters
                if c.id in scene.member_ids and c.present
            ]
            controlled = current_identity(state)
            if controlled:
                names_list.append(controlled.name)
            names = "、".join(names_list) or "（无人在场）"
            content = f"[当前场景]\n地点：{scene.title or '未命名'}"
            if scene.description:
                content += f"\n时间/氛围：{scene.description}"
            content += f"\n在场：{names}"
            groups = [
                group for group in state.groups
                if group.id in scene.group_ids and group.status == "active"
            ]
            if groups:
                recent_player = next(
                    (m.content for m in reversed(visible or [])
                     if m.actor == "player" and m.kind == "roleplay"),
                    "",
                )
                group_lines: list[str] = []
                for group in groups:
                    count = f" ×{group.count}" if group.count is not None else ""
                    # 建卡时的 current_state 可能早已与公开剧情脱节；角色只需知道谁在场。
                    line = f"- {group.label}{count}：在场"
                    relevant = any(
                        name and name.casefold() in recent_player.casefold()
                        for name in [group.label, *group.aliases]
                    )
                    if relevant and group.public_brief and group.id != character.id:
                        line += f"；可观察特征：{group.public_brief}"
                    group_lines.append(line)
                if group_lines:
                    content += "\n当前群体参与者：\n" + "\n".join(group_lines)
            injections.append(
                Injection(source="system", entry_id="scene_card", content=content,
                          anchor="system", order=80, placement="current")
            )
        scenario_instructions = state.meta.scenario_instructions.strip()
        if scenario_instructions:
            injections.append(
                Injection(
                    source="system",
                    entry_id="scenario_instructions",
                    content=f"[本局剧情约定]\n{scenario_instructions}",
                    anchor="system",
                    order=70,
                    placement="current",
                )
            )
        pinned_facts = [
            fact for fact in state.pinned_facts
                if fact.visible_to == "all" or character.id in fact.visible_to
        ]
        if pinned_facts:
            pinned_text = (
                "[本局固定信息]\n"
                "以下由玩家明确标记为重要事实。后续回复应与其保持一致；若剧情明示事实已改变，则以新事实为准。\n"
            )
            kept_facts = [f"- {fact.content.strip()}" for fact in pinned_facts]
            if kept_facts:
                injections.append(
                    Injection(
                        source="system",
                        entry_id="pinned_facts",
                        content=pinned_text + "\n".join(kept_facts),
                        anchor="system",
                        order=1000,
                        placement="current",
                        reason=f"玩家固定信息 {len(kept_facts)} 条",
                    )
                )
        for book in (state.lorebooks if state is not r.state else r.lorebooks):
            book = book.model_copy(deep=True)
            if meta.world_runtime_policy == 'raw':
                raw_ids = {item.entry_id for item in injections if item.source == 'archive'}
                # The complete source is already present in this request. Remove
                # its derivatives before scanning, including recursive triggers.
                def repeats_raw(entry):
                    refs = entry.extensions.get('mrp.archive_sources', [])
                    legacy = entry.extensions.get('mrp.archive_source')
                    if isinstance(legacy, dict):
                        refs = [*refs, legacy]
                    return any(isinstance(ref, dict) and ref.get('source_id', ref.get('archive_id')) in raw_ids for ref in refs)
                book.entries = [entry for entry in book.entries if not repeats_raw(entry)]
            identity = current_identity(state)
            macro_args = {"character_name": character.card.name,
                          "player_name": identity.name if identity else player_name_from_persona(meta.player_persona),
                          "story": meta.title}
            for entry in book.entries:
                entry.content = expand_prompt_macros(entry.content, **macro_args)
                entry.keys = [expand_prompt_macros(key, **macro_args) for key in entry.keys]
                entry.secondary_keys = [expand_prompt_macros(key, **macro_args) for key in entry.secondary_keys]
            hits = r.lorebook_engine.scan(
                book, window=visible if visible is not None else state.visible_messages_for(character.id), rng_seed=r.runtime.rng(turn),
                budget=book.token_budget,
            )
            injections.extend(hits)
        if r.memory is not None and meta.memory_enabled:
            msgs = visible if visible is not None else state.visible_messages_for(character.id)
            # R36.2：query 带场景词（提升场景相关记忆召回）；k 会话级可配
            scene = next((item for item in state.scenes if item.id == state.active_scene_id), None)
            query = ((scene.title + " ") if scene and scene.title else "") + " ".join(
                m.content for m in msgs[-2:]
            )[:800]
            from mrp.orchestrator.memory_recall import recall_memories
            injections.extend(await asyncio.to_thread(recall_memories, r.memory, state,
                character.id, msgs, participants, query, audit=recall_audit))

        # R22 玩家内心 → 潜台词注入（反读心：角色只回应可观察的情绪表现）
        inner_msgs = [
            m for m in state.messages
            if m.actor == "player" and m.kind == "inner" and m.status == "final"
            and current_inner(state, m)
        ][-2:] if include_player_inner else []  # Preserve the existing individual-actor behavior.
        if inner_msgs:
            player_name = player_name_from_persona(state.meta.player_persona)
            inner_text = "\n".join(f"- {m.content}" for m in inner_msgs)
            injections.append(
                Injection(
                    source="system",
                    entry_id="player_inner_subtext",
                    content=(
                        f"[玩家情绪暗示（仅作表演参考）]\n"
                        f"{player_name}此刻内心：\n{inner_text}\n\n"
                        f"注意：你只能通过观察{player_name}的微表情、肢体语言和语气变化"
                        f"来间接感知这些情绪。不要直接引用或\"读出\"这些想法——"
                        f"它们通过{player_name}的无意识行为泄露出来。"
                    ),
                    anchor="system",
                    order=200,  # 高优先级：紧贴对话之前
                    placement="current",
                )
            )
        style = resolve_response_style(getattr(r, "app_settings", None), state.meta, character.id)
        if preserved_policy is None and style:
            injections.append(Injection(
                source="system", entry_id=f"response_style:{style['id']}:{style['revision']}",
                content=response_style_instruction(style), anchor="near", order=250,
                reason=json.dumps(style, ensure_ascii=False), placement="instructions",
            ))
        if preserved_policy is not None:
            injections.extend(Injection.model_validate(item) for item in preserved_policy)
        policies = [inj.model_dump(mode="json") for inj in injections
                    if inj.entry_id == "break_armor_prompt" or inj.entry_id.startswith("response_style:")]
        object.__setattr__(state, "_generation_policy_injections", policies)
        return injections
