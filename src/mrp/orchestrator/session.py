"""会话循环：玩家输入 → 导演决策 → 逐角色回合 → 消息落账 → SSE 事件。

汇合点模块：串联导演/可见性/世界书/记忆/引擎（计划 §13 关键路径终点）。
每轮重组设计（计划 §4）使 swipe 与离席天然成立：
- swipe（R32 原位化）：最后一条角色消息同 id 重生成，旧候选物化为 variants[0]，
  重生成上下文排除 target 自身（design/v3/m8-r32 §3.3）
- 编辑/删除（R32）：改 state.messages 即改下回合上下文，零引擎同步成本
- 离席：消息创建时按在场名单定死 visible_to，回归不回溯

W3 拆分（2026-09-25）：原 1962 行 god object 收敛为 facade——本模块只保留
① 组装（__init__ 建 RunnerRuntime + 六个协作者）② 对外契约（公开方法逐一委托）
③ 会话地基（事件出口/落账/角色索引/随机种子）。逻辑归属：

| 模块                        | 类                     | 职责                                       |
|-----------------------------|------------------------|--------------------------------------------|
| orchestrator/runtime.py     | RunnerRuntime          | 锁/后台任务/检查器 LRU/成本与卫生账        |
| orchestrator/context.py     | ContextBuilder         | 注入/远期压缩/叙事风格                     |
| orchestrator/turn_engine.py | TurnEngine/_DeltaBridge| 回合执行/真伪流式/卫生重试/成本累加        |
| orchestrator/director_flow.py | DirectorFlow         | 导演决策与执行/切场景/确认档               |
| orchestrator/message_ops.py | MessageOps             | swipe/编辑/续写/候选/删除/重跑/在场上场    |
| orchestrator/memory_pipeline.py | MemoryPipeline      | 固化/场景摘要/卫生重查                     |
| orchestrator/assist_material.py | AssistMaterialBuilder | 候选取材（内核仍在 assist.py）           |
| orchestrator/inspections.py | split_sections/build_inspection | 检查器视图与 prompt 分段         |
| orchestrator/channels.py    | parse_channels         | R22 通道语法解析                           |

协作者一律经 runner 读可变依赖（state/engines/memory/judge/padding…），
因此测试与 app 的热替换（`runner.memory = store` 等）语义不变。
"""
from __future__ import annotations
import asyncio

from typing import Any, Callable

from mrp.engines.dsh.process import EngineManager
from mrp.orchestrator.assist_material import AssistMaterialBuilder
from mrp.orchestrator.channels import parse_channels  # noqa: F401 —— 兼容再导出（test_assist）
from mrp.orchestrator.context import ContextBuilder
from mrp.orchestrator.director import Director
from mrp.orchestrator.director_flow import DirectorFlow
from mrp.orchestrator.inspections import (  # noqa: F401 —— 兼容再导出（原私有名保留）
    SECTION_MARKERS as _SECTION_MARKERS,
    build_inspection,
    split_sections as _split_sections,
)
from mrp.orchestrator.lorebook import LorebookEngine
from mrp.orchestrator.memory_pipeline import MemoryPipeline
from mrp.orchestrator.message_ops import MessageOps
from mrp.orchestrator.runtime import (  # noqa: F401 —— EventSink 等为兼容再导出
    DEFAULT_INSPECTION_CAP,
    DirectorJudgeLike,
    EventSink,
    HygieneJudgeLike,
    MemorySearchLike,
    NullSink,
    RunnerRuntime,
    active_characters,
    active_scene,
    append_message,
    characters_by_id,
)
from mrp.orchestrator.turn_engine import _DeltaBridge, TurnEngine  # noqa: F401 —— 兼容再导出
from mrp.orchestrator.worldline_state import record_message_revision
from mrp.shared.models import Character, Message, SessionState

MEMORY_TOP_K = 4  # 兼容常量（历史遗留；实际 k 取 meta.memory_top_k）


class SessionRunner:
    """驱动一个 SessionState 的回合循环（facade）。"""

    def __init__(
        self,
        state: SessionState,
        engine_manager: EngineManager,
        *,
        lorebooks: list | None = None,  # list[Lorebook]（流C 类型，延迟导入避免环）
        memory_store: MemorySearchLike | None = None,
        sink: EventSink | None = None,
        rng_seed: int | None = None,
        hygiene_judge: HygieneJudgeLike | None = None,
        director_judge: DirectorJudgeLike | None = None,
        transition_llm: Any = None,  # Callable[[list[dict]], str]——场景过渡生成（None=模板）
        episodic_consolidator: Any = None,   # R36.1（memory_v2.EpisodicConsolidator）
        scene_summarizer: Any = None,        # R36.3（memory_v2.SceneSummarizer）
        padding_gen: Any = None,             # R37.4（padding.PaddingGenerator）
        assist_generator: Any = None,        # M12-R41（assist.AssistGenerator，手动候选）
        writing_generator: Any = None,
        group_responder: Any = None,         # M26：共享群体回应适配器
        app_settings: Any = None,            # machine-global prompt and generation settings
        world_resolver: Any = None,          # library world lookup for linked story archives
        prompt_preset_resolver: Any = None,
    ) -> None:
        # ---- 可热替换依赖（协作者运行时经 runner 读取，勿复制进协作者）----
        self.state = state
        self.engines = engine_manager
        self.lorebooks = lorebooks or []
        self.memory = memory_store
        self.sink: EventSink = sink or NullSink()
        self.director = Director()
        self.lorebook_engine = LorebookEngine()
        self.hygiene_judge = hygiene_judge
        self.director_judge = director_judge
        self.transition_llm = transition_llm
        self.episodic_consolidator = episodic_consolidator
        self.scene_summarizer = scene_summarizer
        self.padding_gen = padding_gen
        # M12-R41 辅助候选（手动触发）：生成器注入；无运行时账（无缓存/额度——
        # 每次点击都是一次全新生成，用户天然自控）
        self.assist_generator = assist_generator
        from mrp.orchestrator.writing_assistant import WritingGenerator
        self.writing_generator = writing_generator or WritingGenerator()
        self.group_responder = group_responder
        self.app_settings = app_settings
        self.world_resolver = world_resolver
        self.prompt_preset_resolver = prompt_preset_resolver
        # R38 主动性规则引擎（纯逻辑，内部实例化）
        from mrp.orchestrator.proactive import ProactiveEngine

        self.proactive = ProactiveEngine()
        # ---- 运行时状态（锁/后台任务/检查器 LRU/成本账，见 runtime.RunnerRuntime）----
        self.runtime = RunnerRuntime(rng_seed=rng_seed)
        # ---- 协作者（无状态：一律实时读 runner，支持依赖热替换）----
        self.context_builder = ContextBuilder(self)
        self.turns = TurnEngine(self)
        self.director_flow = DirectorFlow(self)
        self.message_ops = MessageOps(self)
        from mrp.orchestrator.message_regeneration import MessageRegeneration
        self.regeneration = MessageRegeneration(self)
        self.memory_pipeline = MemoryPipeline(self)
        self.assist_builder = AssistMaterialBuilder(self)
        from mrp.orchestrator.usage import restore_usage
        restore_usage(self)

    @property
    def hygiene_active(self) -> bool:
        """全局即时开关与故事级开关同时开启时才自动审查。"""
        return bool(
            (self.app_settings is None or getattr(self.app_settings, "hygiene_enabled", False))
            and self.state.meta.hygiene_enabled
            and self.hygiene_judge is not None
        )

    # ---------- 运行时状态的兼容视图（同一份数据；原属性名保留）----------

    @property
    def pending_director(self) -> Any:
        return self.state.pending_director

    @pending_director.setter
    def pending_director(self, value: Any) -> None:
        self.state.pending_director = value

    @property
    def inspections(self):
        return self.runtime.inspections

    @property
    def cost_by_model(self) -> dict[str, dict[str, int]]:
        return self.runtime.cost_by_model

    @property
    def cost_by_character(self) -> dict[str, dict[str, int]]:
        return self.runtime.cost_by_character

    @property
    def cost_by_purpose(self) -> dict[str, dict[str, int]]:
        return self.runtime.cost_by_purpose

    @property
    def hygiene_stats(self) -> dict[str, int]:
        return self.runtime.hygiene_stats

    @property
    def _bg_tasks(self) -> set:
        return self.runtime.bg_tasks

    # ---------- 会话地基 ----------

    def _characters_by_id(self) -> dict[str, Character]:
        return characters_by_id(self.state)

    def _active_characters(self) -> list[Character]:
        return active_characters(self.state)

    def _rng(self, turn: int) -> int:
        """每回合一个可复现种子（决策日志记录的就是它）。"""
        return self.runtime.rng(turn)

    def _append_message(self, msg: Message) -> Message:
        appended = append_message(self.state, msg)
        self._record_message_revision(appended)
        return appended

    def _record_message_revision(self, msg: Message) -> None:
        watermark = (
            self.memory.current_watermark(self.state.meta.id)
            if self.memory is not None and hasattr(self.memory, "current_watermark")
            else None
        )
        record_message_revision(self.state, msg, watermark)

    @property
    def public_head(self):
        from mrp.application.branch_commit import committed_head
        return committed_head(self)

    async def _emit(self, event: str, payload: dict[str, Any], *, lossy: bool = False) -> None:
        from mrp.application.branch_commit import buffer_committed_event
        if buffer_committed_event(self, event, payload, lossy):
            return
        if event.startswith("message."):
            message_id = payload.get("message_id") or payload.get("message", {}).get("id")
            identifiers = self.runtime.generation_events.get(message_id, {})
            if event != "message.error" and payload.get("message"):
                identifiers = {key: payload["message"].get(key) for key in
                               ("generation_id", "operation_id", "attempt_id")}
            payload = {**identifiers, **payload}
            if payload.get("generation_id") in self.runtime.retired_conversation_generations:
                return
            # Read-only snapshot projection retains the exact live prefix. It
            # never places an unfinished generation into persisted story state.
            if event == "message.pending" and payload.get("message"):
                pending = Message.model_validate(payload["message"])
                if pending.scene_id is None:
                    pending.scene_id = self.state.active_scene_id
                self.runtime.pending_messages[message_id] = pending.model_dump(mode="json")
            elif event == "message.delta":
                pending = self.runtime.pending_messages.get(message_id)
                if pending is not None and all(pending.get(key) == payload.get(key)
                        for key in ("generation_id", "attempt_id")):
                    offset = payload.get("offset", len(pending["content"]))
                    if isinstance(offset, int) and 0 <= offset <= len(pending["content"]):
                        pending["content"] = pending["content"][:offset] + payload.get("delta", "")
            elif event in {"message.final", "message.error"}:
                pending = self.runtime.pending_messages.get(message_id)
                if pending is not None and all(pending.get(key) == payload.get(key)
                        for key in ("generation_id", "attempt_id")):
                    self.runtime.pending_messages.pop(message_id, None)
        await self.sink.publish(self.state.meta.id, event, payload, lossy=lossy)

    def active_scene(self):
        """当前场景（无 scenes 的旧构造会话返回 None）。"""
        return active_scene(self.state)

    def _ensure_open(self) -> bool:
        """B5：aclose() 后变更类 API 一律 no-op（安全失败，不抛错）。"""
        return not self.runtime.closed

    # ---------- 公开契约（委托；签名与拆分前逐一相同）----------

    async def player_say(
        self,
        content: str,
        force_character: str | None = None,
        mentions: list[str] | None = None,
        channel: str = "dialogue",
        *,
        client_message_id: str | None = None,
    ) -> list[Message]:
        return await self.turns.player_say(
            content, force_character, mentions, channel,
            client_message_id=client_message_id,
        )

    async def player_say_with_reply_mode(
        self, content: str, force_character: str | None = None,
        mentions: list[str] | None = None, channel: str = "dialogue", *,
        client_message_id: str | None = None, reply_mode: str,
    ) -> list[Message]:
        """Coordinated multi-actor send; kept separate for the legacy facade contract."""
        return await self.turns.player_say(
            content, force_character, mentions, channel,
            client_message_id=client_message_id, reply_mode=reply_mode,
        )

    async def run_character_turn(
        self,
        character: Character,
        turn: int,
        *,
        target: Message | None = None,
        extra_injections: list | None = None,
    ) -> Message:
        return await self.turns.run_character_turn(
            character, turn, target=target, extra_injections=extra_injections
        )

    async def open_round(self) -> list[Message]:
        return await self.message_ops.open_round()

    async def force_turn(self, character_id: str) -> Message | None:
        return await self.message_ops.force_turn(character_id)

    async def swipe(self, message_id: str) -> Message | None:
        return await self.message_ops.swipe(message_id)

    async def edit_message(self, message_id: str, content: str) -> Message | None:
        return await self.message_ops.edit_message(message_id, content)

    async def continue_message(self, message_id: str) -> Message | None:
        return await self.message_ops.continue_message(message_id)

    async def switch_variant(self, message_id: str, index: int) -> Message | None:
        return await self.message_ops.switch_variant(message_id, index)

    async def delete_message(self, message_id: str) -> bool:
        return await self.message_ops.delete_message(message_id)

    async def regenerate_turn(self, message_id: str) -> list[Message] | None:
        return await self.message_ops.regenerate_turn(message_id)

    async def switch_scene_manual(
        self, title: str, description: str = "", member_ids: list[str] | None = None,
        first_speaker_ids: list[str] | None = None,
    ) -> list[Message] | None:
        return await self.director_flow.switch_scene_manual(
            title, description, member_ids, first_speaker_ids
        )

    async def confirm_pending_director(self) -> list[Message] | None:
        return await self.director_flow.confirm_pending_director()

    async def reject_pending_director(self) -> list[Message] | None:
        return await self.director_flow.reject_pending_director()

    async def generate_candidates(self) -> dict:
        return await self.assist_builder.generate_candidates()

    async def draft_candidates(self, intent: str, **kwargs) -> dict:
        from mrp.orchestrator.writing_assistant import WritingAssistant, WritingRequest
        return await WritingAssistant(self).generate(WritingRequest(intent=intent, **kwargs))

    async def set_presence(self, character_id: str, present: bool) -> Message | None:
        return await self.message_ops.set_presence(character_id, present)

    async def add_participant(
        self,
        character: Character,
        *,
        join_current_scene: bool = True,
        entry_brief: str = "",
        persist: Callable[["SessionRunner"], Any] | None = None,
    ) -> bool:
        """Copy a library card into this branch under the turn lock.

        Returns False for an idempotent duplicate. The caller supplies persistence
        so state mutation and the branch checkpoint share one rollback boundary.
        """
        lock = self.runtime.turn_lock
        if lock.locked() and getattr(lock, "_owner_task", None) is not asyncio.current_task():
            raise RuntimeError("回合进行中，暂时不能加入角色")
        await lock.acquire()
        original = self.state.model_copy(deep=True)
        try:
            if character.id in self.state.meta.character_ids:
                return False
            from mrp.shared.player_identity import player_key
            if character.id in {self.state.meta.player_character_id, player_key(self.state)}:
                raise ValueError("玩家角色卡不能同时作为对话角色")
            if character.id in self.state.player_people:
                raise ValueError("该人物已在故事中保存，请通过切换角色或在场状态恢复，不要重复加入")
            if len(self.state.meta.character_ids) >= 20:
                raise ValueError("本故事最多加入 20 名对话角色")
            scene = self.active_scene()
            if join_current_scene and scene is None:
                raise ValueError("当前故事没有可加入的场景")

            snapshot = character.model_copy(deep=True)
            snapshot.present = True
            snapshot.muted = False
            self.state.characters.append(snapshot)
            self.state.meta.character_ids.append(snapshot.id)
            if join_current_scene and scene is not None and snapshot.id not in scene.member_ids:
                scene.member_ids.append(snapshot.id)
            self.state.character_joined_at_seq[snapshot.id] = self.state.next_seq()
            brief = entry_brief.strip()
            if not brief and scene is not None:
                brief = "\n".join(part for part in (scene.title.strip(), scene.description.strip()) if part)
            if brief:
                self.state.character_entry_briefs[snapshot.id] = brief[:4000]
            if persist is not None:
                await persist(self)
            return True
        except BaseException:
            self.state = original
            raise
        finally:
            lock.release()

    def set_muted(self, character_id: str, muted: bool) -> None:
        self.message_ops.set_muted(character_id, muted)

    async def consolidate_all(self, reason: str = "manual") -> list:
        return await self.memory_pipeline.consolidate_all(reason)

    async def recheck_hygiene(self, message_id: str) -> Message | None:
        return await self.memory_pipeline.recheck_hygiene(message_id)

    def busy(self) -> bool:
        """回合进行中（近似判定，单用户场景足够；API 层据此返回 409）。"""
        return bool(self.runtime.active_conversation_run_id or self.runtime.active_turn_run_id
                    or self.runtime.turn_checkpoint_active or self.runtime.turn_lock.locked())

    def inspection(self, character_id: str, turn: int) -> dict[str, Any] | None:
        record = self.inspections.get((character_id, turn))
        if record is None:
            return None
        return build_inspection(record, character_id, turn)

    def cost_report(self) -> dict[str, Any]:
        def _total(store: dict) -> dict[str, int]:
            return {
                k: sum(v[k] for v in store.values())
                for k in ("input_tokens", "output_tokens", "cached_tokens")
            }

        return {
            "by_model": self.cost_by_model,
            "by_character": self.cost_by_character,
            "total": _total(self.cost_by_model),
            "by_purpose": self.cost_by_purpose,  # R34.4：judge 等轻量用途独立账
            "hygiene": self.hygiene_stats,
            "usage_incomplete": self.state.usage_incomplete,
            "call_count": len(self.state.usage_records),
            "scope": "branch_with_inherited_calls",
        }

    async def aclose(self) -> None:
        """B5：关闭 runner——取消全部后台任务（await 结束）→ 清空检查器留档 → 置 closed。

        - 幂等；置位后：后台固化/摘要不再派发（runtime.spawn 守卫），
          变更类 API（player_say/swipe/edit/…）一律 no-op（`_ensure_open` 守卫）；
        - 调用点（留给主线程/app 接线）：会话淘汰或进程关停时 `await runner.aclose()`，
          **不要**在回合进行中调用（会取消该会话的后台固化任务，回合本身不受影响）。
        """
        await self.runtime.close()
        self.inspections.clear()

    # ---------- 兼容委托（原私有方法名保留；app.py/测试/既有文档的调用点）----------

    async def _build_injections(self, character, turn, *, visible=None):
        return await self.context_builder.build_injections(character, turn, visible=visible)

    def _compress_history(self, character, visible):
        return self.context_builder.compress_history(character, visible)

    def _narrative_style(self):
        return self.context_builder.narrative_style()

    async def _maybe_pad(self, content, channel, route_text, turn):
        return await self.turns.maybe_pad(content, channel, route_text, turn)

    def _make_delta_bridge(self, message_id, character_id, turn, initial_offset=0):
        return self.turns.make_delta_bridge(message_id, character_id, turn, initial_offset)

    async def _generate_with_bridge(self, character, ctx, bridge):
        return await self.turns.generate_with_bridge(character, ctx, bridge)

    async def _emit_deltas(self, msg, character_id, turn, content, already_emitted=0):
        return await self.turns.emit_deltas(
            msg, character_id, turn, content, already_emitted=already_emitted
        )

    def _track_cost(self, character_id, model, usage):
        return self.turns.track_cost(character_id, model, usage)

    def _track_purpose_cost(self, purpose, usage):
        return self.turns.track_purpose_cost(purpose, usage)

    def _track_judge_cost(self, usage):
        return self.turns.track_judge_cost(usage)

    async def _run_hygiene_check(self, character, ctx, reply_content):
        return await self.turns.run_hygiene_check(character, ctx, reply_content)

    def _build_judge_input(self, character, ctx, reply_content):
        return self.turns.build_judge_input(character, ctx, reply_content)

    def _hygiene_feedback_text(self, report):
        return TurnEngine.hygiene_feedback_text(report)

    def _maybe_spawn_consolidation(self, turn, reason):
        return self.memory_pipeline.maybe_spawn_consolidation(turn, reason)

    def _spawn_consolidation_task(self, turn, reason):
        return self.memory_pipeline.spawn_consolidation_task(turn, reason)

    def _spawn_scene_summary_task(self, closed_scene):
        return self.memory_pipeline.spawn_scene_summary_task(closed_scene)

    async def _decide_with_hints(self, route_text, channel, turn, mentions, force_character):
        return await self.director_flow.decide_with_hints(
            route_text, channel, turn, mentions, force_character
        )

    async def _llm_decide(self, route_text, channel, turn):
        return await self.director_flow.llm_decide(route_text, channel, turn)

    async def _execute_decision(self, decision, turn):
        return await self.director_flow.execute_decision(decision, turn)

    async def _maybe_followup_async(
        self, last_speaker, spoken, chars, chain_depth, proactive_count, last_reply
    ):
        return await self.director_flow.maybe_followup_async(
            last_speaker, spoken, chars, chain_depth, proactive_count, last_reply
        )

    async def _execute_switch_scene(self, action, turn, first_speaker, keep_members=False):
        return await self.director_flow.execute_switch_scene(
            action, turn, first_speaker, keep_members=keep_members
        )

    async def _generate_transition(self, old, new_scene, action):
        return await self.director_flow.generate_transition(old, new_scene, action)

    def _assist_ready(self):
        return self.assist_builder.ready()

    def _display_name(self, actor):
        return self.assist_builder.display_name(actor)

    def _roster(self):
        return self.assist_builder.roster()

    def _cold_start_material(self):
        return self.assist_builder.cold_start_material()

    def _turn_material(self):
        return self.assist_builder.turn_material()
