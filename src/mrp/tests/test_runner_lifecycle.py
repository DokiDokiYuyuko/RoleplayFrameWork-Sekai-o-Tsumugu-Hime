"""W3 拆分验收：SessionRunner 生命周期与审计修复（B4/B5/B7/B8/B20/B21/B22/B23/C4）。

黑盒为主（facade 契约），个别用例白盒探 runtime 锁/快照。环境隔离同
test_integration.py：import mrp 前设好 MRP_DATA_ROOT / MRP_FAKE_ENGINE。
"""
from __future__ import annotations

import asyncio
import inspect
import os
import tempfile
import threading
import time

_TMP = tempfile.mkdtemp(prefix="mrp-w3-lifecycle-")
os.environ["MRP_DATA_ROOT"] = _TMP
os.environ["MRP_FAKE_ENGINE"] = "1"

import pytest  # noqa: E402

from mrp.orchestrator.session import SessionRunner  # noqa: E402
from mrp.shared.models import MemoryRecord, Message, TokenUsage  # noqa: E402
from mrp.tests.test_integration import make_runner  # noqa: E402


# ---------- 测试替身 ----------


class _FakeMemoryStore:
    """最小记忆库替身：水位只看 episodic（对齐 memory.MemoryStore 语义）。"""

    def __init__(self) -> None:
        self.records: list[MemoryRecord] = []
        self._watermark: dict[tuple[str, str], int] = {}

    def search(self, character_id, query, k=4, *, current_turn=None, session_id=None):
        return []

    def last_consolidated_turn(self, character_id: str, session_id: str) -> int:
        return self._watermark.get((character_id, session_id), 0)

    def add(self, record: MemoryRecord) -> None:
        self.records.append(record)
        if record.kind == "episodic":
            key = (record.character_id, record.session_id)
            self._watermark[key] = max(self._watermark.get(key, 0), record.turn_end)


class _ScriptedConsolidator:
    """可控固化器：按窗口产出确定性记录；delay 用于制造并发重叠。"""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.usage = TokenUsage()
        self.calls: list[tuple[str, int, int]] = []

    def consolidate_window(self, state, character_id: str, watermark: int, current_turn: int):
        if self.delay:
            time.sleep(self.delay)
        self.calls.append((character_id, watermark, current_turn))
        window = [
            m for m in state.visible_messages_for(character_id)
            if m.status == "final" and watermark < m.turn <= current_turn
        ]
        if not window:
            return None
        return MemoryRecord(
            character_id=character_id, session_id=state.meta.id,
            turn_start=watermark + 1, turn_end=current_turn, kind="episodic",
            content=f"{character_id} 窗口 {watermark + 1}-{current_turn}",
        )


class _FlakyConsolidator(_ScriptedConsolidator):
    """首次调用抛错、之后正常——验证 B8 退避期间不持锁。"""

    def consolidate_window(self, state, character_id: str, watermark: int, current_turn: int):
        self.calls.append((character_id, watermark, current_turn))
        if len(self.calls) == 1:
            raise RuntimeError("consolidate boom")
        return super().consolidate_window(state, character_id, watermark, current_turn)


class _CapturingConsolidator:
    """只记录收到的 state 快照，不产记录（B23 白盒）。"""

    def __init__(self) -> None:
        self.usage = TokenUsage()
        self.snapshots: list = []

    def consolidate_window(self, state, character_id: str, watermark: int, current_turn: int):
        self.snapshots.append(state)
        return None


class _ThreadSpyMemory:
    """记录 search 被调用时所在线程（C4）。"""

    def __init__(self) -> None:
        self.thread_names: list[str] = []

    def search(self, character_id, query, k=4, *, current_turn=None, session_id=None):
        self.thread_names.append(threading.current_thread().name)
        return []


async def _drain_bg(runner: SessionRunner) -> None:
    while runner._bg_tasks:
        await asyncio.gather(*list(runner._bg_tasks), return_exceptions=True)


# ---------- B5：aclose 生命周期 ----------


async def test_aclose_cancels_bg_tasks_and_clears_inspections():
    runner, _ = make_runner(replies=["嗯。"])
    store = _FakeMemoryStore()
    runner.memory = store
    runner.episodic_consolidator = _ScriptedConsolidator(delay=0.3)
    runner.state.meta.memory_interval_turns = 1

    created = await runner.player_say("测试甲，你好", force_character="char-a")
    assert len(runner._bg_tasks) == 1
    task = next(iter(runner._bg_tasks))
    assert len(runner.inspections) >= 1  # 回合留档已写入

    await runner.aclose()
    assert runner._bg_tasks == set()
    assert task.cancelled() or task.done()
    assert len(runner.inspections) == 0  # B4/B5：关闭清空
    assert store.records == []  # 被取消的固化没有写入

    await runner.aclose()  # 幂等

    # 关闭后变更类 API 安全 no-op，且不再派发后台任务
    assert await runner.player_say("还在吗", force_character="char-a") == []
    assert await runner.swipe(created[1].id) is None
    assert await runner.set_presence("char-b", False) is None
    assert await runner.consolidate_all() == []
    assert await runner.generate_candidates() == {"options": [], "reason": "closed"}
    runner.memory_pipeline.spawn_consolidation_task(99, "interval")
    assert runner._bg_tasks == set()


# ---------- B4：inspections LRU ----------


async def test_inspections_lru_bound_and_aclose_clear():
    runner, _ = make_runner(replies=["嗯。"])
    await runner.player_say("你好", force_character="char-a")
    assert runner.inspection("char-a", 1) is not None  # 真回合留档可查

    for i in range(250):
        runner.inspections[(f"c{i}", i)] = {"marker": i}
    assert len(runner.inspections) == 200  # 上限淘汰
    assert ("char-a", 1) not in runner.inspections
    assert runner.inspection("char-a", 1) is None
    assert ("c249", 249) in runner.inspections

    # 真 LRU：读一下 c50 再插一条，c50 因被提升而幸存
    runner.inspections.get(("c50", 50))
    runner.inspections[("new", 0)] = {"marker": "new"}
    assert ("c50", 50) in runner.inspections

    await runner.aclose()
    assert len(runner.inspections) == 0


# ---------- B7：手动/后台固化共用一把锁 ----------


async def test_manual_and_bg_consolidation_share_lock():
    runner, _ = make_runner(replies=["嗯。"])
    store = _FakeMemoryStore()
    runner.memory = store
    runner.episodic_consolidator = _ScriptedConsolidator(delay=0.15)  # 拉长重叠窗口
    runner.state.meta.memory_interval_turns = 1

    await runner.player_say("测试甲，你好", force_character="char-a")  # 触发后台固化
    manual = await runner.consolidate_all(reason="manual")  # 并发手动固化
    await _drain_bg(runner)
    await runner.aclose()

    for cid in ("char-a", "char-b"):
        recs = [r for r in store.records if r.character_id == cid and r.kind == "episodic"]
        assert len(recs) == 1, f"{cid} 出现并发重复固化：{[(r.turn_start, r.turn_end) for r in recs]}"
    assert len(store.records) == 2  # 每角色恰一条
    assert all(r.turn_start <= r.turn_end for r in manual)  # 手动产出（若有）窗口合法

    # 窗口不重叠（同一角色按 turn_start 排序，下一起点 > 上一终点）
    for cid in ("char-a", "char-b"):
        recs = sorted(
            (r for r in store.records if r.character_id == cid), key=lambda r: r.turn_start
        )
        for a, b in zip(recs, recs[1:]):
            assert b.turn_start > a.turn_end, f"{cid} 窗口重叠：{a.turn_start}-{a.turn_end}/{b.turn_start}-{b.turn_end}"


# ---------- B8：固化重试的退避不持锁 ----------


async def test_retry_backoff_holds_no_lock(monkeypatch):
    import mrp.orchestrator.memory_pipeline as mp

    monkeypatch.setattr(mp, "_CONSOLIDATION_RETRY_DELAY_S", 0.3)
    runner, _ = make_runner(replies=["嗯。"])
    store = _FakeMemoryStore()
    flaky = _FlakyConsolidator()
    runner.memory = store
    runner.episodic_consolidator = flaky
    runner.state.meta.memory_interval_turns = 1

    await runner.player_say("测试甲，你好", force_character="char-a")

    deadline = time.monotonic() + 2.0  # 等后台任务走到失败后的退避
    while flaky.calls == [] and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert flaky.calls, "后台固化未启动"
    await asyncio.sleep(0.05)  # 此刻应在 sleep(0.3) 退避中
    assert not runner.runtime.consolidation_lock.locked(), "B8：退避 sleep 不得持锁"

    await _drain_bg(runner)
    await runner.aclose()
    assert len(flaky.calls) >= 3  # 首次失败 + 重试（char-a/char-b）
    assert [r.character_id for r in store.records].count("char-a") == 1  # 重试成功且只写一条


# ---------- B20：swipe 不重复发 pending ----------


async def test_swipe_emits_single_pending_event():
    runner, sink = make_runner(replies=["旧回复", "新回复"])
    created = await runner.player_say("测试甲，你怎么看？")
    sink.events.clear()

    new = await runner.swipe(created[1].id)
    assert new is not None and new.content == "新回复"
    names = sink.names()
    assert names.count("message.pending") == 1, f"pending 事件重复：{names}"
    assert names[0] == "message.pending"  # 仍是该回合的起手事件
    assert len(created[1].variants) == 2  # 旧候选物化 + 新候选


# ---------- B21：set_presence 纳入回合锁 ----------


async def test_set_presence_serialized_by_turn_lock():
    runner, _ = make_runner(replies=["嗯。"])
    char_b = runner.state.character("char-b")
    await runner.runtime.turn_lock.acquire()
    try:
        task = asyncio.create_task(runner.set_presence("char-b", False))
        await asyncio.sleep(0.05)
        assert not task.done(), "set_presence 应在回合锁上等待"
        assert char_b.present is True, "等待期间不得改动在场名单"
    finally:
        runner.runtime.turn_lock.release()
    msg = await asyncio.wait_for(task, timeout=1.0)
    assert msg is not None and char_b.present is False
    assert "离开了场景" in msg.content


# ---------- B22：recheck_hygiene 统计口径 ----------


async def test_recheck_hygiene_counters_aligned():
    from mrp.orchestrator.hygiene import FakeJudge

    runner, _ = make_runner(replies=["嗯。"])
    created = await runner.player_say("测试甲，你怎么看？")
    msg = created[1]
    runner.hygiene_judge = FakeJudge(mode="flag")  # 回合后再注入：只走重查路径
    base = dict(runner.hygiene_stats)

    out = await runner.recheck_hygiene(msg.id)
    assert out is not None and out.hygiene is not None and not out.hygiene.passed
    assert runner.hygiene_stats["checks"] == base["checks"] + 1
    assert runner.hygiene_stats["violations_found"] == base["violations_found"] + 1  # B22 对齐
    assert runner.cost_by_purpose["judge"]["input_tokens"] >= 8  # 用量进独立账


# ---------- B23：to_thread worker 只读浅快照 ----------


async def test_consolidation_uses_shallow_snapshot():
    runner, _ = make_runner(replies=["嗯。"])
    await runner.player_say("测试甲，你怎么看？", force_character="char-a")
    cap = _CapturingConsolidator()
    runner.memory = _FakeMemoryStore()
    runner.episodic_consolidator = cap

    await runner.consolidate_all(reason="manual")
    assert cap.snapshots
    snap = cap.snapshots[0]
    assert snap is not runner.state
    assert snap.messages is not runner.state.messages  # 列表副本
    assert snap.scenes is not runner.state.scenes
    assert len(snap.messages) == len(runner.state.messages)

    # 上界：turn=1 的快照不含更晚回合的消息
    cap.snapshots.clear()
    runner.state.messages.append(
        Message(id="future", session_id=runner.state.meta.id, seq=999, turn=99,
                actor="player", content="未来消息", kind="roleplay",
                visible_to="all", status="final")
    )
    await runner.memory_pipeline._consolidate_character(
        "char-a", 1, min_interval=0, retries=1, reason="test")
    assert cap.snapshots
    assert all(m.turn <= 1 for m in cap.snapshots[-1].messages)


# ---------- C4：memory.search 走线程 ----------


async def test_memory_search_runs_off_event_loop_thread():
    runner, _ = make_runner(replies=["嗯。"])
    spy = _ThreadSpyMemory()
    runner.memory = spy

    await runner.player_say("测试甲，你怎么看？")
    assert spy.thread_names, "记忆检索未被调用"
    assert threading.main_thread().name not in spy.thread_names, (
        f"C4：memory.search 仍在事件循环线程执行：{spy.thread_names}"
    )


# ---------- 契约冻结：facade 完备性（grep 调用点清单） ----------

_PUBLIC_CALLSITES = [
    "player_say", "run_character_turn", "open_round", "force_turn", "swipe", "edit_message",
    "continue_message", "switch_variant", "delete_message", "regenerate_turn", "recheck_hygiene",
    "switch_scene_manual", "confirm_pending_director", "reject_pending_director",
    "generate_candidates", "draft_candidates", "set_presence", "set_muted", "inspection",
    "cost_report", "busy", "consolidate_all", "aclose", "active_scene",
]

_PRIVATE_CALLSITES = [
    # app.py / 测试直接引用的（_bg_tasks 为 property，单列在 _RUNTIME_ATTRS）
    "_spawn_consolidation_task", "_compress_history",
    # 拆分后保留的兼容委托（原私有方法名）
    "_maybe_spawn_consolidation", "_spawn_scene_summary_task", "_build_injections",
    "_narrative_style", "_maybe_pad", "_make_delta_bridge", "_generate_with_bridge",
    "_emit_deltas", "_track_cost", "_track_purpose_cost", "_track_judge_cost",
    "_run_hygiene_check", "_build_judge_input", "_hygiene_feedback_text",
    "_decide_with_hints", "_llm_decide", "_execute_decision", "_maybe_followup_async",
    "_execute_switch_scene", "_generate_transition", "_assist_ready", "_display_name",
    "_roster", "_cold_start_material", "_turn_material",
    "_characters_by_id", "_active_characters", "_rng", "_append_message", "_emit",
]

_RUNTIME_ATTRS = [
    "state", "engines", "lorebooks", "memory", "sink", "director", "lorebook_engine",
    "hygiene_judge", "director_judge", "transition_llm", "episodic_consolidator",
    "scene_summarizer", "padding_gen", "assist_generator", "proactive",
]


def test_facade_surface_complete():
    """拆分后仍可调用：公开方法 + 外部引用过的私有方法 + 可写运行时属性。"""
    for name in _PUBLIC_CALLSITES + _PRIVATE_CALLSITES:
        attr = getattr(SessionRunner, name, None)
        assert attr is not None, f"facade 缺少方法：{name}"
        assert callable(attr), f"facade 成员不可调用：{name}"

    # 运行时状态走 property（读写同一份 RunnerRuntime 数据）
    for name in ("pending_director", "inspections", "cost_by_model", "cost_by_character",
                 "cost_by_purpose", "hygiene_stats", "_bg_tasks"):
        assert isinstance(getattr(SessionRunner, name), property), f"{name} 应为 property"

    # 可热替换依赖属性挂在实例上（测试/app 会整体替换：runner.memory = store 等）
    runner, _ = make_runner()
    for name in _RUNTIME_ATTRS:
        assert hasattr(runner, name), f"facade 缺少可替换依赖属性：{name}"
    sentinel = object()
    runner.memory = sentinel
    assert runner.memory is sentinel  # 赋值即生效（协作者实时读 runner）

    # 关键签名冻结（对外契约）
    params = inspect.signature(SessionRunner.player_say).parameters
    assert list(params) == ["self", "content", "force_character", "mentions", "channel", "client_message_id"]
    assert params["channel"].default == "dialogue"
    assert list(inspect.signature(SessionRunner.switch_scene_manual).parameters) == [
        "self", "title", "description", "member_ids", "first_speaker_ids",
    ]
    assert list(inspect.signature(SessionRunner.consolidate_all).parameters) == ["self", "reason"]

    # 实例化后 property 可读写（pending_director 外部赋值语义保留）
    runner.pending_director = "sentinel"
    assert runner.pending_director == "sentinel"
    runner.pending_director = None
    assert runner.pending_director is None
    assert runner.inspections is runner.runtime.inspections
    assert runner._bg_tasks is runner.runtime.bg_tasks
    assert runner.busy() is False
