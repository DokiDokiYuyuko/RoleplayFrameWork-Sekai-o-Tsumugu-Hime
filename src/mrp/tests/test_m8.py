"""M8 测试：R32 消息操控（swipe 原位候选 / 编辑 / 删除）。

环境隔离同 test_integration.py：在 import mrp.server.app 之前设
MRP_DATA_ROOT/MRP_FAKE_ENGINE（World() 模块导入时创建）。
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="mrp-m8-")
os.environ["MRP_DATA_ROOT"] = _TMP
os.environ["MRP_FAKE_ENGINE"] = "1"

import pytest  # noqa: E402

from mrp.engines.dsh.process import EngineManager  # noqa: E402
from mrp.engines.fake import FakeEngine  # noqa: E402
from mrp.orchestrator.memory import MemoryConsolidator  # noqa: E402
from mrp.orchestrator.session import SessionRunner  # noqa: E402
from mrp.shared.models import (  # noqa: E402
    Character,
    CharacterCard,
    MemoryRecord,
    Message,
    SessionMeta,
    SessionState,
    TurnContext,
    fingerprint,
)
from mrp.tests.test_integration import RecordingSink, make_runner  # noqa: E402


class ExplodingEngine(FakeEngine):
    """从第 fail_at 次调用起持续抛异常（R32.4 失败保护；须扛住 EngineManager 的一次重启重试）。"""

    def __init__(self, replies, fail_at: int) -> None:
        super().__init__(replies)
        self.fail_at = fail_at

    async def generate(self, ctx, *, on_delta=None) -> object:
        if len(self.calls) >= self.fail_at - 1:
            self.calls.append(ctx)
            raise RuntimeError("引擎爆炸(测试注入)")
        return await super().generate(ctx, on_delta=on_delta)


class NoStreamEngine(FakeEngine):
    """模拟 DSH：忽略 on_delta → 走编排层整段切片（伪流式路径）。"""

    async def generate(self, ctx, *, on_delta=None) -> object:
        return await super().generate(ctx)


# ---------- R32.1 候选切换 ----------


async def test_switch_variant_updates_fingerprint_and_content():
    runner, sink = make_runner(replies=["旧回复", "新回复"])
    created = await runner.player_say("测试甲，你怎么看？")
    msg = created[1]
    new = await runner.swipe(msg.id)
    assert new is not None and new.active_variant == 1
    fp_before = new.fingerprint

    switched = await runner.switch_variant(msg.id, 0)
    assert switched is not None
    assert switched.content == "旧回复"
    assert switched.active_variant == 0
    assert switched.fingerprint != fp_before  # 内容变了指纹必须变
    assert switched.fingerprint == fingerprint(switched.actor, switched.seq, switched.content)
    # 越界拒绝
    assert await runner.switch_variant(msg.id, 5) is None
    assert await runner.switch_variant(msg.id, -1) is None
    # 事件
    assert "message.updated" in sink.names()
    # 顶层 generation_meta 镜像 active variant
    assert switched.generation_meta == switched.variants[0].generation_meta


async def test_swipe_only_last_message():
    """非最后一条不可 swipe（design/v3/m8-r32 §3.4）。"""
    runner, _ = make_runner(replies=["回复一", "回复二"])
    await runner.player_say("测试甲，你怎么看？")  # 回复一（turn 1）
    await runner.player_say("继续说", force_character="char-a")  # 回复二（turn 2）
    first_reply = next(
        m for m in runner.state.messages
        if m.actor == "char-a" and m.content == "回复一"
    )
    assert await runner.swipe(first_reply.id) is None


# ---------- R32.1 防误写门 ----------


async def test_swapped_variant_never_consolidated():
    """被换下候选永不进入记忆固化（R32.1 验收 / R36.4 联动）。"""
    runner, _ = make_runner(replies=["旧回复", "新回复"])
    created = await runner.player_say("测试甲，你怎么看？")
    new = await runner.swipe(created[1].id)
    assert new is not None

    store: list[MemoryRecord] = []

    class _Store:
        def add(self, record):
            store.append(record)

    consolidator = MemoryConsolidator(_Store(), summarize=lambda t: t)  # 透传全文
    record = consolidator.consolidate(runner.state, "char-a")
    assert record is not None
    assert "旧回复" not in record.content  # 换下候选不在任何组装路径
    assert "新回复" in record.content


# ---------- R32.2 编辑 ----------


async def test_edit_message_next_turn_references_edited():
    runner, _ = make_runner(replies=["嗯。"])
    created = await runner.player_say("测试甲，明天见")
    player_msg = created[0]

    edited = await runner.edit_message(player_msg.id, "测试甲，明天八点图书馆见")
    assert edited is not None
    assert edited.edited is True
    assert edited.content == "测试甲，明天八点图书馆见"
    assert edited.fingerprint == fingerprint("player", edited.seq, edited.content)

    # 下回合角色上下文含编辑后内容（逐回合重组红利）
    await runner.player_say("就这意思", force_character="char-a")
    engine = runner.engines._engines["char-a"]
    ctx = engine.calls[-1]
    assert any(m.content == "测试甲，明天八点图书馆见" for m in ctx.visible_messages)
    assert not any(m.content == "测试甲，明天见" for m in ctx.visible_messages)

    # 空 content 拒绝
    assert await runner.edit_message(player_msg.id, "  ") is None


async def test_edit_inner_and_scene_messages():
    """任意 kind 均可编辑（R32.2），含 inner（R34 C3 证据源）。"""
    runner, _ = make_runner(replies=["嗯。"])
    inner = await runner.player_say("（内心：她好烦）", channel="inner")
    edited = await runner.edit_message(inner[0].id, "（内心：其实有点在意）")
    assert edited is not None and edited.kind == "inner"
    assert edited.content == "（内心：其实有点在意）"


# ---------- R32.3 删除 ----------


async def test_delete_message_removes_from_context():
    runner, _ = make_runner(replies=["嗯。", "哦。"])
    created = await runner.player_say("测试甲，明天见")
    target = created[1]  # 角色回复
    seq_of_target = target.seq

    assert await runner.delete_message(target.id) is True
    assert all(m.id != target.id for m in runner.state.messages)
    # 角色上下文不再含此消息
    await runner.player_say("继续", force_character="char-a")
    engine = runner.engines._engines["char-a"]
    assert not any(m.id == target.id for m in engine.calls[-1].visible_messages)
    # 删除的是末条 → 下一条 seq 不复用（max-scan）
    again = await runner.player_say("再说一句", force_character="char-a")
    assert again[1].seq > seq_of_target
    # 幂等拒绝：再删同 id
    assert await runner.delete_message(target.id) is False


# ---------- R45 重跑最后一轮（编辑"我的消息"→重新生成） ----------


async def test_regenerate_turn_replaces_replies_and_keeps_mentions():
    runner, sink = make_runner(replies=["旧回复", "新回复"])
    created = await runner.player_say("测试甲，你怎么看？", mentions=["char-a"])
    player_msg = created[0]
    old_replies = [m for m in created if m.actor != "player"]
    assert old_replies
    assert player_msg.mentions == ["char-a"]  # R30 显式路由随消息持久化

    edited = await runner.edit_message(player_msg.id, "测试甲，换个说法——你怎么想？")
    assert edited is not None
    out = await runner.regenerate_turn(player_msg.id)
    assert out is not None

    ids = {m.id for m in runner.state.messages}
    assert all(r.id not in ids for r in old_replies)  # 旧回复物理删除
    assert any(e == "message.deleted" for e, _ in sink.events)
    new_replies = [m for m in out if m.actor != "player"]
    assert new_replies and new_replies[0].content == "新回复"
    assert all(m.turn == player_msg.turn for m in out)  # 同回合重跑
    # 路由复用持久化的 mentions（编辑不丢定向）
    assert runner.state.director_log[-1].trigger == "explicit_mention"


async def test_regenerate_turn_guards():
    runner, _ = make_runner(replies=["一", "二", "三"])
    first = (await runner.player_say("第一句", force_character="char-a"))[0]
    await runner.player_say("第二句", force_character="char-a")

    # 非最后一轮 → 拒绝
    assert await runner.regenerate_turn(first.id) is None
    # inner 消息（不触发回合）→ 拒绝
    inner = await runner.player_say("（内心：再想想）", channel="inner")
    assert await runner.regenerate_turn(inner[0].id) is None
    # 不存在的消息 → 拒绝
    assert await runner.regenerate_turn("msg-nonexistent") is None


async def test_regenerate_turn_without_reply_recovers():
    """其后暂无回复也能重跑（=让角色回应这条消息）——回复被删后的恢复路径。"""
    runner, _ = make_runner(replies=["一", "二"])
    created = await runner.player_say("你好")
    player_msg, reply = created[0], created[1]
    assert await runner.delete_message(reply.id) is True

    out = await runner.regenerate_turn(player_msg.id)
    assert out is not None
    assert any(m.actor != "player" for m in out)
    assert any(m.actor != "player" for m in runner.state.messages)


async def test_regenerate_turn_rolls_back_on_engine_failure():
    """生成失败整体回滚：恢复被删回复、清掉半成品、发出 message.error。"""
    runner, sink = make_runner(replies=["原回复"])
    created = await runner.player_say("你好")
    player_msg, reply = created[0], created[1]

    runner.engines = EngineManager(engine_factory=lambda: ExplodingEngine(["x"], fail_at=1))
    sink.events.clear()
    with pytest.raises(RuntimeError):
        await runner.regenerate_turn(player_msg.id)

    # 状态回到重跑前：仅 [玩家消息, 原回复]
    assert [m.id for m in runner.state.messages] == [player_msg.id, reply.id]
    # 回滚事件：原回复重新落账 + 失败信号（供前端撤销 pending 气泡）
    names = sink.names()
    assert "message.final" in names and "message.error" in names


def test_api_regenerate_flow(client):
    sid, reply_id = _setup_api_session(client)
    state = client.get(f"/api/v1/sessions/{sid}").json()
    player_msg = next(m for m in state["messages"] if m["actor"] == "player")
    assert player_msg["mentions"], "显式 @ 应随消息持久化（R45）"
    base = f"/api/v1/sessions/{sid}/messages/{player_msg['id']}"

    # 编辑 + 重跑
    assert client.patch(base, json={"content": "你好呀"}).status_code == 200
    r = client.post(f"{base}/regenerate")
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert any(m["actor"] != "player" for m in msgs)
    state2 = client.get(f"/api/v1/sessions/{sid}").json()
    assert all(m["id"] != reply_id for m in state2["messages"])  # 旧回复已删

    # 再发一条 → 首条玩家消息不再是最后一轮 → 400
    assert client.post(
        f"/api/v1/sessions/{sid}/messages", json={"content": "再说一句"}
    ).status_code == 200
    assert client.post(f"{base}/regenerate").status_code == 400
    # 不存在的消息 → 404
    assert client.post(
        f"/api/v1/sessions/{sid}/messages/msg-nonexistent/regenerate"
    ).status_code == 404


# ---------- R32.4 失败保护 ----------


async def test_swipe_failure_preserves_variants():
    runner, sink = make_runner(replies=["旧回复", "不会到达"])
    # 换成第 2 次调用爆炸的引擎
    runner.engines = EngineManager(
        engine_factory=lambda: ExplodingEngine(["旧回复"], fail_at=2)
    )
    created = await runner.player_say("测试甲，你怎么看？")
    msg = created[1]

    result = await runner.swipe(msg.id)
    assert result is None
    # 原候选无损回滚
    assert msg.status == "final"
    assert msg.content == "旧回复"
    assert msg.variants == []  # 未物化任何候选
    assert "message.error" in sink.names()


# ---------- pending 冲突 ----------


async def test_edit_and_delete_during_pending_rejected():
    runner, _ = make_runner(replies=["嗯。"])
    created = await runner.player_say("测试甲，明天见")
    msg = created[1]
    msg.status = "pending"  # 模拟生成中
    assert await runner.edit_message(msg.id, "x") is None
    assert await runner.switch_variant(msg.id, 0) is None
    assert await runner.delete_message(msg.id) is False


# ---------- API 级（TestClient） ----------


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    import mrp.server.app as app_mod

    app_mod.world.summarizer = lambda text: "（测试摘要）"
    with TestClient(app_mod.app) as c:
        yield c


def _setup_api_session(client) -> tuple[str, str]:
    """建会话 + 发一条消息，返回 (session_id, 角色消息 id)。"""
    resp = client.post(
        "/api/v1/characters/import",
        files={"file": ("测试甲.json", '{"name": "测试甲", "first_mes": "（点头）你来了。"}'.encode("utf-8"), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    cid = resp.json()["id"]
    r = client.post(
        "/api/v1/sessions",
        json={"title": "m8-test", "character_ids": [cid]},
    )
    assert r.status_code == 200, r.text
    sid = r.json()["meta"]["id"]
    r = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "你好", "mentions": [cid]})
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    reply_id = next(m["id"] for m in msgs if m["actor"] != "player")
    return sid, reply_id


def test_api_edit_switch_delete_flow(client):
    sid, reply_id = _setup_api_session(client)
    base = f"/api/v1/sessions/{sid}/messages/{reply_id}"

    # swipe：原位候选
    r = client.post(f"{base}/swipe")
    assert r.status_code == 200, r.text
    msg = r.json()
    assert len(msg["variants"]) == 2
    assert msg["active_variant"] == 1

    # 候选切换
    r = client.patch(base, json={"active_variant": 0})
    assert r.status_code == 200, r.text
    assert r.json()["active_variant"] == 0

    # 编辑
    r = client.patch(base, json={"content": "（手改的回复）"})
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "（手改的回复）"
    assert r.json()["edited"] is True

    # 双传 400
    r = client.patch(base, json={"content": "x", "active_variant": 0})
    assert r.status_code == 400
    # 空 body 400
    r = client.patch(base, json={})
    assert r.status_code == 400
    # 越界 400
    r = client.patch(base, json={"active_variant": 9})
    assert r.status_code == 400
    # 不存在的消息 404
    r = client.patch(f"/api/v1/sessions/{sid}/messages/msg-nonexistent", json={"content": "x"})
    assert r.status_code == 404

    # 删除
    r = client.delete(base)
    assert r.status_code == 200
    r = client.delete(base)
    assert r.status_code == 404  # 已删


# ---------- R33 流式输出 ----------


async def test_fake_engine_on_delta_contract():
    """R33.3：FakeEngine 的 on_delta 协议契约——切片拼接 == 返回 content。"""
    engine = FakeEngine(replies=["这是一段用来验证流式契约的回复文本。"])
    await engine.start(
        Character(id="char-a", card=CharacterCard(name="测试甲", first_mes="你好"))
    )
    chunks: list[str] = []
    ctx = TurnContext(
        session_id="s", character_id="char-a", turn=1,
        visible_messages=[], injections=[], budget_tokens=100,
    )
    reply = await engine.generate(ctx, on_delta=chunks.append)
    assert "".join(chunks) == reply.content
    assert all(len(c) <= 6 for c in chunks)


async def test_pseudo_stream_deltas_long_reply():
    """非流式引擎（DSH 路径）：整段回复按 48 字切片，offset 严格递增、拼接 == final。"""
    long_reply = "雨滴敲打着便利店的玻璃门，像三年前那个夜晚一样急促。" * 4  # 104 字（26×4）
    runner, sink = make_runner(replies=[long_reply])
    runner.engines = EngineManager(engine_factory=lambda: NoStreamEngine([long_reply]))
    created = await runner.player_say("测试甲，讲讲那晚的事")
    assert created[1].content == long_reply
    names = sink.names()
    deltas = [p for e, p in sink.events if e == "message.delta"]
    assert len(deltas) >= 3  # 104 字 / 48 字片 = 3 片
    offsets = [d["offset"] for d in deltas]
    assert offsets == sorted(offsets) and len(set(offsets)) == len(offsets)
    assert "".join(d["delta"] for d in deltas) == long_reply
    last_delta = max(j for j, n in enumerate(names) if n == "message.delta")
    last_final = max(j for j, n in enumerate(names) if n == "message.final")
    assert last_final > last_delta
    assert all(d["message_id"] == created[1].id for d in deltas)


async def test_live_stream_bridge_dedupes():
    """R48 真流式桥：流式引擎逐片回调 → delta 拼接恰等于最终内容（不双发）。"""
    long_reply = "雨滴敲打着便利店的玻璃门。" * 3
    runner, sink = make_runner(replies=[long_reply])
    created = await runner.player_say("测试甲，讲讲那晚的事")
    deltas = [p for e, p in sink.events if e == "message.delta"]
    assert deltas, "真流式引擎应产生 delta"
    assert deltas[0]["offset"] == 0
    offsets = [d["offset"] for d in deltas]
    assert offsets == sorted(offsets) and len(set(offsets)) == len(offsets)
    assert "".join(d["delta"] for d in deltas) == long_reply  # 恰好一次，无重复
    assert all(d["message_id"] == created[1].id for d in deltas)


async def test_streaming_disabled_no_deltas():
    """R33.4：开关关闭 → 事件序列与 v2 完全一致（无任何 delta）。"""
    runner, sink = make_runner(replies=["嗯。"])
    runner.state.meta.streaming_enabled = False
    await runner.player_say("测试甲，你怎么看？")
    names = sink.names()
    assert "message.delta" not in names
    # v2 序列：玩家 final → director.decision → pending → final → cost.update
    assert names[0] == "message.final"
    i = names.index("director.decision")
    assert names[i + 1] == "message.pending"
    assert names[i + 2] == "message.final"


async def test_swipe_delta_resets_offset():
    """swipe 的 delta 从 offset=0 重启（前端截断规则的 API 侧验证）。"""
    runner, sink = make_runner(replies=["旧回复", "新回复"])
    created = await runner.player_say("测试甲，你怎么看？")
    sink.events.clear()
    new = await runner.swipe(created[1].id)
    assert new is not None
    deltas = [p for e, p in sink.events if e == "message.delta"]
    assert deltas and deltas[0]["offset"] == 0  # 重roll 从零重写
    assert "".join(d["delta"] for d in deltas) == "新回复"


async def test_delta_lossy_publish():
    """lossy 发布：队列满时丢最旧不阻塞（R33 防洪泛）。"""
    import asyncio

    from mrp.server.sse import EventBus

    bus = EventBus()
    q = bus.subscribe("sess-x")
    for i in range(256):  # 打满
        q.put_nowait({"event": "x", "data": f"{i}"})
    await bus.publish("sess-x", "message.delta", {"delta": "a", "offset": 0}, lossy=True)
    assert q.qsize() == 256  # 满则丢最旧再放，不增长不阻塞
    assert q.get_nowait()["data"] == "1"  # 最旧(0)被丢弃
    items = list(q.get_nowait() for _ in range(255))
    assert items[-1]["event"] == "message.delta"  # 最新是本次 delta


# ---------- R34 输出卫生 ----------

from mrp.orchestrator.hygiene import FakeJudge, HygieneJudge, JudgeInput  # noqa: E402
from mrp.shared.models import HygieneReport, TokenUsage as _TU  # noqa: E402


def _runner_with_judge(judge, replies):
    """构造带 judge 的 runner（复用 make_runner 的角色/会话脚手架）。"""
    runner, sink = make_runner(replies=replies)
    runner.hygiene_judge = judge
    return runner, sink


async def test_judge_c3_inner_evidence():
    """R34.3 验收：inner'她好烦' + 回复直接回应 → 重试注入含证据与反馈。"""
    judge = FakeJudge(mode="strict")
    runner, _ = _runner_with_judge(judge, replies=["违规回复", "修正后回复"])
    # 玩家内心（C3 证据源）
    await runner.player_say("（内心：她好烦）", channel="inner")
    created = await runner.player_say("测试甲，你怎么看？")

    msg = created[1]
    assert msg.content == "修正后回复"  # strict：首查违规 → 重试 → 第二条 reply
    assert msg.hygiene is not None
    assert msg.hygiene.corrected is True
    assert msg.hygiene.attempts == 2
    # 重试 ctx 注入了 hygiene_feedback（引擎收到的最后一次调用）
    engine = runner.engines._engines["char-a"]
    retry_ctx = engine.calls[-1]
    fb = [i for i in retry_ctx.injections if i.entry_id == "hygiene_feedback"]
    assert fb and "驳回" in fb[0].content
    # C3 证据进入了 judge 输入
    assert judge.calls >= 1
    # 统计
    assert runner.hygiene_stats["auto_corrected"] == 1


async def test_judge_fail_open():
    """校验器异常 → passed=True 放行（绝不阻塞主流程）。"""

    class ExplodingJudge:
        def judge(self, data):
            raise RuntimeError("judge 爆炸(测试注入)")

    runner, _ = _runner_with_judge(ExplodingJudge(), replies=["正常回复"])
    created = await runner.player_say("测试甲，你怎么看？")
    msg = created[1]
    assert msg.content == "正常回复"  # 放行
    # 编排层兜底 fail-open：judge 爆炸 → 无报告（不显示角标），消息照常
    assert msg.hygiene is None or msg.hygiene.passed is True


async def test_judge_retry_still_violating():
    """flag 模式：两查均违规 → 放行 + 角标数据落账（含 variant）。"""
    judge = FakeJudge(mode="flag")
    runner, _ = _runner_with_judge(judge, replies=["违规一", "违规二"])
    created = await runner.player_say("测试甲，你怎么看？")
    msg = created[1]
    assert msg.content == "违规二"  # 重试了一次
    assert msg.hygiene is not None
    assert msg.hygiene.passed is False
    assert msg.hygiene.attempts == 2
    assert msg.hygiene.violations and msg.hygiene.violations[0].category == "C1"
    assert runner.hygiene_stats["violations_found"] == 2  # 两查各记一次


async def test_hygiene_retry_restarts_delta_offsets():
    """R48：R34 重试换新桥——第二次尝试的 delta 从 offset 0 重启（前端按截断规则重写）。"""
    judge = FakeJudge(mode="strict")
    runner, sink = _runner_with_judge(judge, replies=["违规回复文本一二三四", "修正后的完整回复文本"])
    await runner.player_say("测试甲，你怎么看？")
    deltas = [p for e, p in sink.events if e == "message.delta"]
    offsets = [d["offset"] for d in deltas]
    assert offsets and offsets[0] == 0
    assert 0 in offsets[1:], f"重试应从 0 重启: {offsets}"


async def test_judge_cost_separate():
    """R34.4：judge 用量进 by_purpose 独立账，不进 cost_by_model 主账。"""
    judge = FakeJudge(mode="flag")
    runner, _ = _runner_with_judge(judge, replies=["违规一", "违规二"])
    await runner.player_say("测试甲，你怎么看？")
    report = runner.cost_report()
    assert "by_purpose" in report and "hygiene" in report
    assert report["by_purpose"]["judge"]["input_tokens"] > 0
    # 主账不含 judge（fake 引擎的模型名是 deepseek 系列——judge 不出现在 keys）
    assert all("judge" not in k for k in report["by_model"])


async def test_hygiene_disabled():
    """开关关 → judge 不调用。"""
    judge = FakeJudge(mode="flag")
    runner, _ = _runner_with_judge(judge, replies=["回复"])
    runner.state.meta.hygiene_enabled = False
    created = await runner.player_say("测试甲，你怎么看？")
    assert created[1].content == "回复"
    assert judge.calls == 0
    assert created[1].hygiene is None


async def test_judge_unit_prompt_parsing():
    """HygieneJudge 单元：注入式 LlmCall 的 JSON 解析与 fail 语义。"""
    def fake_llm(messages):
        return '{"violations": [{"category": "C3", "evidence": "你心里觉得她烦。"}], "passed": false}'

    judge = HygieneJudge(llm_call=fake_llm)
    report = judge.judge(JudgeInput(
        reply="你心里觉得她烦，对吧？", character_name="测试甲", player_name="玩家",
        player_inner_texts=["她好烦"],
    ))
    assert report.passed is False
    assert report.violations[0].category == "C3"
    assert "她烦" in report.violations[0].evidence

    def ok_llm(messages):
        return '{"violations": [], "passed": true}'

    judge2 = HygieneJudge(llm_call=ok_llm)
    assert judge2.judge(JudgeInput(reply="正常", character_name="A", player_name="B")).passed is True

    def garbage(messages):
        return "完全不是 JSON"

    judge3 = HygieneJudge(llm_call=garbage)
    assert judge3.judge(JudgeInput(reply="x", character_name="A", player_name="B")).passed is True
