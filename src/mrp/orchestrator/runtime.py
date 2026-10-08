"""SessionRunner 运行时状态容器与会话地基（W3 拆分）。

集中登记 SessionRunner 的全部运行时实例状态（grep 自原 session.py __init__）：

| 原属性             | 现位置                        | 说明                               |
|--------------------|-------------------------------|------------------------------------|
| `_rng_seed`        | RunnerRuntime.rng_seed        | 每回合可复现种子基底               |
| `pending_director` | SessionState.pending_director| 挂起的 LLM 决策，随故事持久化       |
| `_turn_lock`       | RunnerRuntime.turn_lock       | 同会话回合串行（B21 起含 set_presence）|
| `_consolidation_lock` | RunnerRuntime.consolidation_lock | R36.1 固化串行（B7：手动/后台共用）|
| `_bg_tasks`        | RunnerRuntime.bg_tasks        | 后台任务引用（防 GC，B5 关闭时取消）|
| `inspections`      | RunnerRuntime.inspections     | 注入检查器留档，LRU 上限 200（B4） |
| `cost_by_model`    | RunnerRuntime.cost_by_model   | 主成本账                           |
| `cost_by_character`| RunnerRuntime.cost_by_character | 主成本账                         |
| `cost_by_purpose`  | RunnerRuntime.cost_by_purpose | judge/memory/padding 等独立账（R34.4）|
| `hygiene_stats`    | RunnerRuntime.hygiene_stats   | checks/violations_found/auto_corrected |

SessionRunner 以只读 property 暴露同一份数据（外部读写语义不变）。
"""
from __future__ import annotations

import asyncio
import random
from collections import OrderedDict
from typing import Any, Coroutine, Protocol

from mrp.shared.models import Character, Message, SessionState

DEFAULT_INSPECTION_CAP = 200


class EventSink(Protocol):
    """SSE 事件出口（server/sse.py 实现注入）。lossy 仅 message.delta 使用（R33）。"""

    async def publish(
        self, session_id: str, event: str, payload: dict[str, Any], *, lossy: bool = False
    ) -> None: ...


class NullSink:
    async def publish(
        self, session_id: str, event: str, payload: dict[str, Any], *, lossy: bool = False
    ) -> None:
        pass


class MemorySearchLike(Protocol):
    def search(
        self, character_id: str, query: str, k: int = 4, *,
        current_turn: int | None = None, session_id: str | None = None,
    ) -> list[tuple[Any, float]]: ...


class HygieneJudgeLike(Protocol):
    """R34 校验器钩子（实现见 orchestrator/hygiene.py，鸭子类型注入）。"""

    def judge(self, data: Any) -> Any: ...


class DirectorJudgeLike(Protocol):
    """R35 LLM 导演钩子（实现见 orchestrator/director_llm.py，鸭子类型注入）。"""

    def decide(self, data: Any) -> dict | None: ...


class InspectionCache:
    """注入检查器留档：(character_id, turn) -> {"ctx": TurnContext, "composed": ...}。

    B4：真 LRU（读写都 move_to_end），超出上限淘汰最旧，防长会话无界增长。
    接口兼容原 dict 用法（`cache[key] = v` / `cache.get(key)` / `in` / `len`）。
    """

    def __init__(self, cap: int = DEFAULT_INSPECTION_CAP) -> None:
        self._cap = max(1, int(cap))
        self._data: OrderedDict[tuple, dict[str, Any]] = OrderedDict()

    def __setitem__(self, key: tuple[str, int], value: dict[str, Any]) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._cap:
            self._data.popitem(last=False)

    def __getitem__(self, key: tuple[str, int]) -> dict[str, Any]:
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def get(self, key: tuple[str, int], default: Any = None) -> Any:
        if key not in self._data:
            return default
        self._data.move_to_end(key)
        return self._data[key]

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def clear(self) -> None:
        self._data.clear()

    def record_generation(self, message_id: str, generation_id: str, record: dict[str, Any]) -> None:
        self[("generation", message_id, generation_id)] = record

    def generation(self, message_id: str, generation_id: str):
        return self.get(("generation", message_id, generation_id))


class OwnedTurnLock(asyncio.Lock):
    """Reject foreign mutations for the whole run, including scheduling gaps."""
    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime
        self._owner_task = None
        self._depth = 0

    async def acquire(self):
        task = asyncio.current_task()
        if task is self._owner_task:
            self._depth += 1
            return True
        from mrp.orchestrator.conversation_context import owner, ConversationConflict
        def check():
            run_id = self.runtime.active_conversation_run_id
            if run_id and (owner.get() is None or owner.get()[1] != run_id):
                raise ConversationConflict("对话正在自动推进，请先暂停或停止")
            from mrp.orchestrator.turn_runs import execution, TurnRunConflict
            if self.runtime.closed:
                raise TurnRunConflict("故事已关闭，请重新打开后再操作")
            turn_run_id = self.runtime.active_turn_run_id
            ordinary = execution.get()
            if turn_run_id and (ordinary is None or ordinary.operation_id != turn_run_id):
                raise TurnRunConflict("普通回合正在生成，请先等待或停止")
            if self.runtime.turn_checkpoint_active and ordinary is None:
                raise TurnRunConflict("普通回合正在保存，请稍后再试")
        check()
        result = await super().acquire()
        try:
            check()
        except BaseException:
            self.release()
            raise
        self._owner_task = task
        self._depth = 1
        return result

    def release(self):
        if self._owner_task is not None and self._owner_task is not asyncio.current_task():
            raise RuntimeError("Only the owning task may release the branch lock")
        self._depth -= 1
        if self._depth > 0:
            return
        self._owner_task = None
        self._depth = 0
        super().release()


class RunnerRuntime:
    """一次 SessionRunner 生命周期的运行时状态（不持久化）。"""

    def __init__(self, rng_seed: int | None = None) -> None:
        self.rng_seed = rng_seed if rng_seed is not None else random.randrange(2**31)
        # R36.1：固化任务串行锁（B7 手动/后台共用一把；配合运行时水位复检）
        self.consolidation_lock = asyncio.Lock()
        self.active_conversation_run_id: str | None = None
        self.active_turn_run_id: str | None = None
        self.turn_checkpoint_active = False
        self.retired_conversation_generations: set[str] = set()
        self.turn_lock = OwnedTurnLock(self)  # Same branch, including run ownership.
        self.bg_tasks: set[asyncio.Task] = set()  # 后台任务引用（防 GC，B5 关闭时取消）
        self.inspections = InspectionCache()  # B4：LRU 上限 200
        self.generation_events: dict[str, dict[str, str | None]] = {}
        self.pending_messages: dict[str, dict[str, Any]] = {}
        # 成本累计：model/character -> tokens 累加
        self.cost_by_model: dict[str, dict[str, int]] = {}
        self.cost_by_character: dict[str, dict[str, int]] = {}
        # R34.4：judge 用量独立账（不进 cost_by_model 主账）+ 校验统计
        self.cost_by_purpose: dict[str, dict[str, int]] = {}
        self.hygiene_stats: dict[str, int] = {"checks": 0, "violations_found": 0, "auto_corrected": 0}
        self.turn_errors: list[str] = []  # 本轮部分发言失败；HTTP 回包补偿丢失的 SSE 错误
        self.closed = False  # B5：aclose() 后置位；新后台任务不再派发

    def rng(self, turn: int) -> int:
        """每回合一个可复现种子（决策日志记录的就是它）。"""
        return (self.rng_seed + turn * 7919) % 2**31

    def spawn(self, coro: Coroutine) -> asyncio.Task | None:
        """登记一个后台任务（防 GC + 生命周期可取消）。关闭后拒绝派发。"""
        if self.closed:
            coro.close()  # 避免 "coroutine was never awaited" 警告
            return None
        task = asyncio.get_running_loop().create_task(coro)
        self.bg_tasks.add(task)
        task.add_done_callback(self.bg_tasks.discard)
        return task

    async def close(self) -> None:
        """B5：取消全部后台任务并等它们结束（幂等）。"""
        self.closed = True
        tasks = list(self.bg_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.bg_tasks.clear()


# ---------- 会话地基（纯函数，SessionRunner 与各协作者共用）----------


def characters_by_id(state: SessionState) -> dict[str, Character]:
    return {c.id: c for c in state.characters}


def active_characters(state: SessionState) -> list[Character]:
    return [c for c in state.characters if c.present and not c.muted]


def active_scene(state: SessionState):
    """当前场景（无 scenes 的旧构造会话返回 None）。"""
    sid = state.active_scene_id
    if sid is None:
        return None
    return next((s for s in state.scenes if s.id == sid), None)


def append_message(state: SessionState, msg: Message) -> Message:
    from mrp.shared.player_identity import bind_message
    bind_message(state, msg)
    # R35：所有落账消息统一归属当前场景（单一漏斗；v1 迁移消息已回填）
    if msg.scene_id is None and state.active_scene_id:
        msg.scene_id = state.active_scene_id
    state.messages.append(msg)
    return msg


def snapshot_state(state: SessionState, *, upper_turn: int | None = None) -> SessionState:
    """B23：给 `asyncio.to_thread` worker 的浅快照。

    复制 messages 列表（+ turn 上界）与 scenes 列表；Message/Scene 对象本身共享，
    worker 只读——避免 worker 与事件循环线程争用可变列表。
    """
    snap = state.model_copy(deep=False)
    msgs = list(state.messages)
    if upper_turn is not None:
        msgs = [m for m in msgs if m.turn <= upper_turn]
    snap.messages = msgs
    snap.scenes = list(state.scenes)
    return snap
