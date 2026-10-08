"""W4：EngineManager 并发修复回归（B9 启动期替换、B10 回收不阻塞、
B11 锁清理、同角色 generate 串行、health_snapshot、预热去重）。

全部用可控假引擎（零真实进程）。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from mrp.engines.dsh.process import EngineManager
from mrp.engines.fake import FakeEngine
from mrp.server.warmup import pick_characters, warm_characters, warmup_concurrency, warmup_max
from mrp.shared.models import Character, CharacterCard, EngineReply, TokenUsage, TurnContext


def make_char(cid: str = "char-a", name: str = "测试甲", **kw) -> Character:
    return Character(id=cid, card=CharacterCard(name=name), **kw)


def _ctx(cid: str = "char-a") -> TurnContext:
    return TurnContext(session_id="s", character_id=cid, turn=1)


class SlowStartEngine:
    """start 慢（模拟 dsh 首启），记录实例数。"""

    def __init__(self) -> None:
        self.alive = False
        self.starts = 0

    async def start(self, character: Character) -> None:
        self.starts += 1
        await asyncio.sleep(0.1)
        self.alive = True

    async def stop(self) -> None:
        self.alive = False

    async def is_alive(self) -> bool:
        return self.alive

    async def generate(self, ctx, *, on_delta=None):  # pragma: no cover
        raise NotImplementedError


class SlowStopEngine:
    """stop 慢（模拟 dsh 进程回收），stopping 事件用于确定性同步。"""

    def __init__(self) -> None:
        self.alive = False
        self.stopping = asyncio.Event()

    async def start(self, character: Character) -> None:
        self.alive = True

    async def stop(self) -> None:
        self.stopping.set()
        await asyncio.sleep(0.4)
        self.alive = False

    async def is_alive(self) -> bool:
        return self.alive

    async def generate(self, ctx, *, on_delta=None):  # pragma: no cover
        raise NotImplementedError


class CountingEngine:
    """记录并发 generate 峰值（验证同角色串行 / 跨角色并行）。"""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.alive = False
        self.concurrent = 0
        self.max_concurrent = 0
        self.turns = 0

    async def start(self, character: Character) -> None:
        self.alive = True

    async def stop(self) -> None:
        self.alive = False

    async def is_alive(self) -> bool:
        return self.alive

    async def generate(self, ctx, *, on_delta=None) -> EngineReply:
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        await asyncio.sleep(self.delay)
        self.concurrent -= 1
        self.turns += 1
        return EngineReply(
            content="ok", finish_reason="stop", usage=TokenUsage(), engine_session_id="x"
        )


class TraceEngine:
    """把 start/stop 的起止时刻写进共享 log（验证"新旧不并存"）。"""

    def __init__(self, idx: int, log: list[tuple[str, int]]) -> None:
        self.idx = idx
        self.log = log
        self.alive = False

    async def start(self, character: Character) -> None:
        self.log.append(("start", self.idx))
        self.alive = True

    async def stop(self) -> None:
        self.log.append(("stop_begin", self.idx))
        await asyncio.sleep(0.2)
        self.alive = False
        self.log.append(("stop_end", self.idx))

    async def is_alive(self) -> bool:
        return self.alive

    async def generate(self, ctx, *, on_delta=None):  # pragma: no cover
        raise NotImplementedError


# ---------- B9：启动期被 shutdown_all 替换 ----------


async def test_acquire_survives_shutdown_all_during_start():
    engines: list[SlowStartEngine] = []

    def factory() -> SlowStartEngine:
        e = SlowStartEngine()
        engines.append(e)
        return e

    mgr = EngineManager(engine_factory=factory)
    ch = make_char()
    task = asyncio.ensure_future(mgr.acquire(ch))
    await asyncio.sleep(0.02)  # acquire 已注册并进入 start
    await mgr.shutdown_all()  # 启动期清空注册表（旧实现这里 KeyError）
    eng = await asyncio.wait_for(task, timeout=2.0)
    assert await eng.is_alive()
    assert eng is engines[1]  # 野引擎被弃用，重新走启动路径
    assert not engines[0].alive  # 野引擎已停
    await mgr.shutdown_all()


async def test_acquire_gives_up_with_clear_error_after_repeated_shutdowns():
    holder: dict = {}
    attempts = {"n": 0}

    class SelfShutdownEngine:
        async def start(self, character: Character) -> None:
            attempts["n"] += 1
            await holder["mgr"].shutdown_all()  # 每次启动期间都被清空

        async def stop(self) -> None:
            pass

        async def is_alive(self) -> bool:
            return False

        async def generate(self, ctx, *, on_delta=None):  # pragma: no cover
            raise NotImplementedError

    mgr = EngineManager(engine_factory=SelfShutdownEngine)
    holder["mgr"] = mgr
    with pytest.raises(RuntimeError, match="shutdown_all/stop_engine"):
        await asyncio.wait_for(mgr.acquire(make_char()), timeout=2.0)
    assert attempts["n"] == 3  # 重试上限


# ---------- B10/B11：sweep_idle ----------


async def test_sweep_idle_does_not_block_acquire():
    """sweep 在停一台慢引擎时，另一角色的 acquire 不应被全局锁挡住。"""
    engines: list[SlowStopEngine] = []

    def factory() -> SlowStopEngine:
        e = SlowStopEngine()
        engines.append(e)
        return e

    mgr = EngineManager(engine_factory=factory, idle_timeout_s=0.0)
    a = make_char("char-a")
    b = make_char("char-b", "测试乙")
    await mgr.acquire(a)  # a 空闲可回收；b 从未注册
    sweep_task = asyncio.ensure_future(mgr.sweep_idle())
    await asyncio.wait_for(engines[0].stopping.wait(), timeout=1.0)  # 确认 sweep 正在 stop
    t0 = time.monotonic()
    await asyncio.wait_for(mgr.acquire(b), timeout=1.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"acquire 被 sweep 的 stop 阻塞了 {elapsed:.3f}s"
    assert await sweep_task == 1
    await mgr.shutdown_all()


async def test_sweep_idle_cleans_per_character_locks():
    mgr = EngineManager(engine_factory=CountingEngine, idle_timeout_s=0.0)
    ch = make_char()
    await mgr.generate(ch, _ctx())  # acquire + generate（建立 _turn_locks 条目）
    assert ch.id in mgr._turn_locks and ch.id in mgr._start_locks
    assert await mgr.sweep_idle() == 1
    assert ch.id not in mgr._start_locks  # B11：无界增长修复
    assert ch.id not in mgr._turn_locks
    assert mgr.active_ids() == []
    await mgr.shutdown_all()


async def test_sweep_idle_skips_engine_with_inflight_turn():
    mgr = EngineManager(engine_factory=lambda: CountingEngine(delay=0.2), idle_timeout_s=0.0)
    ch = make_char()
    task = asyncio.ensure_future(mgr.generate(ch, _ctx()))
    await asyncio.sleep(0.05)  # generate 已进入引擎
    assert await mgr.sweep_idle() == 0  # 回合在途 → 不回收
    await asyncio.wait_for(task, timeout=1.0)
    assert await mgr.sweep_idle() == 1  # 回合结束后可回收
    await mgr.shutdown_all()


async def test_stop_engine_does_not_block_other_characters():
    """stop_engine 的慢 stop 不得挡住其他角色的 acquire（续修：锁外 stop）。"""
    engines: list[SlowStopEngine] = []

    def factory() -> SlowStopEngine:
        e = SlowStopEngine()
        engines.append(e)
        return e

    mgr = EngineManager(engine_factory=factory)
    a, b = make_char("char-a"), make_char("char-b", "测试乙")
    await mgr.acquire(a)
    stop_task = asyncio.ensure_future(mgr.stop_engine(a.id))
    await asyncio.wait_for(engines[0].stopping.wait(), timeout=1.0)  # 已进入 stop
    t0 = time.monotonic()
    await asyncio.wait_for(mgr.acquire(b), timeout=1.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"acquire 被 stop_engine 的慢 stop 阻塞了 {elapsed:.3f}s"
    await asyncio.wait_for(stop_task, timeout=1.0)
    assert a.id not in mgr.active_ids()
    await mgr.shutdown_all()


async def test_stop_engine_serializes_with_same_character_acquire():
    """stop 慢时，同角色的新引擎必须等 stop 完成才启动（不新旧并存，续修核心）。"""
    log: list[tuple[str, int]] = []
    counter = {"n": 0}

    def factory() -> TraceEngine:
        counter["n"] += 1
        return TraceEngine(counter["n"] - 1, log)

    mgr = EngineManager(engine_factory=factory)
    ch = make_char()
    e0 = await mgr.acquire(ch)  # E0 已启动
    stop_task = asyncio.ensure_future(mgr.stop_engine(ch.id))
    await asyncio.sleep(0.05)  # stop_engine 已摘除 E0 并进入其 stop
    acquire_task = asyncio.ensure_future(mgr.acquire(ch))
    _, e1 = await asyncio.wait_for(asyncio.gather(stop_task, acquire_task), timeout=2.0)
    assert log == [
        ("start", 0),
        ("stop_begin", 0),
        ("stop_end", 0),  # 旧引擎彻底停掉之后……
        ("start", 1),  # ……才启动新引擎
    ], log
    assert e1 is not e0 and await e1.is_alive()
    assert mgr.active_ids() == [ch.id]
    await mgr.shutdown_all()


async def test_stop_engine_cleans_locks_via_sweep_gc():
    """续修：stop_engine 不就地清锁（避免同角色等待者拿到第二把锁），由 sweep GC 兜底。"""
    mgr = EngineManager(engine_factory=CountingEngine, idle_timeout_s=0.0)
    ch = make_char()
    await mgr.generate(ch, _ctx())  # 建立 _start_locks/_turn_locks 条目
    await mgr.stop_engine(ch.id)
    assert mgr.active_ids() == []
    assert ch.id in mgr._start_locks  # 就地保留（见 _cleanup_char_locks 注释）
    await mgr.sweep_idle()  # 无引擎 + 无 last_used → GC
    assert ch.id not in mgr._start_locks and ch.id not in mgr._turn_locks
    await mgr.shutdown_all()


# ---------- 同角色 generate 串行 ----------


async def test_same_character_concurrent_acquire_starts_once():
    engines: list[SlowStartEngine] = []

    def factory() -> SlowStartEngine:
        e = SlowStartEngine()
        engines.append(e)
        return e

    mgr = EngineManager(engine_factory=factory)
    ch = make_char()
    got = await asyncio.gather(*(mgr.acquire(ch) for _ in range(3)))
    assert len(engines) == 1 and engines[0].starts == 1  # 只建/只启动一次
    assert all(e is got[0] for e in got)
    await mgr.shutdown_all()


async def test_same_character_generate_is_serialized():
    mgr = EngineManager(engine_factory=CountingEngine)
    ch = make_char()
    replies = await asyncio.gather(*(mgr.generate(ch, _ctx()) for _ in range(3)))
    assert all(r.content == "ok" for r in replies)
    eng = await mgr.acquire(ch)
    assert eng.max_concurrent == 1, "同角色 generate 发生并发（stdio 会交错）"
    assert eng.turns == 3
    await mgr.shutdown_all()


async def test_different_characters_generate_in_parallel():
    mgr = EngineManager(engine_factory=lambda: CountingEngine(delay=0.2))
    a, b = make_char("char-a"), make_char("char-b", "测试乙")
    t0 = time.monotonic()
    await asyncio.gather(mgr.generate(a, _ctx("char-a")), mgr.generate(b, _ctx("char-b")))
    elapsed = time.monotonic() - t0
    assert elapsed < 0.35, f"不同角色被串行了: {elapsed:.3f}s"  # 串行需 0.4s
    await mgr.shutdown_all()


# ---------- health_snapshot ----------


async def test_health_snapshot_reports_engine_fields():
    mgr = EngineManager(engine_factory=FakeEngine)
    ch = make_char()
    await mgr.acquire(ch)
    rows = await mgr.health_snapshot()
    assert len(rows) == 1
    row = rows[0]
    assert row["character_id"] == ch.id
    assert row["engine"] == "FakeEngine"
    assert row["alive"] is True
    assert row["pid"] is None  # fake 无进程
    assert row["turns"] == 0 and row["errors"] == 0
    assert row["idle_s"] >= 0
    await mgr.shutdown_all()


async def test_health_snapshot_minimal_for_engine_without_health():
    class Bare:
        async def start(self, character):  # noqa: ANN001
            pass

        async def stop(self):
            pass

        async def is_alive(self):
            return True

        async def generate(self, ctx, *, on_delta=None):  # pragma: no cover
            raise NotImplementedError

    mgr = EngineManager(engine_factory=Bare)
    await mgr.acquire(make_char())
    row = (await mgr.health_snapshot())[0]
    assert row["alive"] is True
    assert row["turns"] is None and row["errors"] is None  # 最小字段
    assert row["pid"] is None
    await mgr.shutdown_all()


# ---------- 预热 ----------


def test_warmup_env_read_at_call_time(monkeypatch):
    monkeypatch.setenv("MRP_WARMUP_MAX", "7")
    monkeypatch.setenv("MRP_WARMUP_CONCURRENCY", "5")
    assert warmup_max() == 7 and warmup_concurrency() == 5
    chars = [make_char(f"c{i}", f"角色{i}") for i in range(9)]
    assert len(pick_characters(chars)) == 7  # 未在 import 时固化为默认 3


async def test_warm_characters_concurrent_calls_start_once():
    """并发的两次预热（同角色）只启动一次——由 EngineManager._start_locks 保证。"""
    starts: list[str] = []

    class Slow:
        def __init__(self) -> None:
            self.alive = False

        async def start(self, character: Character) -> None:
            starts.append(character.id)
            await asyncio.sleep(0.1)
            self.alive = True

        async def stop(self) -> None:
            self.alive = False

        async def is_alive(self) -> bool:
            return self.alive

        async def generate(self, ctx, *, on_delta=None):  # pragma: no cover
            raise NotImplementedError

    mgr = EngineManager(engine_factory=Slow)
    ch = make_char()
    r1, r2 = await asyncio.gather(
        warm_characters(mgr, [ch], concurrency=2),
        warm_characters(mgr, [ch], concurrency=2),
    )
    assert r1 == [ch.id] and r2 == [ch.id]
    assert starts == [ch.id]  # 只启动一次
    await mgr.shutdown_all()
