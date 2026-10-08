"""导演与场景流：路由决策（R30/R35/R38）、决策执行、切场景、确认档（W3 拆分自 session.py）。"""
from __future__ import annotations

import asyncio
import logging

from mrp.orchestrator import visibility
from mrp.orchestrator.turn_runs import execution
from mrp.shared.models import DirectorDecision, Message, ScoredCandidate, utcnow
from mrp.shared.actor_labels import build_actor_labels
from mrp.shared.player_identity import message_label, player_key

logger = logging.getLogger(__name__)


class DirectorFlow:
    """一个 SessionRunner 的导演流（无状态，一律实时读 runner）。"""

    def __init__(self, runner) -> None:
        self.r = runner

    async def decide_with_hints(
        self, route_text: str, channel: str, turn: int,
        mentions: list[str] | None, force_character: str | None,
        group_mentions: list[str] | None = None,
    ):
        """路由决策：显式 mentions > force > [narration 必查 LLM] > 隐式提及
        > [LLM 导演] > v1 轮盘。规则前置——@/提及路径 0 LLM 调用（R35.4）。"""
        r = self.r
        chars = r._characters_by_id()
        decision = None
        if mentions:
            # 确定性路由：按序去重；离席/静音者照选但回合执行时自然跳过（R30.4）
            ordered: list[str] = []
            for cid in mentions:
                if cid in chars and cid not in ordered:
                    ordered.append(cid)
            decision = DirectorDecision(
                turn=turn,
                trigger="explicit_mention",
                candidates=[
                    ScoredCandidate(character_id=cid, score=0.0, reasons=["explicit_mention"])
                    for cid in ordered
                ],
                chosen=ordered,
                rng_seed=r.runtime.rng(turn),
            )
        elif force_character:
            decision = r.director.decide(
                trigger="manual_force",
                text=route_text,
                characters=r.state.characters,
                history=[m for m in r.state.messages if m.kind != "inner"],
                rng_seed=r.runtime.rng(turn),
                forced_character_id=force_character,
            )
        else:
            from mrp.orchestrator.director import extract_mentions

            use_llm = (
                r.director_judge is not None
                and r.state.meta.director_mode != "rules"
            )
            # narration 必查 LLM（旁白常含场景/时间信号）；dialogue 先查隐式提及
            implicit = [] if channel == "narration" else extract_mentions(route_text, r.state.characters)
            if use_llm and (channel == "narration" or not implicit):
                decision = await self.llm_decide(route_text, channel, turn)
            if decision is None:
                if implicit:
                    decision = r.director.decide(
                        trigger="mention",
                        text=route_text,
                        characters=r.state.characters,
                        history=[m for m in r.state.messages if m.kind != "inner"],
                        rng_seed=r.runtime.rng(turn),
                    )
                else:
                    # v1 轮盘兜底（LLM 不可用 / rules 模式 / fail-open）
                    decision = r.director.decide(
                        trigger="talkativeness",
                        text=route_text,
                        characters=r.state.characters,
                        history=[m for m in r.state.messages if m.kind != "inner"],
                        rng_seed=r.runtime.rng(turn),
                    )
                    if use_llm:
                        decision.rationale = "导演 LLM 不可用，回退规则路由"
        for group_id in group_mentions or []:
            if group_id not in decision.chosen:
                decision.chosen.append(group_id)
            if not any(item.character_id == group_id for item in decision.candidates):
                decision.candidates.append(ScoredCandidate(
                    character_id=group_id, score=0.0,
                    reasons=["explicit_group" if group_id in (mentions or []) else "group_cue"],
                ))
        live = execution.get()
        if live is not None:
            live.guard()
        r.state.director_log.append(decision)
        await r._emit(
            "director.decision", {"decision": decision.model_dump(mode="json")}
        )
        return decision

    async def llm_decide(self, route_text: str, channel: str, turn: int):
        """LLM 导演三选一（fail-open 返回 None 时由调用方回退 v1 轮盘）。"""
        from mrp.orchestrator.director_llm import CharacterBrief, DirectorJudgeInput

        r = self.r
        scene = r.active_scene()
        interjecters = {c.id for c in r.proactive.interject_candidates(r.state.characters)}
        briefs = []
        for c in r.state.characters:
            if not (c.present and not c.muted):
                continue
            hook = (c.card.personality or c.card.description or "")[:80]
            briefs.append(CharacterBrief(
                id=c.id, name=c.card.name, personality_hook=hook,
                talkativeness=c.talkativeness,
                last_speak_turn=r.state.last_turn_of(c.id),
                interject_hint=c.id in interjecters,  # R38.1：意愿标记进导演 prompt
            ))
        recent = []
        public = [m for m in r.state.messages if m.status == "final" and m.kind != "inner"
                  and not m.control_event and m.visible_to == "all"][-8:]
        labels = build_actor_labels(r.state, public)
        for m in public:
            recent.append(f"{message_label(r.state, m, labels)}: {m.content[:80]}")
        data = DirectorJudgeInput(
            player_text=route_text[:400],
            channel=channel,
            scene_title=scene.title if scene else "",
            scene_description=scene.description if scene else "",
            present=briefs,
            recent_messages=recent,
            turn=turn,
        )
        try:
            out = await asyncio.to_thread(r.director_judge.decide, data)
        except Exception:  # noqa: BLE001 —— 双保险 fail-open：编排层不让导演挡回合
            logger.warning("director judge failed (turn=%s)", turn, exc_info=True)
            return None
        if out is None:
            return None
        action = out.get("action", "pick_speaker")
        trigger = {
            "pick_speaker": "llm_route",
            "switch_scene": "llm_switch_scene",
            "end_scene": "llm_end_scene",
        }[action]
        scene_obj = None
        if action in ("switch_scene", "end_scene"):
            raw = out.get("scene") or {}
            from mrp.shared.models import SceneAction

            scene_obj = SceneAction(
                title=raw.get("title") or ("收束" if action == "end_scene" else "新场景"),
                description=raw.get("description") or "",
                member_ids=raw.get("members") or [c.id for c in r.state.characters if c.present],
                transition_hint=raw.get("transition_hint") or "",
            )
        # R38.1 插话：导演点名的"有话要说"者追加进本轮（仅限开关开启者）
        chosen = list(out.get("chosen") or [])
        candidates = [
            ScoredCandidate(character_id=cid, score=0.0, reasons=["llm_route"])
            for cid in chosen
        ]
        for cid in out.get("interject") or []:
            if cid in interjecters and cid not in chosen:
                chosen.append(cid)
                candidates.append(ScoredCandidate(
                    character_id=cid, score=0.0,
                    reasons=["interject", str(out.get("rationale") or "")[:60]],
                ))
        return DirectorDecision(
            turn=turn,
            trigger=trigger,
            action=action,
            candidates=candidates,
            chosen=chosen,
            rationale=str(out.get("rationale") or "")[:200],
            scene=scene_obj,
            rng_seed=r.runtime.rng(turn),
        )

    async def execute_decision(self, decision, turn: int) -> list[Message]:
        """执行导演决策：场景动作或逐角色回合。"""
        r = self.r
        if decision.action in ("switch_scene", "end_scene") and decision.scene is not None:
            return await self.execute_switch_scene(
                decision.scene, turn, decision.chosen,
                keep_members=(decision.action == "end_scene"),
            )
        chars = r._characters_by_id()
        created: list[Message] = []
        spoken: set[str] = set()  # 本回合已发言角色（R38.2 接话排除）
        proactive_count = 0
        chain_depth = 0
        for cid in decision.chosen:
            character = chars.get(cid)
            if character is None or not character.present or character.muted:
                continue
            if cid in spoken:
                continue  # 已通过接话提前发言（R38.2），不重复
            reply_msg = await r.turns.run_character_turn(character, turn)
            created.append(reply_msg)
            spoken.add(cid)
            # R38.2 接话：每条角色回复后，规则筛候选 → LLM 判定"想不想"
            # （无候选零调用；链深/全局上限由规则闸门保证）
            followup = await self.maybe_followup_async(
                cid, spoken, chars, chain_depth, proactive_count, reply_msg,
            )
            if followup is not None:
                fcid, fmsg = followup
                created.append(fmsg)
                spoken.add(fcid)
                proactive_count += 1
                chain_depth += 1
        # M12-R41（2026-09-24 修订）：候选不再随回合自动生成——玩家点「生成候选」才触发
        # R36.1 常规间隔固化检查（回合完成、消息齐后；后台异步）
        r.memory_pipeline.maybe_spawn_consolidation(turn, "interval")
        return created

    async def maybe_followup_async(
        self, last_speaker: str, spoken: set[str], chars: dict,
        chain_depth: int, proactive_count: int, last_reply: Message,
    ) -> tuple[str, Message] | None:
        """R38.2 接话：规则闸门筛候选（零候选零 LLM）→ judge_followup → 生成。

        主动发言用角色引擎（人格一致性），usage 进主账（run_character_turn
        已计）+ by_purpose["proactive"] 分列。
        """
        from mrp.orchestrator.director_llm import CharacterBrief, FollowupInput

        r = self.r
        live = execution.get()
        if live is not None and last_reply.id in live.run.followups:
            cached = live.run.followups[last_reply.id]
            if cached is None:
                return None
            chosen = chars.get(cached)
            if chosen is None:
                return None
            from mrp.orchestrator.generation_sources import make_provenance, source_ref
            provenance = make_provenance(r.state, "single", [last_speaker, chosen.id],
                reply_to_message_ids=[last_reply.id], scheduling_sources=[source_ref(last_reply)])
            already_committed = any(s.actor_id == chosen.id and s.status == "committed" for s in live.run.slots)
            msg = await r.turns.run_character_turn(chosen, last_reply.turn, provenance=provenance)
            if not already_committed and msg.generation_meta is not None:
                r.turns.track_purpose_cost("proactive", msg.generation_meta.usage, generation_id=msg.generation_meta.generation_id)
            return chosen.id, msg
        limit = max(0, min(3, r.state.meta.proactive_turn_limit))
        cands = r.proactive.followup_candidates(
            last_speaker, spoken, r.state.characters, r.state.messages,
            chain_depth, proactive_count, limit,
        )
        if not cands or r.director_judge is None:
            if live is not None:
                await live.followup(last_reply.id, None)
            return None
        scene = r.active_scene()
        speaker = chars.get(last_speaker)
        data = FollowupInput(
            last_speaker_name=speaker.card.name if speaker else last_speaker,
            reply_text=last_reply.content,
            candidates=[
                CharacterBrief(
                    id=c.id, name=c.card.name,
                    personality_hook=(c.card.personality or c.card.description or "")[:80],
                )
                for c in cands
            ],
            scene_title=scene.title if scene else "",
        )
        try:
            verdict = await asyncio.to_thread(r.director_judge.judge_followup, data)
        except Exception:  # noqa: BLE001 —— fail-open：不接话
            logger.warning("followup judge failed (speaker=%s)", last_speaker, exc_info=True)
            if live is not None:
                await live.followup(last_reply.id, None)
            return None
        if not verdict or not verdict.get("speak"):
            if live is not None:
                await live.followup(last_reply.id, None)
            return None
        chosen = chars.get(verdict.get("chosen") or "")
        if chosen is None:
            if live is not None:
                await live.followup(last_reply.id, None)
            return None
        if live is not None:
            await live.followup(last_reply.id, chosen.id)
        from mrp.orchestrator.generation_sources import make_provenance, source_ref
        provenance = make_provenance(r.state, "single", [last_speaker, chosen.id],
            reply_to_message_ids=[last_reply.id], scheduling_sources=[source_ref(last_reply)])
        msg = await r.turns.run_character_turn(chosen, r.state.current_turn(), provenance=provenance)
        if msg.generation_meta is not None:
            r.turns.track_purpose_cost("proactive", msg.generation_meta.usage, generation_id=msg.generation_meta.generation_id)
        return chosen.id, msg

    async def execute_switch_scene(
        self, action, turn: int, first_speaker: list[str], keep_members: bool = False,
    ) -> list[Message]:
        """R35.2 切场景：关旧场景 → 过渡消息 → 在场名单重置 → 新场景 → 首发言。

        "楼下听不见楼上"由 visible_to 机器保证（场景切换=批量 presence 变更，
        新消息 visible_to 按新在场名单创建时定死）——不做 scene_id 级过滤。
        """
        from mrp.shared.models import Scene as SceneModel

        r = self.r
        live = execution.get()
        if live is not None and live.run.prepared_scene_message_ids:
            created = [m for m in r.state.messages if m.id in live.run.prepared_scene_message_ids]
            chars = r._characters_by_id()
            for cid in first_speaker or []:
                character = chars.get(cid)
                if character is not None and character.present and not character.muted:
                    created.append(await r.turns.run_character_turn(character, turn))
            return created
        before_scene_ids = {m.id for m in r.state.messages}
        old = r.active_scene()
        member_ids = ([c.id for c in r.state.characters if c.present]
            if keep_members else list(action.member_ids))
        controlled = player_key(r.state)
        if controlled != "player" and controlled not in member_ids:
            member_ids.append(controlled)
        new_scene = SceneModel(title=action.title, description=action.description,
            turn_start=turn, member_ids=member_ids)
        # Generate before changing scene membership. A cancellation or stop
        # during this await cannot persist a half-applied scene switch.
        transition = await self.generate_transition(old, new_scene, action)
        if live is not None:
            live.guard()
        departure_messages = []
        if old is not None:
            old.turn_end = turn
            for group in r.state.groups:
                if group.status != "active" or group.id not in old.group_ids:
                    continue
                group.status = "left"
                group.updated_at = utcnow()
                event = r._append_message(Message(
                    session_id=r.state.meta.id, seq=r.state.next_seq(), turn=turn,
                    actor="director", content=f"[群体离开场景]\n{group.label}"
                    + (f" ×{group.count}" if group.count is not None else "")
                    + (f"\n最后状态：{group.current_state[:300]}" if group.current_state else ""),
                    kind="system_event", visible_to="all", scene_id=old.id))
                departure_messages.append(event)
        for character in r.state.characters:
            character.present = character.id in member_ids
        r.state.scenes.append(new_scene)
        r.state.active_scene_id = new_scene.id

        msg = r._append_message(
            Message(
                session_id=r.state.meta.id,
                seq=r.state.next_seq(),
                turn=turn,
                actor="director",
                content=transition,
                kind="scene",
                visible_to=visibility.compute_visible_to(r.state.characters, group_ids=visibility.active_group_ids(r.state)),
            )
        )
        if live is not None:
            await live.scene_prepared([m.id for m in r.state.messages if m.id not in before_scene_ids])
        for departure in departure_messages:
            await r._emit("message.final", {"message": departure.model_dump(mode="json")})
        await r._emit("message.final", {"message": msg.model_dump(mode="json")})
        await r._emit(
            "scene.switched",
            {
                "old_scene": old.model_dump(mode="json") if old else None,
                "new_scene": new_scene.model_dump(mode="json"),
                "transition_message": msg.model_dump(mode="json"),
            },
        )

        # 新场景首发言
        created: list[Message] = [msg]
        chars = r._characters_by_id()
        for cid in first_speaker or []:
            character = chars.get(cid)
            if character is None or not character.present or character.muted:
                continue
            created.append(await r.turns.run_character_turn(character, turn))
        # R36 钩子：旧场景摘要 + 常规窗口固化（场景边界触发）
        if old is not None:
            r.memory_pipeline.spawn_scene_summary_task(old)
        r.memory_pipeline.maybe_spawn_consolidation(turn, "scene")
        return created

    async def generate_transition(self, old, new_scene, action) -> str:
        """过渡消息文本：便宜模型生成 1-3 句时间/地点描写；fail-open 模板。"""
        r = self.r
        parts = [f"场景转换：{(old.title if old else '未知')} → {new_scene.title}。"]
        if new_scene.description:
            parts.append(new_scene.description + "。")
        template = f"（{' '.join(parts)}）"
        if r.transition_llm is None:
            return template
        try:
            public = [m for m in r.state.messages if m.status == "final" and m.kind != "inner"
                      and not m.control_event and m.visible_to == "all"][-2:]
            labels = build_actor_labels(r.state, public)
            recent = "\n".join(
                f"{message_label(r.state, m, labels)}: {m.content[:60]}" for m in public
            ) or "（无）"
            text = await asyncio.to_thread(
                r.transition_llm,
                [
                    {"role": "system", "content": "你是场景过渡描写生成器。输出 1-3 句时间/地点过渡描写，用括号包裹整体，不要对话，不要角色名。只输出描写本身。"},
                    {"role": "user", "content": (
                        f"旧场景：{old.title if old else '未知'}\n新场景：{new_scene.title}"
                        f"——{new_scene.description}\n提示：{action.transition_hint or '无'}\n最近对话：\n{recent}"
                    )},
                ],
            )
            text = (text or "").strip()
            if text:
                if not (text.startswith("（") or text.startswith("(")):
                    text = f"（{text}）"
                return text
            return template
        except Exception:  # noqa: BLE001 —— fail-open
            logger.warning("transition generation failed", exc_info=True)
            return template

    async def switch_scene_manual(
        self, title: str, description: str = "", member_ids: list[str] | None = None,
        first_speaker_ids: list[str] | None = None,
    ) -> list[Message] | None:
        """R35.2 玩家手动切场景（API，trigger=player_scene）。"""
        from mrp.shared.models import SceneAction

        r = self.r
        if not r._ensure_open():  # B5
            return None
        async with r.runtime.turn_lock:
            if not title.strip():
                return None
            present_ids = [c.id for c in r.state.characters if c.present]
            members = member_ids if member_ids is not None else present_ids
            decision = DirectorDecision(
                turn=r.state.current_turn() + 1,
                trigger="player_scene",
                action="switch_scene",
                chosen=[c for c in (first_speaker_ids or []) if c in members],
                rationale="玩家手动切换场景",
                scene=SceneAction(title=title.strip(), description=description, member_ids=members),
                rng_seed=r.runtime.rng(r.state.current_turn() + 1),
            )
            r.state.director_log.append(decision)
            await r._emit(
                "director.decision", {"decision": decision.model_dump(mode="json")}
            )
            return await self.execute_switch_scene(
                decision.scene, decision.turn, decision.chosen,
            )

    async def confirm_pending_director(self) -> list[Message] | None:
        """R35.3 确认档：执行挂起的 LLM 决策。"""
        r = self.r
        if not r._ensure_open():  # B5
            return None
        async with r.runtime.turn_lock:
            if r.pending_director is None:
                return None
            decision = r.pending_director
            r.pending_director = None
            return await self.execute_decision(decision, decision.turn)

    async def reject_pending_director(self) -> list[Message] | None:
        """R35.3 确认档：否决 LLM 决策 → v1 轮盘重算执行。"""
        r = self.r
        if not r._ensure_open():  # B5
            return None
        async with r.runtime.turn_lock:
            if r.pending_director is None:
                return None
            old = r.pending_director
            r.pending_director = None
            decision = r.director.decide(
                trigger="talkativeness",
                text="",
                characters=r.state.characters,
                history=r.state.messages,
                rng_seed=r.runtime.rng(old.turn),
            )
            decision.rationale = "玩家否决 LLM 决策，回退规则路由"
            r.state.director_log.append(decision)
            await r._emit(
                "director.decision", {"decision": decision.model_dump(mode="json")}
            )
            return await self.execute_decision(decision, old.turn)
