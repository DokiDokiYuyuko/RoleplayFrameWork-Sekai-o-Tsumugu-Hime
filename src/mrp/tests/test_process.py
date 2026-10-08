"""EngineManager 单测：FakeEngine 驱动的注册/回收/重启重试。"""
from __future__ import annotations

import asyncio

from mrp.engines.dsh.process import EngineManager
from mrp.engines.fake import FakeEngine
from mrp.shared.models import Character, CharacterCard, TurnContext


def make_char() -> Character:
    return Character(id="char-a", card=CharacterCard(name="测试甲"))


def test_acquire_starts_once_and_sweeps():
    async def run():
        mgr = EngineManager(engine_factory=FakeEngine, idle_timeout_s=0.0)
        ch = make_char()
        e1 = await mgr.acquire(ch)
        e2 = await mgr.acquire(ch)
        assert e1 is e2 and await e1.is_alive()

        stopped = await mgr.sweep_idle()  # timeout=0 → 立即回收
        assert stopped == 1
        assert not await e1.is_alive()
        await mgr.shutdown_all()

    asyncio.run(run())


def test_generate_retries_after_crash():
    class CrashOnceEngine(FakeEngine):
        def __init__(self):
            super().__init__(replies=["ok"])
            self.crashed = False

        async def generate(self, ctx, *, on_delta=None):
            if not self.crashed:
                self.crashed = True
                raise RuntimeError("simulated crash")
            return await super().generate(ctx, on_delta=on_delta)

    async def run():
        mgr = EngineManager(engine_factory=CrashOnceEngine)
        ch = make_char()
        ctx = TurnContext(session_id="s", character_id=ch.id, turn=1)
        reply = await mgr.generate(ch, ctx)  # 首次崩溃 → 重启重试成功
        assert reply.content == "ok"
        await mgr.shutdown_all()

    asyncio.run(run())


def test_acquire_parallel_across_characters_and_dedup_same():
    """预热改造（2026-09-25）：不同角色可并行启动（不再被全局锁串行）；同角色并发只起一次。"""
    import time

    starts: dict[str, int] = {}

    class Slow:
        def __init__(self) -> None:
            self.alive = False

        async def start(self, character) -> None:  # noqa: ANN001
            await asyncio.sleep(0.2)
            self.alive = True
            starts[character.id] = starts.get(character.id, 0) + 1

        async def stop(self) -> None:
            self.alive = False

        async def is_alive(self) -> bool:
            return self.alive

        async def generate(self, ctx, *, on_delta=None):  # pragma: no cover
            raise NotImplementedError

    async def run():
        mgr = EngineManager(engine_factory=Slow)
        a = make_char()  # char-a
        b = Character(id="char-b", card=CharacterCard(name="测试乙"))
        t0 = time.monotonic()
        await asyncio.gather(mgr.acquire(a), mgr.acquire(b))
        assert time.monotonic() - t0 < 0.35, "不同角色的启动被串行了"
        # 同角色并发：只启动一次
        await asyncio.gather(mgr.acquire(a), mgr.acquire(a))
        assert starts["char-a"] == 1
        await mgr.shutdown_all()

    asyncio.run(run())
