"""引擎预热（R28.2，2026-09-25 实施）：后台预启角色引擎，消除首回合冷启动。

背景：DSH 引擎首启含 node_modules 初始化（本地盘实测 ~17s，真机整回合首回合 46-48s 主要来自这里），
服务重启后用户的第一条消息必然踩到。策略（幂等、失败静默、不阻塞任何请求）：

- **服务启动**：预热"最近一个有过消息的会话"的在场角色；
- **打开会话**（GET /sessions/{id}）：预热该会话在场角色（会话切换同样受益）；
- **设置变更**（PATCH /settings）：重建引擎后按需预热当前会话。

native（OpenRouterEngine）/ fake 引擎无进程，预热只做一次轻量初始化（≈空操作）。
环境变量：`MRP_WARMUP=0` 关闭预热；`MRP_WARMUP_MAX`（默认 3）单会话预热上限；
`MRP_WARMUP_CONCURRENCY`（默认 2）并发启动数。
env 一律在函数内读取（不在 import 时固化）——容器/测试后续注入也能生效。
"""
from __future__ import annotations

import asyncio
import logging
import os

from mrp.engines.dsh.process import EngineManager
from mrp.shared.models import Character

logger = logging.getLogger("mrp.warmup")


def warmup_enabled() -> bool:
    return os.environ.get("MRP_WARMUP", "1") not in ("0", "false", "False", "off")


def warmup_max() -> int:
    """单会话预热上限（默认 3）。"""
    return int(os.environ.get("MRP_WARMUP_MAX", "3"))


def warmup_concurrency() -> int:
    """预热并发启动数（默认 2，至少 1）。"""
    return max(1, int(os.environ.get("MRP_WARMUP_CONCURRENCY", "2")))


def pick_characters(characters: list[Character], limit: int | None = None) -> list[Character]:
    """预热对象：在场且未静音的角色（静音不会被导演抽选，不该占进程）。"""
    picked = [c for c in characters if c.present and not c.muted]
    return picked[: (limit if limit is not None else warmup_max())]


async def warm_characters(
    manager: EngineManager,
    characters: list[Character],
    *,
    limit: int | None = None,
    concurrency: int | None = None,
) -> list[str]:
    """并发（限流）预启引擎；单个失败静默跳过。返回已就绪的角色 id。

    "启动中"去重由 EngineManager 保证：同一角色并发 acquire 会被每角色
    `_start_locks` 串行化，后到者看到 is_alive 直接复用（本函数被并发调用、
    或列表出现重复 id，都不会重复启动）。
    """
    targets = pick_characters(characters, limit)
    if not targets or not warmup_enabled():
        return []
    sem = asyncio.Semaphore(max(1, concurrency if concurrency is not None else warmup_concurrency()))

    async def one(c: Character) -> str | None:
        async with sem:
            try:
                await manager.acquire(c)
                return c.id
            except Exception as e:  # noqa: BLE001 —— 预热失败绝不影响主流程
                logger.warning("预热引擎失败 %s: %s", c.id, e)
                return None

    results = await asyncio.gather(*(one(c) for c in targets))
    ready = [r for r in results if r]
    if ready:
        logger.info("引擎预热就绪: %s", ", ".join(ready))
    return ready
