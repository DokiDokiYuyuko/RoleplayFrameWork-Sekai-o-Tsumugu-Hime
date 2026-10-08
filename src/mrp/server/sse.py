"""SSE 事件总线：每会话的发布/订阅，FastAPI StreamingResponse 消费。

并发约定（2026-09-25 B1 修复）：
- publish **永不阻塞生产方**（回合循环）：队列满一律丢最旧再放，非 lossy
  额外计数 + 节流告警（此前 `await q.put(...)` 会在慢消费者处永久挂起）；
- `_subs` 的读（publish 的 `list()` 快照 + 同步投递）/写（subscribe/unsubscribe）
  都在事件循环线程内、其间无 await，天然原子，不需要锁。

断连清理（2026-09-25 回归修复）：
- 客户端断开的清理由 Starlette 的 StreamingResponse 自身保证：它监听断连并
  取消/关闭生成器，本模块 `finally: bus.unsubscribe(...)` 随之回收订阅槽，
  无需（也不得）在此处做 Request 级断连探测。
- 教训：曾用 Request 级断连探测 + asyncio.wait 监听断连，真实 uvicorn 下该
  探测会长时间挂起（其内部 anyio 取消域无法中断 asyncio 原语的 receive），
  且外部 task.cancel() 会被该取消域吞掉 → 等待其收尾的 gather 永久阻塞 →
  SSE 生成器卡死、后续事件全部不再推送（冒烟 15 条失败的根因）。
  回归守卫见 tests/test_sse_concurrency.py::test_sse_no_request_disconnect_probe。
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
import json
import logging
import time
from typing import Any
from uuid import uuid4

logger = logging.getLogger("mrp.sse")

# 非 lossy 事件丢弃告警的节流间隔（同一会话每分钟最多一条）
_DROP_WARN_INTERVAL_S = 60.0
REPLAY_MAX_EVENTS = 2048
REPLAY_MAX_BYTES = 2 * 1024 * 1024
REPLAY_MAX_AGE_S = 300.0
REPLAY_MAX_SESSIONS = 32


@dataclass
class _Journal:
    epoch: str = field(default_factory=lambda: uuid4().hex)
    sequence: int = 0
    events: deque[tuple[float, int, int, dict[str, Any]]] = field(default_factory=deque)
    bytes_used: int = 0

    def cursor(self) -> str:
        return f"{self.epoch}:{self.sequence}"


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        # 队列满丢最旧计数（进程生命周期内单调累加；供测试与 /health 读取）
        self._dropped: dict[str, int] = {}
        self._last_drop_warn: dict[str, float] = {}
        # 仅故事快照启用；simple chat 保持原有无重放语义。
        self._journals: OrderedDict[str, _Journal] = OrderedDict()

    def _journal(self, session_id: str) -> _Journal:
        journal = self._journals.get(session_id)
        if journal is None:
            journal = _Journal()
            self._journals[session_id] = journal
            if len(self._journals) > REPLAY_MAX_SESSIONS:
                self._journals.popitem(last=False)
        self._journals.move_to_end(session_id)
        self._prune(journal)
        return journal

    def _prune(self, journal: _Journal) -> None:
        now = time.monotonic()
        while journal.events and (
            len(journal.events) > REPLAY_MAX_EVENTS
            or journal.bytes_used > REPLAY_MAX_BYTES
            or now - journal.events[0][0] > REPLAY_MAX_AGE_S
        ):
            _, _, size, _ = journal.events.popleft()
            journal.bytes_used -= size

    def current_cursor(self, session_id: str) -> str:
        """会话快照的水位；与后续订阅同一事件循环内无 await。"""
        return self._journal(session_id).cursor()

    def subscribe_replay(
        self, session_id: str, after: str | None
    ) -> tuple[asyncio.Queue[dict[str, Any]], list[dict[str, Any]], bool]:
        """原子取得历史与实时队列；无效/过期游标交由客户端快照同步。"""
        journal = self._journal(session_id)
        replay: list[dict[str, Any]] = []
        resync = False
        if after:
            try:
                epoch, raw_sequence = after.rsplit(":", 1)
                sequence = int(raw_sequence)
                oldest = journal.events[0][1] if journal.events else journal.sequence + 1
                if epoch != journal.epoch or not oldest - 1 <= sequence <= journal.sequence:
                    resync = True
                else:
                    replay = [item for _, number, _, item in journal.events if number > sequence]
            except (ValueError, TypeError):
                resync = True
        q = self.subscribe(session_id)
        return q, replay, resync

    async def publish(
        self, session_id: str, event: str, payload: dict[str, Any], *, lossy: bool = False
    ) -> None:
        """发布事件，**永不阻塞**（B1）。

        lossy=True（仅 message.delta，R33）：队列满丢最旧，静默——delta 可丢
        （message.final 永远是权威整包），慢消费者不能拖死事件循环。
        lossy=False：同样丢最旧（保住最新事件，如 message.final），但计数 +
        节流告警（同一 session 每分钟最多一条），便于发现慢消费者/前端卡死。
        """
        from mrp.contracts.story import project_event
        # Independent plain chats have their own document and message contract.
        if not session_id.startswith("chat-"):
            payload = project_event(event, payload)
        data = json.dumps(payload, ensure_ascii=False, default=str)
        item = {"event": event, "data": data}
        journal = self._journals.get(session_id)
        if journal is not None:
            self._journals.move_to_end(session_id)
            journal.sequence += 1
            item = {**item, "id": journal.cursor(), "sequence": journal.sequence}
            size = len(event.encode("utf-8")) + len(data.encode("utf-8")) + 64
            journal.events.append((time.monotonic(), journal.sequence, size, item))
            journal.bytes_used += size
            self._prune(journal)
        subs = self._subs.get(session_id)
        if not subs:
            return
        for q in list(subs):  # 快照：投递期间 unsubscribe 改 dict 不影响本次遍历
            self._put_drop_oldest(q, item, session_id, lossy=lossy)

    def _put_drop_oldest(
        self,
        q: asyncio.Queue[dict[str, Any]],
        item: dict[str, Any],
        session_id: str,
        *,
        lossy: bool,
    ) -> None:
        """非阻塞投递：满则丢最旧再放。本函数内无 await（单线程内原子，不会死循环）。"""
        while True:
            try:
                q.put_nowait(item)
                return
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # 丢最旧
                except asyncio.QueueEmpty:  # 理论不可达（刚判满），重试
                    continue
                self._dropped[session_id] = self._dropped.get(session_id, 0) + 1
                if not lossy:
                    self._warn_drop_throttled(session_id)

    def _warn_drop_throttled(self, session_id: str) -> None:
        now = time.monotonic()
        if now - self._last_drop_warn.get(session_id, 0.0) < _DROP_WARN_INTERVAL_S:
            return
        self._last_drop_warn[session_id] = now
        logger.warning(
            "SSE 订阅队列满：丢弃最旧事件（session=%s，累计丢弃 %d 条；消费者过慢）",
            session_id,
            self._dropped.get(session_id, 0),
        )

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subs.setdefault(session_id, set()).add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subs.get(session_id)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            self._subs.pop(session_id, None)  # B2：不留空集合（旧写法残留 set()）

    def dropped_counts(self) -> dict[str, int]:
        """每会话累计丢弃事件数（健康检查/测试用；进程内单调累加）。"""
        return dict(self._dropped)


SSE_HEARTBEAT = {"event": "heartbeat", "data": "{}"}


async def sse_stream(
    bus: EventBus, session_id: str, request: Any = None, heartbeat_s: float = 30.0,
    after: str | None = None,
):
    """SSE 生成器：事件 + 心跳。

    `request` 形参仅为位置兼容保留、不被读取（断连清理由 Starlette 取消/关闭
    生成器 + 本函数 finally 退订保证，原因见模块 docstring）。
    心跳帧与 `retry:` 帧格式保持不变。
    """
    if after is None:
        q = bus.subscribe(session_id)
        replay: list[dict[str, Any]] = []
        resync = False
    else:
        q, replay, resync = bus.subscribe_replay(session_id, after)
    try:
        yield 'retry: 3000\n\n'
        if resync:
            yield 'event: stream.resync\ndata: {}\n\n'
            return
        previous_sequence: int | None = int(after.rsplit(":", 1)[1]) if after and not resync else None
        for item in replay:
            previous_sequence = item["sequence"]
            yield _event_frame(item)
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=heartbeat_s)
            except asyncio.TimeoutError:
                yield f"event: {SSE_HEARTBEAT['event']}\ndata: {SSE_HEARTBEAT['data']}\n\n"
                continue
            sequence = item.get("sequence")
            if sequence is not None and previous_sequence is not None and sequence != previous_sequence + 1:
                yield 'event: stream.resync\ndata: {}\n\n'
                return
            if sequence is not None:
                previous_sequence = sequence
            yield _event_frame(item)
    finally:
        bus.unsubscribe(session_id, q)


def _event_frame(item: dict[str, Any]) -> str:
    event_id = item.get("id")
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {item['event']}\ndata: {item['data']}\n\n"
