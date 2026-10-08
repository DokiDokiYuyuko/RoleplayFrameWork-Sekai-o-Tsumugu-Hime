"""R28.2 引擎预热单测：选择对象 / 限流并发 / 开关 / 失败静默。"""
from __future__ import annotations

import asyncio
import time

from mrp.engines.dsh.process import EngineManager
from mrp.server.warmup import pick_characters, warm_characters, warmup_enabled
from mrp.shared.models import Character, CharacterCard


class SlowEngine:
    """记录启动时刻的可控引擎（delay 用于验证并发）。"""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.started = False
        self.start_at: float | None = None

    async def start(self, character: Character) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.started = True
        self.start_at = time.monotonic()

    async def stop(self) -> None:
        self.started = False

    async def is_alive(self) -> bool:
        return self.started

    async def generate(self, ctx, *, on_delta=None):  # pragma: no cover
        raise NotImplementedError


def _char(cid: str, *, present: bool = True, muted: bool = False) -> Character:
    return Character(
        id=cid, card=CharacterCard(name=cid), present=present, muted=muted
    )


def test_pick_characters_filters_and_limits():
    chars = [
        _char("a"),
        _char("b", present=False),
        _char("c", muted=True),
        _char("d"),
        _char("e"),
        _char("f"),
    ]
    assert [c.id for c in pick_characters(chars)] == ["a", "d", "e"]  # 默认上限 3
    assert [c.id for c in pick_characters(chars, limit=2)] == ["a", "d"]


def test_warm_characters_parallel_and_ready():
    """不同角色的引擎并行启动（限流 2）：2×0.2s 的启动应明显短于串行 0.4s。"""
    engines: dict[str, SlowEngine] = {}

    def factory() -> SlowEngine:
        e = SlowEngine(delay=0.2)
        engines[f"e{len(engines)}"] = e
        return e

    async def run():
        mgr = EngineManager(engine_factory=factory)
        chars = [_char("a"), _char("b"), _char("c")]
        t0 = time.monotonic()
        ready = await warm_characters(mgr, chars, concurrency=3)
        elapsed = time.monotonic() - t0
        assert ready == ["a", "b", "c"]
        assert elapsed < 0.35, f"疑似串行启动: {elapsed:.3f}s"
        # 幂等：再次预热（已存活）应即时返回且不重复 start
        t1 = time.monotonic()
        assert await warm_characters(mgr, chars, concurrency=3) == ["a", "b", "c"]
        assert time.monotonic() - t1 < 0.15
        await mgr.shutdown_all()

    asyncio.run(run())


def test_warm_characters_silent_on_failure():
    def broken_factory():
        class Broken:
            async def start(self, character):  # noqa: ANN001
                raise RuntimeError("引擎起不来")

            async def stop(self):  # pragma: no cover
                pass

            async def is_alive(self):
                return False

            async def generate(self, ctx, *, on_delta=None):  # pragma: no cover
                raise NotImplementedError

        return Broken()

    async def run():
        mgr = EngineManager(engine_factory=broken_factory)
        assert await warm_characters(mgr, [_char("a")]) == []

    asyncio.run(run())


def test_warmup_disabled_by_env(monkeypatch):
    monkeypatch.setenv("MRP_WARMUP", "0")
    assert warmup_enabled() is False
    starts: list[str] = []

    def factory():
        class Eng(SlowEngine):
            async def start(self, character: Character) -> None:
                starts.append(character.id)
                await super().start(character)

        return Eng()

    async def run():
        mgr = EngineManager(engine_factory=factory)
        assert await warm_characters(mgr, [_char("a"), _char("b")]) == []
        assert starts == []  # 关闭后零启动

    asyncio.run(run())
