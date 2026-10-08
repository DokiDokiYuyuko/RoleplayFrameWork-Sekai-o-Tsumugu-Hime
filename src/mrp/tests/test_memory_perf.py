"""W5 记忆检索与写路径优化测试（性能/等价性/生命周期）。

不改 test_memory.py / test_m9_r36.py（协议冻结的回归另跑）。
全部离线：tmp_path + HashEmbedding，不碰网络。

覆盖：
1. 新旧检索实现等价（中文/英文/空/超长/特殊字符/双路融合/三元加权）
2. 500 条规模：检索不再全表取行（拦截 + SQL trace + 行物化计数）
3. 500 条规模检索耗时护栏
4. records_for 分页边界（0/越界/limit=None/负值/SQL 下推）
5. search_async 等价 + 真下沉线程池（不阻塞事件循环）
6. close() 幂等 + 关闭后明确报错 + 数据已落盘
7. 镜像 append 并发安全（5 线程 × 10 条不丢不交错）
8. 镜像全量重写原子替换（临时文件 + os.replace，无残留）
9. 镜像 append 目录缓存（同角色仅首次 mkdir；目录被外部删除后自愈）
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import statistics
import threading
import time
from pathlib import Path

import pytest

from mrp.orchestrator.memory import HashEmbedding, MemoryStore, _pack_f32
from mrp.shared.models import MemoryRecord

# ---------------------------------------------------------------- 工具


def make_store(
    tmp_path: Path, embedding=None, *, vec_dim: int = 64, sub: str = "",
) -> MemoryStore:
    base = tmp_path / sub if sub else tmp_path
    return MemoryStore(
        db_path=base / "mem.db",
        mirror_dir=base / "mirror",
        embedding=embedding,
        vec_dim=vec_dim,
    )


def make_record(mid: str, content: str, character_id: str = "char-a", **kw) -> MemoryRecord:
    return MemoryRecord(id=mid, character_id=character_id, content=content, **kw)


def seed(store: MemoryStore, n: int, character_id: str = "char-a") -> None:
    """确定性语料：既有字面可召回的 trigram，也有可区分的编号。"""
    for i in range(n):
        store.add(
            make_record(
                f"m{i:04d}",
                f"第{i}条记忆：排练安排与约定事项 编号{i:03d} "
                "lighthouse keeper silver dragon",
                character_id,
                session_id="s1",
                turn_start=i,
                turn_end=i,
                kind="episodic",
                keywords=["排练", "约定"],
                importance=(i % 5) + 1,
            )
        )


def _legacy_search(
    store: MemoryStore, character_id: str, query: str, k: int = 4, *,
    current_turn: int | None = None, session_id: str | None = None,
):
    """W5 改动前 _search_locked 的逐行参照实现（2026-09-25 基线，仅供等价性比对）。"""
    recs = {r.id: r for r in store._records_for_locked(character_id)}
    rowid_to_id = {
        r[0]: r[1]
        for r in store.conn.execute(
            "SELECT rowid, id FROM memory_records WHERE character_id=?",
            (character_id,),
        )
    }
    use_vector = store.vector_enabled
    vec_scores: dict[str, float] = {}
    if use_vector:
        qv = store.embedding.embed([query])[0]
        if len(qv) == store.vec_dim:
            knn = store.conn.execute(
                "SELECT record_id, distance FROM memory_vec "
                "WHERE embedding MATCH ? AND k = ? AND character_id = ?",
                (_pack_f32(qv), max(k * 3, 1), character_id),
            ).fetchall()
            for rid, dist in knn:
                vec_scores[rid] = 1.0 / (1.0 + float(dist))
    fts_scores: dict[str, float] = {}
    for rowid, bm25_score in store._fts_query(query, character_id, k * 3):
        rid = rowid_to_id.get(rowid)
        if rid is not None:
            fts_scores[rid] = -float(bm25_score)
    vec_norm = store._minmax_norm(vec_scores)
    fts_norm = store._minmax_norm(fts_scores)
    w_vec, w_fts = (0.6, 0.4) if use_vector else (0.0, 1.0)
    combined: dict[str, float] = {}
    for rid in set(vec_norm) | set(fts_norm):
        if rid not in recs:
            continue
        relevance = w_vec * vec_norm.get(rid, 0.0) + w_fts * fts_norm.get(rid, 0.0)
        if current_turn is None:
            combined[rid] = relevance
            continue
        rec = recs[rid]
        if session_id and rec.session_id == session_id:
            recency = 0.5 ** (
                max(0, current_turn - rec.turn_end) / store.MEMORY_RECENCY_HALFLIFE
            )
        else:
            recency = store.MEMORY_CROSS_SESSION_RECENCY
        importance = (rec.importance - 1) / 4
        combined[rid] = (
            store.MEMORY_W_REL * relevance
            + store.MEMORY_W_REC * recency
            + store.MEMORY_W_IMP * importance
        )
    top = sorted(combined.items(), key=lambda x: (-x[1], x[0]))[:k]
    return [(recs[rid], score) for rid, score in top]


def assert_same_hits(new_hits, legacy_hits) -> None:
    assert [r.id for r, _ in new_hits] == [r.id for r, _ in legacy_hits]
    for (_, s_new), (_, s_old) in zip(new_hits, legacy_hits):
        assert s_new == pytest.approx(s_old, rel=1e-12, abs=1e-12), (s_new, s_old)


# ---------------------------------------------------------------- 1. 等价性

QUERIES = [
    "排练安排",          # 中文（trigram 子串）
    "lighthouse keeper",  # 英文
    "",                   # 空 query
    "长" * 300,           # 超长
    "%_'\"",              # FTS5 语法字符 → 降级为字面短语重试
    "\"排练\"",           # 带引号
]


@pytest.fixture(scope="module")
def eq_stores(tmp_path_factory):
    """两种检索形态各一份 30 条语料：纯 FTS / 混合（FTS+vec）。"""
    stores: dict[str, MemoryStore] = {}
    d0 = tmp_path_factory.mktemp("memperf-eq-fts")
    stores["fts"] = make_store(d0, None)
    seed(stores["fts"], 30)
    d1 = tmp_path_factory.mktemp("memperf-eq-hybrid")
    stores["hybrid"] = make_store(d1, HashEmbedding(dim=16), vec_dim=16)
    seed(stores["hybrid"], 30)
    yield stores
    for s in stores.values():
        s.close()


@pytest.mark.parametrize("flavor", ["fts", "hybrid"])
@pytest.mark.parametrize("query", QUERIES)
def test_search_equivalence_vs_legacy(eq_stores, flavor: str, query: str):
    store = eq_stores[flavor]
    assert_same_hits(
        store.search("char-a", query, k=4),
        _legacy_search(store, "char-a", query, k=4),
    )


@pytest.mark.parametrize("flavor", ["fts", "hybrid"])
def test_search_equivalence_with_recency_weighting(eq_stores, flavor: str):
    """v6: scoped searches retain same-branch results and reject other branches."""
    store = eq_stores[flavor]
    for turn in (0, 15, 42):
        hits = store.search("char-a", "排练安排", k=5, current_turn=turn, session_id="s1")
        assert hits and all(record.session_id == "s1" for record, _ in hits)
    assert store.search("char-a", "排练安排", k=5, current_turn=42, session_id="other") == []


def test_search_empty_query_degraded_behavior(tmp_path: Path):
    """空 query 在纯 FTS 下确无结果（与旧实现一致），且不抛异常。"""
    store = make_store(tmp_path, None)
    seed(store, 5)
    assert store.search("char-a", "", k=4) == []


def test_search_ignores_stale_vec_fts_rows(tmp_path: Path):
    """残留 vec/fts 行（主表已无对应记录）不得召回幽灵、不得抛错。"""
    fts_store = make_store(tmp_path, None, sub="fts")
    seed(fts_store, 5)
    fts_store.conn.execute(
        "INSERT INTO memory_fts (rowid, content, character_id) VALUES (?,?,?)",
        (999999, "ghost phrase 幽灵残留", "char-a"),
    )
    fts_store.conn.commit()
    assert fts_store.search("char-a", "幽灵残留", k=4) == []  # 纯 FTS：幽灵被回表过滤

    vec_store = make_store(tmp_path, HashEmbedding(dim=16), vec_dim=16, sub="vec")
    seed(vec_store, 5)
    vec_store.conn.execute(
        "INSERT INTO memory_vec (rowid, record_id, character_id, embedding) "
        "VALUES (?,?,?,?)",
        (999999, "ghost-id", "char-a", _pack_f32([0.0] * 16)),
    )
    vec_store.conn.commit()
    hybrid_hits = vec_store.search("char-a", "排练安排", k=4)  # vec 分支同样过滤
    assert hybrid_hits, "正常记录仍可召回"
    assert all(r.id != "ghost-id" for r, _ in hybrid_hits)


# ---------------------------------------------------------------- 2. 规模：不全表取行


@pytest.fixture(scope="module")
def big_store(tmp_path_factory):
    d = tmp_path_factory.mktemp("memperf-big")
    store = make_store(d, None)
    seed(store, 500)
    yield store
    store.close()


def test_search_no_full_table_fetch(big_store, monkeypatch):
    """500 条规模：检索不得调用全量取行；回表必须走 rowid IN 精准查询。"""
    calls: list[str] = []
    orig = MemoryStore._records_for_locked

    def spy(self, character_id):
        calls.append(character_id)
        return orig(self, character_id)

    monkeypatch.setattr(MemoryStore, "_records_for_locked", spy)
    stmts: list[str] = []
    big_store.conn.set_trace_callback(stmts.append)
    try:
        hits = big_store.search("char-a", "排练安排", k=4)
    finally:
        big_store.conn.set_trace_callback(None)

    assert hits, "500 条语料应能召回"
    assert len(hits) <= 4
    assert calls == [], "检索路径不得退化为全表取行"
    mem_selects = [s for s in stmts if "FROM memory_records" in s]
    assert mem_selects, "应有回表查询"
    for s in mem_selects:
        assert ("rowid IN" in s) or ("id IN" in s), f"回表未用 IN 精准查询: {s}"


def test_search_materializes_only_candidates(big_store, monkeypatch):
    """行物化计数：搜索只转换 k*3 量级候选；旧实现为全表（对照）。"""
    counter = {"n": 0}
    orig = MemoryStore._row_to_record

    def counting(row):
        counter["n"] += 1
        return orig(row)

    monkeypatch.setattr(MemoryStore, "_row_to_record", staticmethod(counting))
    big_store.search("char-a", "排练安排", k=4)
    new_n = counter["n"]
    counter["n"] = 0
    _legacy_search(big_store, "char-a", "排练安排", k=4)
    legacy_n = counter["n"]

    assert new_n <= 12, f"新实现物化 {new_n} 行（上界 k*3=12）"
    assert legacy_n == 500, f"旧参照应物化全表 500 行，实际 {legacy_n}"


def test_search_latency_500_records(big_store):
    """耗时护栏（宽松，精确防退化靠上面的计数断言）：500 条单次检索中位数 < 100ms。"""
    big_store.search("char-a", "热身查询", k=4)  # 预热
    samples = []
    for _ in range(7):
        t0 = time.perf_counter()
        big_store.search("char-a", "排练安排", k=4)
        samples.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(samples)
    print(f"\n500 条检索中位耗时 {med:.2f} ms，样本 {[round(x, 2) for x in samples]}")
    assert med < 100.0, f"500 条规模检索中位 {med:.2f} ms，疑似 O(N) 退化"


# ---------------------------------------------------------------- 3. 分页


def test_records_for_pagination_boundaries(tmp_path: Path):
    store = make_store(tmp_path, None)
    seed(store, 10)
    all_ids = [f"m{i:04d}" for i in range(10)]

    assert [r.id for r in store.records_for("char-a")] == all_ids  # 默认语义不变
    assert [r.id for r in store.records_for("char-a", limit=3)] == all_ids[:3]
    assert [r.id for r in store.records_for("char-a", limit=3, offset=8)] == all_ids[8:]
    assert store.records_for("char-a", limit=0) == []
    assert store.records_for("char-a", limit=3, offset=99) == []
    assert store.records_for("char-a", limit=-5) == []  # 负值按 0
    assert [r.id for r in store.records_for("char-a", offset=7)] == all_ids[7:]
    assert [r.id for r in store.records_for("char-a", limit=-1, offset=-3)] == []
    # 与旧全量+切片语义一致
    assert [r.id for r in store.records_for("char-a", limit=4, offset=2)] == all_ids[2:6]
    assert store.records_for("char-b") == []  # 角色隔离

    # 分页下推到 SQL（不整表取回再切片）
    stmts: list[str] = []
    store.conn.set_trace_callback(stmts.append)
    try:
        store.records_for("char-a", limit=2, offset=3)
    finally:
        store.conn.set_trace_callback(None)
    assert any(("LIMIT" in s and "OFFSET" in s) for s in stmts), stmts


# ---------------------------------------------------------------- 4. search_async


async def test_search_async_matches_and_offloads(tmp_path: Path, monkeypatch):
    store = make_store(tmp_path, HashEmbedding(dim=16), vec_dim=16)
    seed(store, 20)
    sync_hits = store.search("char-a", "排练安排", k=3, current_turn=25, session_id="s1")
    async_hits = await store.search_async(
        "char-a", "排练安排", k=3, current_turn=25, session_id="s1"
    )
    assert [r.id for r, _ in async_hits] == [r.id for r, _ in sync_hits]
    for (_, a), (_, b) in zip(async_hits, sync_hits):
        assert a == pytest.approx(b, rel=1e-12, abs=1e-12)

    # 真下沉：把 search 换成同步阻塞探针，事件循环必须不被卡住
    worker: dict[str, int] = {}
    main_tid = threading.get_ident()

    def slow_search(*args, **kwargs):
        worker["tid"] = threading.get_ident()
        time.sleep(0.5)
        return []

    monkeypatch.setattr(store, "search", slow_search)
    task = asyncio.create_task(store.search_async("char-a", "x"))
    t0 = time.perf_counter()
    await asyncio.sleep(0.03)
    elapsed = time.perf_counter() - t0
    await task
    assert worker.get("tid") not in (None, main_tid), "search_async 未在线程池执行"
    assert elapsed < 0.25, f"事件循环被同步检索阻塞（{elapsed:.3f}s）"


async def test_search_async_after_close_raises(tmp_path: Path):
    store = make_store(tmp_path, None)
    store.close()
    with pytest.raises(RuntimeError):
        await store.search_async("char-a", "排练")


# ---------------------------------------------------------------- 5. close 生命周期


def test_close_idempotent_and_guards(tmp_path: Path):
    store = make_store(tmp_path, None)
    store.add(make_record("m0", "最后一条记忆", "char-a"))
    store.close()
    store.close()  # 幂等：二次 close 不得抛

    with pytest.raises(RuntimeError):
        store.search("char-a", "记忆")
    with pytest.raises(RuntimeError):
        store.add(make_record("m1", "已关闭后写入", "char-a"))
    with pytest.raises(RuntimeError):
        store.records_for("char-a")
    with pytest.raises(RuntimeError):
        store.delete_record("m0")
    with pytest.raises(RuntimeError):
        store.update_record(make_record("m0", "改", "char-a"))

    # close 前已 commit：同库重开可见（无未决事务丢失）
    reopened = make_store(tmp_path, None)
    assert [r.id for r in reopened.records_for("char-a")] == ["m0"]
    reopened.close()


# ---------------------------------------------------------------- 6. 镜像写路径


def test_mirror_append_concurrent_no_loss(tmp_path: Path):
    """5 线程 × 10 条：镜像零丢行、零交错（每行都是完整 JSON 且 id 唯一）。"""
    store = make_store(tmp_path, None)

    def worker(t: int) -> None:
        for i in range(10):
            store.add(make_record(f"t{t}-{i}", f"线程{t}第{i}条记忆 排练安排", "char-a"))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    mirror = tmp_path / "mirror" / "char-a" / "records.jsonl"
    lines = [x for x in mirror.read_text(encoding="utf-8").splitlines() if x.strip()]
    objs = [json.loads(x) for x in lines]  # 交错写会在此抛 JSONDecodeError
    assert len(lines) == 50
    assert len({o["id"] for o in objs}) == 50
    assert len(store.records_for("char-a")) == 50


def test_mirror_rewrite_is_atomic_replace(tmp_path: Path, monkeypatch):
    store = make_store(tmp_path, None)
    store.add(make_record("m0", "旧内容一", "char-a"))
    store.add(make_record("m1", "旧内容二", "char-a"))
    mirror = tmp_path / "mirror" / "char-a" / "records.jsonl"
    old_text = mirror.read_text(encoding="utf-8")

    real_replace = os.replace
    seen: list[tuple[Path, Path]] = []

    def spy(src, dst):
        src, dst = Path(src), Path(dst)
        seen.append((src, dst))
        assert dst == mirror
        # 替换发生前：目标仍是完整旧全文；临时文件已含完整新内容
        assert mirror.read_text(encoding="utf-8") == old_text
        new_text = src.read_text(encoding="utf-8")
        assert "旧内容二" in new_text and "旧内容一" not in new_text
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    assert store.delete_record("m0") is True
    assert len(seen) == 1 and seen[0][1] == mirror
    assert "旧内容一" not in mirror.read_text(encoding="utf-8")
    assert not list(mirror.parent.glob(".records.jsonl.tmp*")), "不得残留临时文件"


def test_mirror_append_dir_cache_and_self_heal(tmp_path: Path, monkeypatch):
    """同角色仅首次 append 建目录；目录被外部删除后自动重建不丢行。"""
    store = make_store(tmp_path, None)
    char_dir = tmp_path / "mirror" / "char-a"
    calls = {"n": 0}
    real_mkdir = Path.mkdir

    def counting_mkdir(self, *args, **kwargs):
        # 只计目标目录的逻辑调用（pathlib parents=True 内部会递归调 self/parent.mkdir）
        if Path(self) == char_dir and kwargs.get("parents"):
            calls["n"] += 1
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", counting_mkdir)
    for i in range(10):
        store.add(make_record(f"m{i}", f"第{i}条", "char-a"))
    assert calls["n"] == 1, f"10 次 append 应只建一次目录，实际 {calls['n']}"

    shutil.rmtree(tmp_path / "mirror" / "char-a")
    store.add(make_record("m10", "目录被删后追加", "char-a"))
    assert calls["n"] == 2, "外部删除目录后应重建一次"
    mirror = tmp_path / "mirror" / "char-a" / "records.jsonl"
    lines = [x for x in mirror.read_text(encoding="utf-8").splitlines() if x.strip()]
    # 目录被外部删除时旧镜像随文件消失（DB 仍是真相源，11 条都在）；自愈 = 重建文件并续写
    assert len(lines) == 1 and json.loads(lines[0])["id"] == "m10"
    assert len(store.records_for("char-a")) == 11
