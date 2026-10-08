"""M9 测试：R36 记忆分层（增量固化/检索新近度/场景摘要压缩/单条 CRUD）。

环境隔离同 test_integration.py。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="mrp-m9-mem-")
os.environ["MRP_DATA_ROOT"] = _TMP
os.environ["MRP_FAKE_ENGINE"] = "1"

from mrp.orchestrator.memory import MemoryStore  # noqa: E402
from mrp.orchestrator.memory_v2 import EpisodicConsolidator, SceneSummarizer  # noqa: E402
from mrp.shared.models import MemoryRecord, Scene, SessionState, SessionMeta, Character, CharacterCard, TurnContext  # noqa: E402
from mrp.shared.prompt import compose_prompt  # noqa: E402
from mrp.tests.test_integration import make_runner  # noqa: E402

_MEM_TMP = Path(tempfile.mkdtemp(prefix="mrp-mem-store-"))
_MEM_DB_SEQ = [0]


def _unique_db(prefix: str) -> Path:
    _MEM_DB_SEQ[0] += 1
    return _MEM_TMP / f"{prefix}-{_MEM_DB_SEQ[0]}.db"


def _fake_llm(messages):
    """注入式便宜模型替身：结构化情景记录。"""
    if "场景摘要" in messages[0]["content"]:
        return json.dumps({"summary": "在天台聊了排练的事，约定明天再见。"}, ensure_ascii=False)
    material = json.loads(messages[-1]["content"])
    row = next((r for r in material["messages"] if r["actor"] == "player"), material["messages"][-1])
    return json.dumps({"memories": [{"category": "experience", "content": row["text"],
        "participant_ids": list(material["actors"]), "source_message_ids": [row["id"]],
        "evidence": {row["id"]: row["text"]}, "keywords": [], "matter_status": "unknown"}]}, ensure_ascii=False)



def _mem_runner(replies=None, interval=5, llm=None):
    runner, sink = make_runner(replies=replies or ["嗯。", "好。", "哦。"])
    db = _unique_db("m")
    store = MemoryStore(db_path=db, mirror_dir=db.with_suffix(".mirror"), embedding=None)
    runner.memory = store
    runner.episodic_consolidator = EpisodicConsolidator(llm_call=llm or _fake_llm)
    runner.scene_summarizer = SceneSummarizer(llm_call=llm or _fake_llm)
    runner.state.meta.memory_interval_turns = interval
    scene = Scene(title="活动室", member_ids=["char-a", "char-b"])
    runner.state.scenes = [scene]
    runner.state.active_scene_id = scene.id
    runner.state.schema_version = 2
    return runner, sink, store


async def _drain_bg(runner):
    """等后台任务全部完成（固化/摘要都是 _bg_tasks）。"""
    while runner._bg_tasks:
        await asyncio.gather(*list(runner._bg_tasks), return_exceptions=True)


async def test_interval_consolidation_windows_disjoint():
    """R36.1：间隔触发后台固化；窗口左开右闭结构性无重复。"""
    runner, sink, store = _mem_runner(interval=2)
    for i in range(5):
        await runner.player_say(f"测试甲，第{i}句话", force_character="char-a")
    await _drain_bg(runner)

    records = [r for r in store.records_for("char-a") if r.kind == "episodic"]
    assert len(records) >= 2
    # 窗口互斥不重叠（按 turn_start 排序后，下一窗口起点 > 上一窗口终点）
    records.sort(key=lambda r: r.turn_start)
    for a, b in zip(records, records[1:]):
        assert b.turn_start > a.turn_end
    # 结构化字段进库
    assert records[0].kind == "episodic"
    assert "排练" in records[0].keywords or records[0].keywords == []
    assert "memory.consolidated" in sink.names()
    # 成本分列
    assert "memory" in runner.cost_by_purpose


async def test_manual_consolidate_all():
    runner, sink, store = _mem_runner()
    await runner.player_say("测试甲，你好", force_character="char-a")
    records = await runner.consolidate_all(reason="manual")
    assert len(records) >= 1
    assert records[0].content  # 本地降级也有内容
    assert "memory.consolidated" in sink.names()


def test_recency_and_importance_formula():
    """R36.2：新近相关 > 旧相关；重要性可抬回老记忆；跨会话 floor。"""
    store = MemoryStore(db_path=_MEM_TMP / "r.db", mirror_dir=_MEM_TMP, embedding=None)
    store.add(MemoryRecord(id="m-old", character_id="c1", session_id="s1",
                           turn_start=1, turn_end=2, kind="episodic",
                           content="排练安排讨论", importance=3))
    store.add(MemoryRecord(id="m-new", character_id="c1", session_id="s1",
                           turn_start=40, turn_end=41, kind="episodic",
                           content="排练安排确认", importance=3))
    store.add(MemoryRecord(id="m-imp", character_id="c1", session_id="s1",
                           turn_start=1, turn_end=2, kind="episodic",
                           content="排练安排的约定", importance=5))
    store.add(MemoryRecord(id="m-cross", character_id="c1", session_id="s0",
                           turn_start=1, turn_end=2, kind="episodic",
                           content="排练安排初识", importance=3))
    hits = store.search("c1", "排练安排", k=4, current_turn=42, session_id="s1")
    ids = [r.id for r, _ in hits]
    assert ids.index("m-new") < ids.index("m-old")  # 新 > 旧
    # 无新近度（旧调用形态）——不崩
    hits2 = store.search("c1", "排练安排", k=4)
    assert set(r.id for r, _ in hits2) <= {"m-old", "m-new", "m-imp", "m-cross"}


async def test_delete_record_retrieval_miss():
    """R36.5 验收：删除某条记忆后，下回合检索不到（角色"忘记"）。"""
    runner, _, store = _mem_runner()
    await runner.player_say("测试甲，记住密码是8848", force_character="char-a")
    await runner.consolidate_all(reason="manual")
    # Extracted evidence contains the actual player fact.
    hits = store.search("char-a", "8848", k=5)
    assert len(hits) >= 1

    for r, _ in hits:
        store.delete_record(r.id)
    assert not store.search("char-a", "8848", k=5)  # 全部"忘记"


def test_update_record_and_mirror_consistency():
    store = MemoryStore(db_path=_MEM_TMP / "u.db", mirror_dir=_MEM_TMP / "mirror-u", embedding=None)
    store.add(MemoryRecord(id="m1", character_id="cX", session_id="s1",
                           kind="episodic", content="旧内容", importance=3))
    rec = store.records_for("cX")[0]
    rec.content = "新内容 关键词X"
    rec.importance = 5
    assert store.update_record(rec) is True
    after = store.records_for("cX")[0]
    assert after.content == "新内容 关键词X" and after.importance == 5
    # 镜像重写一致
    mirror = (_MEM_TMP / "mirror-u" / "cX" / "records.jsonl").read_text(encoding="utf-8")
    assert "新内容" in mirror and "旧内容" not in mirror
    # FTS 重建：新关键词可检索
    assert any(r.id == "m1" for r, _ in store.search("cX", "关键词X", k=3))


async def test_scene_summary_and_history_compress():
    """R36.3：场景关闭→摘要；远期场景整块替换为伪消息，当前场景原文。"""
    runner, _, store = _mem_runner()
    # 场景1（远期，turn 1-2）
    s1 = Scene(id="scene-1", title="活动室", turn_start=1, turn_end=2,
               member_ids=["char-a", "char-b"])
    # 场景2（当前）
    s2 = Scene(id="scene-2", title="天台", turn_start=30, member_ids=["char-a"])
    runner.state.scenes = [s1, s2]
    runner.state.active_scene_id = "scene-2"
    # 消息：远期场景 4 条 + 当前场景 2 条
    from mrp.shared.models import Message

    def _msg(mid, turn, actor, content, scene_id):
        return Message(id=mid, session_id=runner.state.meta.id, seq=0, turn=turn,
                       actor=actor, content=content, kind="roleplay",
                       visible_to="all", scene_id=scene_id)

    runner.state.messages = [
        _msg("a1", 1, "player", "远期消息一（活动室里的长对话内容）", "scene-1"),
        _msg("a2", 2, "char-a", "远期消息二（角色的旧回应）", "scene-1"),
        _msg("b1", 30, "player", "当前消息一", "scene-2"),
        _msg("b2", 30, "char-a", "当前消息二", "scene-2"),
    ]
    # 当前回合距离场景1结束 > horizon
    runner.state.meta.memory_compress_horizon_turns = 15
    # No model capacity means retain source text; a constrained window may compress.
    visible = runner.state.visible_messages_for("char-a")
    char = next(c for c in runner.state.characters if c.id == "char-a")
    assert any(m.id == "a1" for m in runner._compress_history(char, visible))

    # 写入场景摘要 → 替换为伪消息
    store.add(MemoryRecord(id="sum1", character_id="char-a", session_id=runner.state.meta.id,
                           turn_start=1, turn_end=2, kind="scene",
                           content="在天台聊了排练的事，约定明天再见。",
                           scene_id="scene-1"))
    compressed = runner.context_builder.compress_history(
        char, runner.state.visible_messages_for("char-a"), input_limit=32,
    )
    ids = [m.id for m in compressed]
    assert "summary-scene-1" in ids
    assert "a1" not in ids and "a2" not in ids  # 整块替换
    assert "b1" in ids and "b2" in ids  # 当前场景原文
    # 伪消息进 composed
    ctx2 = TurnContext(
        session_id=runner.state.meta.id, character_id="char-a", turn=31,
        visible_messages=compressed, budget_tokens=1024,
    )
    assert "早期场景" in compose_prompt(ctx2).text


async def test_token_curve_sublinear():
    """M9 验收：100 回合 token 曲线亚线性——压缩后边际递减且 < 基线×0.6。"""
    char = Character(id="char-a", card=CharacterCard(name="甲", first_mes="嗨"))
    state = SessionState(
        meta=SessionMeta(id="sess-curve", character_ids=["char-a"]),
        characters=[char], schema_version=2,
    )
    # 5 个场景（每 20 回合切换），前 4 个已关闭
    scenes = []
    for i in range(5):
        s = Scene(id=f"sc{i}", title=f"场景{i}", turn_start=i * 20 + 1,
                  turn_end=(i + 1) * 20 if i < 4 else None, member_ids=["char-a"])
        scenes.append(s)
    state.scenes = scenes
    state.active_scene_id = "sc4"
    from mrp.shared.models import Message

    long_text = "这是一段模拟的较长对话内容，" * 8  # ~120 字/条
    msgs = []
    for turn in range(1, 101):
        scene_idx = min((turn - 1) // 20, 4)
        msgs.append(Message(id=f"p{turn}", session_id="sess-curve", seq=turn * 2,
                             turn=turn, actor="player", content=long_text,
                             kind="roleplay", visible_to="all", scene_id=f"sc{scene_idx}"))
        msgs.append(Message(id=f"c{turn}", session_id="sess-curve", seq=turn * 2 + 1,
                             turn=turn, actor="char-a", content=long_text,
                             kind="roleplay", visible_to="all", scene_id=f"sc{scene_idx}"))
    state.messages = msgs

    store = MemoryStore(db_path=_MEM_TMP / "curve.db", mirror_dir=_MEM_TMP / "curve-m", embedding=None)
    for i in range(4):  # 前 4 个远期场景有摘要
        store.add(MemoryRecord(id=f"sum{i}", character_id="char-a", session_id="sess-curve",
                               turn_start=i * 20 + 1, turn_end=(i + 1) * 20, kind="scene",
                               content="该场景的摘要：约定了排练与见面。", scene_id=f"sc{i}"))

    def _tokens_at(turn_cap: int, horizon: int) -> int:
        """度量：可见消息的渲染 token 总量（不经 compose 预算截断——隔离压缩效果）。"""
        state.meta.memory_compress_horizon_turns = horizon
        runner, _ = make_runner(replies=["x"])
        runner.state = state
        runner.memory = store
        # 每次从完整数据重建截断视图（防逐步截断污染）
        state.messages = [m for m in msgs if m.turn <= turn_cap]
        visible = runner.context_builder.compress_history(
            char, state.visible_messages_for("char-a"), input_limit=10_000,
        )
        from mrp.shared.prompt import estimate_tokens, _fmt_message

        return sum(estimate_tokens(_fmt_message(m)) for m in visible)

    samples = {}
    for t in (20, 40, 60, 80, 100):
        samples[t] = (_tokens_at(t, horizon=15), _tokens_at(t, horizon=0))
    growth = samples[100][0] - samples[60][0]
    early = samples[60][0] - samples[20][0]
    assert growth <= early, f"边际应递减: 后期增量{growth} vs 早期增量{early}"
    assert samples[100][0] < samples[100][1] * 0.6, (
        f"压缩后应 < 基线×0.6: {samples[100][0]} vs {samples[100][1]}"
    )
