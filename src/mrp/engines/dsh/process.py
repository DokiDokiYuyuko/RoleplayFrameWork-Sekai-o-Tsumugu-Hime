"""EngineManager：每角色引擎实例的注册表 + LRU 空闲回收 + 崩溃重启。

dsh 进程常驻（首启 ~17s），空闲角色回收内存；generate 失败指数退避重启一次。

并发约定（2026-09-25 W4 修复）：
- `_lock` 只保护注册表读写，**绝不在持锁时 await 引擎 start/stop**（可达秒级）；
- `_start_locks[cid]`：同角色只启动一次（不同角色可并行启动）；
- `_turn_locks[cid]`：同一引擎实例不并发 generate（两路回合交错写引擎 stdio）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from mrp.engines.dsh.engine import DshEngine
from mrp.shared.models import Character, EngineReply, TokenUsage, TurnContext

logger = logging.getLogger("mrp.engine.manager")

# acquire 的"野引擎"重试上限（启动期被反复 shutdown_all/stop_engine 时放弃）
_ACQUIRE_MAX_ATTEMPTS = 3


class EngineLike(Protocol):
    async def start(self, character: Character) -> None: ...

    async def stop(self) -> None: ...

    async def is_alive(self) -> bool: ...

    async def generate(self, ctx: TurnContext) -> EngineReply: ...


class EngineManager:
    def __init__(
        self,
        engine_factory: Callable[[], EngineLike] | None = None,
        idle_timeout_s: float = 600.0,
    ) -> None:
        self._factory = engine_factory or (lambda: DshEngine())
        self._engines: dict[str, EngineLike] = {}
        self._last_used: dict[str, float] = {}
        # Remember the character snapshot last bound to each live engine. Native
        # engines may rebind/reconfigure on start(), while repeated acquires of
        # the same snapshot must not call start() again.
        self._started_character_signatures: dict[str, str] = {}
        # 每角色独立启动锁（2026-09-25 预热改造）：此前 acquire 持全局锁跨 await eng.start()，
        # 导致"角色 B 首回合要等角色 A 引擎启动完"（中局同样卡）；现在只序列化同一角色的启动。
        self._start_locks: dict[str, asyncio.Lock] = {}
        # 每角色回合锁（W4）：dsh 是 stdio 请求-响应协议，同一进程并发 run 会交错写管道
        self._turn_locks: dict[str, asyncio.Lock] = {}
        self._idle_timeout = idle_timeout_s
        self._lock = asyncio.Lock()

    # ---- 注册表辅助 ----

    def _turn_lock(self, character_id: str) -> asyncio.Lock:
        """取/建角色回合锁（调用处不得在 lookup 与 acquire 之间 await）。"""
        lock = self._turn_locks.get(character_id)
        if lock is None:
            lock = asyncio.Lock()
            self._turn_locks[character_id] = lock
        return lock

    def _cleanup_char_locks(self, character_id: str) -> None:
        """清理该角色的启动/回合锁（B11：防 `_start_locks` 无界增长）。

        只在锁未被持有时删：已持有说明有 acquire/generate 在途，删掉会让新老调用
        拿到两把不同的锁，破坏"同角色串行"。
        调用点仅限 sweep_idle 的 GC 与 shutdown_all ——**不要在 stop_engine 就地调用**：
        那里恰可能有同角色 acquire 在等待该锁，刚 release、唤醒者尚未恢复时
        `locked()` 为 False，删掉会导致后到者新建第二把锁 → 同角色双启动。
        """
        for locks in (self._start_locks, self._turn_locks):
            lock = locks.get(character_id)
            if lock is not None and not lock.locked():
                locks.pop(character_id, None)

    # ---- 生命周期 ----

    async def acquire(self, character: Character) -> EngineLike:
        """Prewarm or acquire an engine without rebinding it during a turn."""
        async with self._turn_lock(character.id):
            return await self._acquire(character)

    async def _acquire(self, character: Character) -> EngineLike:
        """取或启动该角色的引擎（启动幂等；不同角色可并行启动）。

        B9：启动期间引擎可能被 shutdown_all/stop_engine 清除或替换——检测到"野引擎"
        就把自己停掉并重试（上限 _ACQUIRE_MAX_ATTEMPTS 次），而不是对已清空的字典
        取键（旧实现 `return self._engines[character.id]` 会 KeyError）。
        """
        for _attempt in range(1, _ACQUIRE_MAX_ATTEMPTS + 1):
            async with self._lock:
                eng = self._engines.get(character.id)
                if eng is None:
                    eng = self._factory()
                    self._engines[character.id] = eng
                # 注册即刷新活跃时间：启动中不会被 sweep_idle 误回收
                self._last_used[character.id] = time.monotonic()
                lock = self._start_locks.setdefault(character.id, asyncio.Lock())
            async with lock:
                signature = character.model_dump_json()
                alive = await eng.is_alive()
                if not alive or self._started_character_signatures.get(character.id) != signature:
                    # Native engines rebind/update their profile in start(); only
                    # skip it when this exact character snapshot is already live.
                    await eng.start(character)
                    self._started_character_signatures[character.id] = signature
                async with self._lock:
                    current = self._engines.get(character.id)
                if current is not eng:
                    # 启动期间被替换/清除 → 锁外停掉野引擎（避免进程泄漏）后重试
                    logger.warning("引擎 %s 启动期被清除/替换，弃用并重试", character.id)
                    await eng.stop()
                    continue
                async with self._lock:
                    self._last_used[character.id] = time.monotonic()
                return eng
        raise RuntimeError(
            f"acquire({character.id!r}) 连续 {_ACQUIRE_MAX_ATTEMPTS} 次在启动期被 "
            "shutdown_all/stop_engine 清除，已放弃（检查是否在反复关闭引擎）"
        )

    async def generate(self, character: Character, ctx: TurnContext, *, on_delta=None) -> EngineReply:
        """带一次崩溃重启重试的生成。on_delta（R33）透传给引擎。

        同一角色串行（`_turn_locks`，W4）：dsh 的 stdio 协议在同一进程上并发 run
        会交错写管道；崩溃重启的 stop/start 同样不能与另一路 generate 并行。
        选 asyncio.Lock 而非 Semaphore(1)：这里只要"同一时刻一个回合"的互斥语义，
        不需要许可计数（Semaphore 的计数能力用不上，还多一层理解成本）。
        """
        async with self._turn_lock(character.id):
            eng = await self._acquire(character)
            try:
                reply = await eng.generate(ctx, on_delta=on_delta)
            except Exception as first_error:
                if getattr(first_error, "retryable", True) is False:
                    raise
                # 崩溃重启一次（指数退避简化为单次：dsh 子进程挂了重拉）
                first_usage = getattr(first_error, "usage", None)
                first_calls = getattr(first_error, "usage_calls", None) or ([first_usage] if first_usage is not None else [])
                try:
                    await eng.stop()
                    await asyncio.sleep(0.5)
                    eng = await self._acquire(character)
                    reply = await eng.generate(ctx, on_delta=on_delta)
                except Exception as retry_error:
                    retry_usage = getattr(retry_error, "usage", None)
                    retry_calls = getattr(retry_error, "usage_calls", None) or ([retry_usage] if retry_usage is not None else [])
                    if first_usage is not None:
                        retry_error.usage = self._combined_usage(first_usage, retry_usage)
                    if first_calls:
                        retry_error.usage_calls = first_calls + retry_calls
                    raise
                if first_usage is not None:
                    reply = reply.model_copy(update={
                        "usage": self._combined_usage(first_usage, reply.usage),
                        "usage_calls": first_calls + (reply.usage_calls or [reply.usage]),
                    })
            self._last_used[character.id] = time.monotonic()
            return reply

    @staticmethod
    def _combined_usage(first, second) -> TokenUsage:
        values = [value if isinstance(value, TokenUsage) else TokenUsage(**value)
                  for value in (first, second) if value is not None]
        return TokenUsage(**{key: sum(getattr(value, key) for value in values)
                            for key in ("input_tokens", "output_tokens", "cached_tokens")})

    async def sweep_idle(self) -> int:
        """回收空闲超时的引擎进程（dsh_home 保留）。返回回收数。

        B10：锁内只做"摘除 + 快照"，stop()（可达秒级）在锁外逐台执行——
        否则空闲回收期间所有 acquire 都被 `_lock` 挡在门外。
        """
        now = time.monotonic()
        to_stop: list[tuple[str, EngineLike]] = []
        async with self._lock:
            for cid, last in list(self._last_used.items()):
                turn_lock = self._turn_locks.get(cid)
                if turn_lock is not None and turn_lock.locked():
                    continue  # 回合在途：不算空闲，不能杀（会打断生成）
                if now - last < self._idle_timeout:
                    continue
                eng = self._engines.pop(cid, None)
                self._last_used.pop(cid, None)
                self._started_character_signatures.pop(cid, None)
                self._cleanup_char_locks(cid)
                if eng is not None:
                    to_stop.append((cid, eng))
        stopped: list[str] = []
        for cid, eng in to_stop:
            try:
                if await eng.is_alive():
                    await eng.stop()
                    stopped.append(cid)
            except Exception as e:  # noqa: BLE001 —— 单台回收失败不中断其余
                logger.warning("回收引擎失败 %s: %s", cid, e)
        async with self._lock:
            # B11 续：GC"已无引擎且无活跃记录"的角色锁（stop_engine 摘除后留下的条目）。
            # 安全前提：等待者必持有已注册的引擎（acquire 先注册再等锁），
            # 因此"无引擎 + 无 last_used"时不可能存在等待者；再加 locked() 双保险。
            stale = [
                cid
                for cid in self._start_locks
                if cid not in self._engines and cid not in self._last_used
            ]
            for cid in stale:
                self._cleanup_char_locks(cid)
        if stopped:
            logger.info("空闲引擎已回收: %s", ", ".join(stopped))
        return len(stopped)

    async def stop_engine(self, character_id: str) -> None:
        """停掉并摘除该角色的引擎（设置变更/手动重启路径）。

        续修（波 1 追加）：`stop()` 可达秒级，**不得在 `_lock` 内等**——旧实现会让
        所有角色的 acquire 一起排队。做法：
          1) `_lock` 内摘除注册表快照（先摘除才能让后续 acquire 看到"无引擎"）；
          2) 取该角色 `_start_locks[cid]`（与 acquire 同一把锁）→ 锁外 `await stop()`：
             "停旧引擎"与"启新引擎"被同一把锁顺序化，不会新旧并存；
          3) 不就地清锁（同角色 acquire 可能正在等这把锁，见 `_cleanup_char_locks`
             注释），残留条目由 sweep_idle GC / shutdown_all 兜底。

        代价与边界：若该角色恰有启动在途，本调用需等启动结束（≤ initialize_timeout，
        默认 180s）——这是必要的，否则 stop 与 start 并发操作同一引擎对象（旧实现
        的进程泄漏）。本方法不取 `_turn_locks`：与 generate 的"回合锁 → 启动锁"顺序
        相反，取回合锁会引入环；在途回合被 stop 打断属既有语义。
        """
        async with self._lock:
            eng = self._engines.pop(character_id, None)
            self._last_used.pop(character_id, None)
            self._started_character_signatures.pop(character_id, None)
            lock = self._start_locks.get(character_id)
            if eng is not None and lock is None:
                lock = asyncio.Lock()
                self._start_locks[character_id] = lock
        if eng is None:
            return
        assert lock is not None  # eng 非 None ⇒ 上面已就绪
        async with lock:
            await eng.stop()
        logger.info("引擎已停止: %s", character_id)

    async def shutdown_all(self) -> None:
        async with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
            self._last_used.clear()
            self._started_character_signatures.clear()
            self._start_locks.clear()
            self._turn_locks.clear()
        # 锁外停（同 B10 理由：stop 可达秒级）；clear 后新 acquire 会重建引擎
        for eng in engines:
            try:
                await eng.stop()
            except Exception:  # noqa: BLE001
                pass

    def active_ids(self) -> list[str]:
        return list(self._engines.keys())

    async def ready_ids(self) -> list[str]:
        """真正已启动完成的引擎（acquire 会先注册后启动；启动中不算就绪——预热观测用）。

        注：快照后在锁外逐个 is_alive()，启动中的引擎可能已就绪或在途，属观测口径。
        """
        async with self._lock:
            items = list(self._engines.items())
        out: list[str] = []
        for cid, eng in items:
            try:
                if await eng.is_alive():
                    out.append(cid)
            except Exception:  # noqa: BLE001
                continue
        return out

    async def health_snapshot(self) -> list[dict[str, Any]]:
        """每引擎健康快照（供 /health 接线）。

        有 `health()` 的引擎给 turns/errors/startup/uptime 等；没有的只给最小字段
        （id/engine/alive/pid/idle_s）。所有取值容错，单台异常不影响整体。
        """
        async with self._lock:
            items = list(self._engines.items())
            last_used = dict(self._last_used)
        now_mono, now_wall = time.monotonic(), time.time()
        out: list[dict[str, Any]] = []
        for cid, eng in items:
            row: dict[str, Any] = {
                "character_id": cid,
                "engine": type(eng).__name__,
                "alive": False,
                "pid": getattr(eng, "pid", None),
                "idle_s": round(now_mono - last_used.get(cid, now_mono), 1),
                "uptime_s": None,
                "turns": None,
                "errors": None,
                "startup_seconds": None,
            }
            try:
                row["alive"] = bool(await eng.is_alive())
            except Exception as e:  # noqa: BLE001
                row["error"] = f"is_alive: {e}"
            health_fn = getattr(eng, "health", None)
            if callable(health_fn):
                try:
                    h = health_fn()
                    if h.started_at is not None:
                        row["uptime_s"] = round(now_wall - h.started_at.timestamp(), 1)
                    row["turns"] = h.turns
                    row["errors"] = h.errors
                    row["startup_seconds"] = h.startup_seconds
                except Exception as e:  # noqa: BLE001
                    row["error"] = f"health: {e}"
            out.append(row)
        return out
