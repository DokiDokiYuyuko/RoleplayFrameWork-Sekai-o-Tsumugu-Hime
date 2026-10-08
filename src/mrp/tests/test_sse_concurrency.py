"""EventBus / SSE 流并发回归（B1 非阻塞发布、B2 退订清理、断连取消收尾）。

覆盖：
1. 非 lossy publish 在订阅队列满时 ≤1s 返回（旧实现 await q.put 会永久挂起）
2. 队满丢最旧、最新事件保留
3. 丢弃计数 + 节流告警（lossy 静默）
4. unsubscribe 不留空集合、可重复调用
5. 事件帧/心跳帧格式不变，aclose 后退订
6. 生成器被取消（客户端断开时 Starlette 的行为）必须及时收尾并退订
7. 回归守卫：禁止再做 Request 级断连探测（2026-09-25 SSE 卡死事故，机理见 sse.py 模块 docstring）
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import logging

import pytest

from mrp.server import sse as sse_mod
from mrp.server.sse import EventBus, sse_stream


def _fill(q: asyncio.Queue, n: int, prefix: str = "old") -> None:
    for i in range(n):
        q.put_nowait({"event": "x", "data": f"{prefix}-{i}"})


# ---------- B1：publish 永不阻塞 ----------


async def test_publish_non_lossy_never_blocks_when_full():
    bus = EventBus()
    q = bus.subscribe("s")
    _fill(q, 256)  # maxsize=256 打满
    await asyncio.wait_for(
        bus.publish("s", "message.final", {"content": "整包"}), timeout=1.0
    )
    assert q.qsize() == 256  # 丢最旧换新，不增长也不阻塞


async def test_non_lossy_drop_oldest_keeps_latest():
    bus = EventBus()
    q = bus.subscribe("s")
    _fill(q, 256)
    await bus.publish("s", "message.final", {"content": "整包"})
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert items[0]["data"] == "old-1"  # 最旧(old-0)被丢弃
    assert items[-1] == {"event": "message.final", "data": '{"content": "整包"}'}


async def test_dropped_counts_and_warning_throttled(caplog):
    bus = EventBus()
    q = bus.subscribe("s")
    _fill(q, 256)
    with caplog.at_level(logging.WARNING, logger="mrp.sse"):
        await bus.publish("s", "e1", {})
        await bus.publish("s", "e2", {})
    assert bus.dropped_counts()["s"] == 2
    assert len([r for r in caplog.records if r.name == "mrp.sse"]) == 1  # 每分钟一条


async def test_lossy_drop_silent_but_counted(caplog):
    bus = EventBus()
    q = bus.subscribe("s")
    _fill(q, 256)
    with caplog.at_level(logging.WARNING, logger="mrp.sse"):
        await bus.publish("s", "message.delta", {"delta": "a"}, lossy=True)
    assert not caplog.records  # lossy 属于设计内丢弃，不告警
    assert bus.dropped_counts()["s"] == 1
    assert q.qsize() == 256  # 既有 lossy 语义不变


async def test_publish_without_subscribers_is_noop():
    bus = EventBus()
    await bus.publish("nobody", "e", {})  # 不抛
    assert bus.dropped_counts() == {}


# ---------- B2：unsubscribe 清理 ----------


async def test_unsubscribe_removes_empty_session_entry():
    bus = EventBus()
    q1 = bus.subscribe("s")
    q2 = bus.subscribe("s")
    bus.unsubscribe("s", q1)
    assert bus._subs["s"] == {q2}
    bus.unsubscribe("s", q2)
    assert "s" not in bus._subs  # 不留空集合
    bus.unsubscribe("s", q2)  # 幂等：重复退订不炸
    await bus.publish("s", "e", {})  # 无订阅者发布不炸


# ---------- SSE 流：帧格式 / 取消收尾 / 源码守卫 ----------


async def test_sse_stream_frames_and_unsubscribe_on_close():
    """事件帧/心跳帧格式与旧版一致，aclose 后退订。"""
    bus = EventBus()
    agen = sse_stream(bus, "s", heartbeat_s=0.05)
    assert await agen.__anext__() == "retry: 3000\n\n"
    await bus.publish("s", "message.final", {"content": "hi"})
    assert await agen.__anext__() == 'event: message.final\ndata: {"content": "hi"}\n\n'
    hb = await asyncio.wait_for(agen.__anext__(), timeout=1.0)
    assert hb == "event: heartbeat\ndata: {}\n\n"
    await agen.aclose()
    assert "s" not in bus._subs


async def test_sse_stream_cancel_releases_promptly():
    """客户端断开时 Starlette 取消生成器：取消必须 ≤1s 完成并退订。

    回归守卫（2026-09-25）：旧实现用 asyncio.wait 并行监听 Request 级断连探测，
    真实 uvicorn 下该探测会吞掉外部取消 → 生成器卡死在收尾等待上 → 后续事件
    全部无法推送。本测试确保"取消 → 收尾"路径上不存在不可取消的等待。
    """
    bus = EventBus()
    agen = sse_stream(bus, "s", heartbeat_s=30.0)
    assert await agen.__anext__() == "retry: 3000\n\n"
    pending = asyncio.ensure_future(agen.__anext__())
    await asyncio.sleep(0.05)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, timeout=1.0)
    assert "s" not in bus._subs  # finally 已退订


async def test_sse_stream_positional_request_arg_ignored():
    """request 形参仅为位置兼容：传任意对象也不影响事件投递（不再被读取）。"""
    bus = EventBus()
    agen = sse_stream(bus, "s", object(), heartbeat_s=1.0)
    assert await agen.__anext__() == "retry: 3000\n\n"
    await bus.publish("s", "message.delta", {"delta": "你", "offset": 0}, lossy=True)
    frame = await asyncio.wait_for(agen.__anext__(), timeout=1.0)
    assert frame == 'event: message.delta\ndata: {"delta": "你", "offset": 0}\n\n'
    await agen.aclose()


async def test_story_replay_after_disconnect_keeps_order_without_duplicates():
    bus = EventBus()
    cursor = bus.current_cursor("story")
    await bus.publish("story", "message.pending", {"id": "m"})
    await bus.publish("story", "message.delta", {"delta": "你", "offset": 0}, lossy=True)
    agen = sse_stream(bus, "story", after=cursor)
    assert await agen.__anext__() == "retry: 3000\n\n"
    pending = await agen.__anext__()
    delta = await agen.__anext__()
    assert "event: message.pending" in pending and "id: " in pending
    assert "event: message.delta" in delta and '"offset": 0' in delta
    await bus.publish("story", "message.final", {"id": "m", "content": "你"})
    final = await agen.__anext__()
    assert "event: message.final" in final
    assert [frame.split("\n", 1)[0] for frame in (pending, delta, final)] == [
        f"id: {cursor.rsplit(':', 1)[0]}:{i}" for i in (1, 2, 3)
    ]
    await agen.aclose()


async def test_story_replay_expired_cursor_requests_resync():
    bus = EventBus()
    cursor = bus.current_cursor("story")
    for i in range(sse_mod.REPLAY_MAX_EVENTS + 1):
        await bus.publish("story", "message.delta", {"offset": i}, lossy=True)
    agen = sse_stream(bus, "story", after=cursor)
    assert await agen.__anext__() == "retry: 3000\n\n"
    assert await agen.__anext__() == "event: stream.resync\ndata: {}\n\n"
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()


async def test_story_replay_journal_is_bounded():
    bus = EventBus()
    for i in range(sse_mod.REPLAY_MAX_SESSIONS + 1):
        bus.current_cursor(f"story-{i}")
    assert len(bus._journals) == sse_mod.REPLAY_MAX_SESSIONS
    bus.current_cursor("large")
    for _ in range(3):
        await bus.publish("large", "message.delta", {"delta": "中" * 500_000}, lossy=True)
    journal = bus._journals["large"]
    assert journal.bytes_used <= sse_mod.REPLAY_MAX_BYTES


def test_sse_no_request_disconnect_probe():
    """回归守卫：sse.py 不得再出现 Request 级断连探测的"调用"（AST 检查调用点）。

    模块 docstring 里的文字提及不算；这里只拦截实际调用，防止事故重演。
    """
    tree = ast.parse(inspect.getsource(sse_mod))
    called_attrs = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "is_disconnected" not in called_attrs
