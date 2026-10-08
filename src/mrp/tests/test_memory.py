"""记忆系统单测：MemoryStore（FTS5/混合检索/镜像回写/降级）+ MemoryConsolidator（防误写）。

全部离线：tmp_path + HashEmbedding，不碰网络。
"""
from __future__ import annotations

import json
from pathlib import Path

from mrp.orchestrator.memory import (
    HashEmbedding,
    MemoryConsolidator,
    MemoryStore,
)
from mrp.shared.models import MemoryRecord, Message, SessionMeta, SessionState


def make_store(tmp_path: Path, embedding=None) -> MemoryStore:
    return MemoryStore(
        db_path=tmp_path / "mem.db",
        mirror_dir=tmp_path / "mirror",
        embedding=embedding,
    )


def make_record(content: str, character_id: str = "char-a", **kw) -> MemoryRecord:
    return MemoryRecord(character_id=character_id, content=content, **kw)


def make_session(*messages: Message) -> SessionState:
    return SessionState(meta=SessionMeta(id="sess-1"), messages=list(messages))


def msg(seq: int, turn: int, actor: str, content: str, status: str = "final") -> Message:
    return Message(
        session_id="sess-1",
        seq=seq,
        turn=turn,
        actor=actor,
        content=content,
        status=status,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------- add/search 往返


def test_add_search_roundtrip(tmp_path: Path):
    store = make_store(tmp_path, HashEmbedding())
    store.add(make_record(content="合成检索记录甲：关键词古代航海史"))
    store.add(make_record(content="合成检索记录乙：关键词灯塔"))
    store.add(make_record(content="她在厨房里烤了一个苹果派，大家都说好吃"))

    # 中文关键词召回（FTS5 trigram 生效）
    results = store.search("char-a", "苹果派")
    assert results and "苹果派" in results[0][0].content
    results = store.search("char-a", "古代航海史")
    assert results and "航海史" in results[0][0].content

    # 英文关键词同样召回
    store.add(make_record(content="The lighthouse keeper warned them about the storm"))
    results = store.search("char-a", "lighthouse keeper")
    assert results and "lighthouse" in results[0][0].content

    # 角色隔离：别的角色的记忆不出现在 char-a 的结果里
    store.add(make_record(content="苹果派 秘密食谱只在char-b可见", character_id="char-b"))
    for rec, _score in store.search("char-a", "苹果派"):
        assert rec.character_id == "char-a"


# ---------------------------------------------------------------- 混合检索


def test_hybrid_ranking(tmp_path: Path):
    store = make_store(tmp_path, HashEmbedding())
    query = "silver dragon flying over misty mountains"

    # 词面强命中（BM25 强，token 集合与 query 不完全相同）
    lexical = make_record(
        content=(
            "The silver dragon was seen flying over the misty mountains. "
            "The silver dragon loves the misty mountains."
        )
    )
    # 语义近邻：token 集合与 query 完全相同 → HashEmbedding 向量完全相同（距离 0），
    # 但词序打乱 → trigram 几乎不命中（BM25 弱）
    semantic = make_record(content="mountains over flying dragon misty silver")
    # 无关干扰项
    distractor = make_record(content="She baked an apple pie in the kitchen")
    for rec in (lexical, semantic, distractor):
        store.add(rec)

    results = store.search("char-a", query)
    top_ids = {rec.id for rec, _score in results[:2]}
    assert top_ids == {lexical.id, semantic.id}  # 两路信号各自的第一名融合后稳居前二
    assert distractor.id not in top_ids
    if results and results[-1][0].id == distractor.id:
        assert results[-1][1] < min(
            s for rec, s in results if rec.id in top_ids
        )


# ---------------------------------------------------------------- JSONL 镜像


def test_jsonl_mirror_and_reload(tmp_path: Path):
    store = make_store(tmp_path, HashEmbedding())
    store.add(make_record(content="旧记忆内容alpha"))

    mirror = tmp_path / "mirror" / "char-a" / "records.jsonl"
    assert mirror.exists()
    lines = [json.loads(x) for x in mirror.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["content"] == "旧记忆内容alpha"
    assert set(lines[0]) >= {"id", "kind", "turn_range", "content"}  # 人类可读字段

    # 用户手改镜像 → reload 回写 → search 反映修改
    edited = dict(lines[0], content="手工编辑后的记忆内容beta")
    mirror.write_text(json.dumps(edited, ensure_ascii=False) + "\n", encoding="utf-8")
    assert store.reload_mirrors() == 1

    records = store.records_for("char-a")
    assert len(records) == 1
    assert records[0].content == "手工编辑后的记忆内容beta"
    assert records[0].id == edited["id"]  # id 保留，不产生抖动

    results = store.search("char-a", "手工编辑")
    assert results and "beta" in results[0][0].content

    # 旧内容已不可检索：用旧内容独有的词 "alpha" 查，top-1 是后来新增的
    # 含 alpha 的记录，而不是已改写的前一条（词面+向量双路都应指向新记录）
    store.add(make_record(content="有人又提到了旧记忆内容alpha"))
    results = store.search("char-a", "alpha")
    assert results and "alpha" in results[0][0].content
    assert "beta" not in results[0][0].content


# ---------------------------------------------------------------- 防误写（R5.3）


def test_consolidate_only_final_messages(tmp_path: Path):
    store = make_store(tmp_path, HashEmbedding())
    seen: list[str] = []

    def fake_summarize(text: str) -> str:
        seen.append(text)
        return "这是固定的第三人称摘要"

    cons = MemoryConsolidator(store, summarize=fake_summarize)

    final_a = msg(0, 1, "player", "玩家公开发言一号")
    final_b = msg(1, 2, "char-a", "角色的最终版回复")
    session = make_session(
        final_a,
        final_b,
        msg(2, 2, "char-a", "这条草稿不该进记忆", status="pending"),
        msg(3, 3, "char-a", "这条已撤回不该进记忆", status="retracted"),
    )
    record = cons.consolidate(session, "char-a")

    assert record is not None
    assert record.content == "这是固定的第三人称摘要"
    assert "草稿" not in seen[0]
    assert "撤回" not in seen[0]
    assert "玩家公开发言一号" in seen[0]
    assert "角色的最终版回复" in seen[0]

    # MemoryRecord 字段
    assert record.character_id == "char-a"
    assert record.session_id == "sess-1"
    assert record.source_message_ids == [final_a.id, final_b.id]
    assert record.turn_start == 1
    assert record.turn_end == 2

    # 已落库
    stored = store.records_for("char-a")
    assert len(stored) == 1 and stored[0].id == record.id


def test_consolidate_no_visible_final_returns_none(tmp_path: Path):
    store = make_store(tmp_path, HashEmbedding())
    cons = MemoryConsolidator(store, summarize=lambda t: t)
    session = make_session(
        msg(0, 1, "char-a", "只有 pending", status="pending"),
        msg(1, 2, "char-a", "只有已撤回", status="retracted"),
    )
    assert cons.consolidate(session, "char-a") is None
    assert store.records_for("char-a") == []


def test_consolidate_fallback_summarizer_truncates(tmp_path: Path):
    store = make_store(tmp_path, HashEmbedding())
    cons = MemoryConsolidator(store, summarize=None)  # 本地兜底
    long_content = "长" * 600
    session = make_session(msg(0, 1, "player", long_content))
    record = cons.consolidate(session, "char-a")
    assert record is not None
    assert record.content == f"player: {long_content}"[:500]
    assert len(record.content) == 500


# ---------------------------------------------------------------- 降级路径


def test_degraded_pure_bm25(tmp_path: Path):
    store = make_store(tmp_path, embedding=None)
    assert store.vector_enabled is False
    store.add(make_record(content="记录甲：关于北方雪山的探险故事"))
    store.add(make_record(content="记录乙：关于南方海滩的度假时光"))

    results = store.search("char-a", "北方雪山")
    assert results and "雪山" in results[0][0].content
    for rec, score in results:
        assert 0.0 < score <= 1.0
        assert rec.character_id == "char-a"


# ---------------------------------------------------------------- 向量后端性质


def test_hash_embedding_deterministic():
    e = HashEmbedding()
    a = e.embed(["silver dragon flying over misty mountains"])[0]
    b = e.embed(["silver dragon flying over misty mountains"])[0]
    c = e.embed(["mountains over flying dragon misty silver"])[0]
    d = e.embed(["She baked an apple pie in the kitchen"])[0]
    assert a == b  # 同文本同向量
    assert a == c  # token 集合相同 → 词袋向量相同
    assert a != d
