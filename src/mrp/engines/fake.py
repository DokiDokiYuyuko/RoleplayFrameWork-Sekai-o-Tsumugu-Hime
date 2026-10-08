"""FakeEngine：可编程测试替身（集成测试驱动全链路，计划 §11）。

记录收到的每个 TurnContext——集成测试据此断言可见性与注入正确性
（R3.1/R4.2/R5.1 的自动化验证手段）。
"""
from __future__ import annotations

import asyncio
import time

from mrp.shared.models import (
    Character,
    EngineHealth,
    EngineReply,
    TokenUsage,
    TurnContext,
)


class FakeEngine:
    """固定/可编程回复 + 调用记录。"""

    def __init__(self, replies: list[str] | None = None, delay: float = 0.0) -> None:
        self.replies = list(replies or ["（测试回复）"])
        self.delay = delay
        self.calls: list[TurnContext] = []
        self.composed_texts: list[str] = []
        self.started = False
        self.stopped = False
        self._turns = 0
        self._errors = 0

    async def start(self, character: Character) -> None:
        self.started = True
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True
        self.started = False

    async def is_alive(self) -> bool:
        return self.started and not self.stopped

    async def generate(self, ctx: TurnContext, *, on_delta=None) -> EngineReply:
        """on_delta（R33.3）：提供时按 6 字片吐出全文（协议契约验证——拼接须等于
        返回的 content）。切片不会触发服务端事件，编排层的伪流式统一由
        _emit_deltas 负责（design/v3/m8-r33 §2 定案，避免双发）。
        """
        self.calls.append(ctx)
        if self.delay:
            await asyncio.sleep(self.delay)
        self._turns += 1
        idx = min(self._turns - 1, len(self.replies) - 1)
        content = self.replies[idx]
        if on_delta is not None:
            for i in range(0, len(content), 6):
                on_delta(content[i : i + 6])
                if self.delay:
                    await asyncio.sleep(self.delay)
        return EngineReply(
            content=content,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            engine_session_id=f"{ctx.session_id}-{ctx.character_id}-{ctx.turn:04d}",
        )

    def health(self) -> EngineHealth:
        return EngineHealth(turns=self._turns, errors=self._errors)
