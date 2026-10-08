"""orchestrator/lorebook.py 单元测试：触发判定、二级门矩阵、概率、递归、预算、扫描窗口。"""
from __future__ import annotations

import random

from mrp.orchestrator.lorebook import LorebookEngine
from mrp.shared.models import Injection, Lorebook, LorebookEntry, Message


# ---------- 构造工具 ----------


def msg(content: str, *, turn: int, seq: int, actor: str = "player") -> Message:
    return Message(session_id="sess-test", seq=seq, turn=turn, actor=actor, content=content)


def entry(uid: int, **kw) -> LorebookEntry:
    return LorebookEntry(uid=uid, **kw)


def book(entries: list[LorebookEntry], **kw) -> Lorebook:
    return Lorebook(id="book-1", name="t", entries=entries, **kw)


def ids(out: list[Injection]) -> set[str]:
    return {i.entry_id for i in out}


def by_uid(out: list[Injection], uid: int) -> Injection:
    for i in out:
        if i.entry_id == f"book-1:{uid}":
            return i
    raise AssertionError(f"uid {uid} not injected: {ids(out)}")


def test_diagnostics_explain_excluded_entries_without_changing_scan():
    sample = book([
        entry(0, keys=["灯塔"], content="命中"),
        entry(1, keys=["海盗"], content="未命中"),
        entry(2, keys=["灯塔"], secondary_keys=["钥匙"], selective=True, content="二级"),
        entry(3, keys=["灯塔"], probability=0, content="概率"),
        entry(4, keys=["灯塔"], content="关闭", enabled=False),
        entry(5, constant=True, content="太长" * 100),
    ], token_budget=10)
    window = [msg("我走进灯塔", turn=1, seq=1)]
    selected, excluded = LorebookEngine.scan_with_diagnostics(sample, window, rng_seed=5)
    assert selected == LorebookEngine.scan(sample, window, rng_seed=5)
    assert ids(selected) == {"book-1:0"}
    assert "关键词未命中" in excluded["book-1:1"]
    assert "二级关键词" in excluded["book-1:2"]
    assert "概率未通过" in excluded["book-1:3"]
    assert excluded["book-1:4"] == "已关闭"
    assert "预算" in excluded["book-1:5"]


# ---------- 触发基础 ----------


def test_constant_unconditional():
    e = entry(0, constant=True, content="世界规则")
    out = LorebookEngine.scan(book([e]), [])  # 空窗口也注入
    assert len(out) == 1
    inj = out[0]
    assert inj.source == "lorebook"
    assert inj.entry_id == "book-1:0"
    assert inj.reason == "constant"
    assert inj.tokens == len("世界规则") // 2
    assert inj.anchor == "system" and inj.order == 100


def test_key_substring_case_insensitive():
    e = entry(0, keys=["Magic"], content="魔法体系")
    out = LorebookEngine.scan(book([e]), [msg("there is MAGIC here", turn=0, seq=0)])
    assert ids(out) == {"book-1:0"}
    assert out[0].reason == "key:Magic"
    assert LorebookEngine.scan(book([e]), [msg("nothing to see", turn=0, seq=0)]) == []


def test_key_chinese_substring():
    e = entry(0, keys=["魔法"], content="禁咒")
    out = LorebookEngine.scan(book([e]), [msg("他施展了魔法", turn=0, seq=0)])
    assert ids(out) == {"book-1:0"}


def test_regex_key():
    e = entry(0, keys=["/魔[法術]/"], content="译名统一")
    out = LorebookEngine.scan(book([e]), [msg("魔術師登場", turn=0, seq=0)])
    assert ids(out) == {"book-1:0"}
    assert out[0].reason.startswith("regex:")


def test_regex_case_sensitive():
    e = entry(0, keys=["/[A-Z]{4}/"], content="x")
    assert LorebookEngine.scan(book([e]), [msg("abcd", turn=0, seq=0)]) == []
    assert ids(LorebookEngine.scan(book([e]), [msg("xxABCDxx", turn=0, seq=0)])) == {"book-1:0"}


def test_multiple_keys_any_hit():
    e = entry(0, keys=["alpha", "beta"], content="x")
    out = LorebookEngine.scan(book([e]), [msg("beta 落下了", turn=0, seq=0)])
    assert ids(out) == {"book-1:0"}
    assert out[0].reason == "key:beta"


def test_empty_keys_never_triggers():
    e = entry(0, content="x")  # 非 constant 且无 key
    assert LorebookEngine.scan(book([e]), [msg("随便什么", turn=0, seq=0)]) == []


def test_disabled_entry_skipped():
    e = entry(0, keys=["魔法"], content="x", enabled=False)
    assert LorebookEngine.scan(book([e]), [msg("魔法", turn=0, seq=0)]) == []


# ---------- selective_logic 二级门矩阵 ----------

TEXT_NONE = "the gate opens"  # 无次级 key
TEXT_X = "the gate opens x"  # 仅 x
TEXT_XY = "the gate opens x and y"  # x、y 都在


def _gated_hit(logic: int, text: str) -> bool:
    e = entry(0, keys=["gate"], secondary_keys=["x", "y"], selective=True, selective_logic=logic)
    return bool(LorebookEngine.scan(book([e]), [msg(text, turn=0, seq=0)]))


def test_selective_logic_matrix():
    # 0 = AND_ANY：任一次级 key 命中才过
    assert _gated_hit(0, TEXT_NONE) is False
    assert _gated_hit(0, TEXT_X) is True
    assert _gated_hit(0, TEXT_XY) is True
    # 3 = AND_ALL：全部次级 key 命中才过
    assert _gated_hit(3, TEXT_NONE) is False
    assert _gated_hit(3, TEXT_X) is False
    assert _gated_hit(3, TEXT_XY) is True
    # 1 = NOT_ALL：仅"全部命中"时排除（部分/全不命中都过）
    assert _gated_hit(1, TEXT_NONE) is True
    assert _gated_hit(1, TEXT_X) is True
    assert _gated_hit(1, TEXT_XY) is False
    # 2 = NOT_ANY：任一命中即排除
    assert _gated_hit(2, TEXT_NONE) is True
    assert _gated_hit(2, TEXT_X) is False
    assert _gated_hit(2, TEXT_XY) is False


def test_selective_gate_off_without_secondary_keys():
    e = entry(0, keys=["gate"], selective=True, secondary_keys=[], selective_logic=2)
    assert ids(LorebookEngine.scan(book([e]), [msg(TEXT_X, turn=0, seq=0)])) == {"book-1:0"}


def test_selective_gate_off_when_flag_false():
    e = entry(0, keys=["gate"], selective=False, secondary_keys=["x"], selective_logic=2)
    assert ids(LorebookEngine.scan(book([e]), [msg(TEXT_X, turn=0, seq=0)])) == {"book-1:0"}


# ---------- probability ----------


def test_probability_100_always_and_0_never():
    e100 = entry(0, keys=["k"], content="x", probability=100)
    e0 = entry(1, keys=["k"], content="x", probability=0)
    b = book([e100, e0])
    for seed in range(10):
        out = LorebookEngine.scan(b, [msg("k", turn=0, seq=0)], rng_seed=seed)
        assert ids(out) == {"book-1:0"}
        assert out[0].reason == "key:k"  # p=100 不掷骰，reason 无概率部分


def test_probability_roll_matches_shared_rng():
    e = entry(0, keys=["k"], content="x", probability=50)
    b = book([e])
    for seed in (0, 1, 2, 3, 4):
        roll = random.Random(seed).randint(1, 100)  # 单条目：首个掷骰即首消费
        out = LorebookEngine.scan(b, [msg("k", turn=0, seq=0)], rng_seed=seed)
        assert (len(out) == 1) == (roll <= 50)
        if roll <= 50:
            assert f"probability:{roll}" in out[0].reason


def test_probability_deterministic_same_seed():
    e = entry(0, keys=["k"], content="x", probability=50)
    b = book([e])
    w = [msg("k", turn=0, seq=0)]
    r1 = LorebookEngine.scan(b, w, rng_seed=7)
    r2 = LorebookEngine.scan(b, w, rng_seed=7)
    assert [(i.entry_id, i.reason) for i in r1] == [(i.entry_id, i.reason) for i in r2]


# ---------- 一级递归 ----------


def test_recursive_activation():
    a = entry(0, keys=["阿卡"], content="阿卡城里有贝卡商会")
    b = entry(1, keys=["贝卡"], content="贝卡商会垄断魔晶")
    out = LorebookEngine.scan(book([a, b]), [msg("他们抵达阿卡", turn=0, seq=0)])
    assert ids(out) == {"book-1:0", "book-1:1"}
    assert by_uid(out, 0).reason == "key:阿卡"
    assert by_uid(out, 1).reason.startswith("recursive")


def test_recursive_disabled():
    a = entry(0, keys=["阿卡"], content="阿卡城里有贝卡商会")
    b = entry(1, keys=["贝卡"], content="x")
    out = LorebookEngine.scan(
        book([a, b], recursive_scanning=False), [msg("他们抵达阿卡", turn=0, seq=0)]
    )
    assert ids(out) == {"book-1:0"}


def test_exclude_recursion():
    a = entry(0, keys=["阿卡"], content="阿卡城里有贝卡商会")
    b = entry(1, keys=["贝卡"], content="x", extensions={"excludeRecursion": True})
    out = LorebookEngine.scan(book([a, b]), [msg("他们抵达阿卡", turn=0, seq=0)])
    assert ids(out) == {"book-1:0"}  # B 不被递归激活
    # 但 B 一级正常触发不受影响
    out2 = LorebookEngine.scan(book([a, b]), [msg("贝卡涨价了", turn=0, seq=0)])
    assert ids(out2) == {"book-1:1"}


def test_prevent_recursion():
    a = entry(0, keys=["阿卡"], content="阿卡城里有贝卡商会", extensions={"preventRecursion": True})
    b = entry(1, keys=["贝卡"], content="x")
    out = LorebookEngine.scan(book([a, b]), [msg("他们抵达阿卡", turn=0, seq=0)])
    assert ids(out) == {"book-1:0"}  # A 的内容不参与递归扫描文本


def test_mutual_recursion_terminates():
    a = entry(0, keys=["甲"], content="甲派与乙派世仇")  # content 含 B 的 key
    b = entry(1, keys=["乙"], content="乙派盯上甲派")  # content 含 A 的 key
    out = LorebookEngine.scan(book([a, b]), [msg("甲", turn=0, seq=0)])  # 能返回即无死循环
    assert ids(out) == {"book-1:0", "book-1:1"}
    assert by_uid(out, 0).reason == "key:甲"
    assert by_uid(out, 1).reason.startswith("recursive")


# ---------- 预算 ----------


def _budget_book(token_budget: int) -> Lorebook:
    big = entry(0, keys=["big"], content="B" * 200, order=100)  # tokens=100
    small = entry(1, keys=["small"], content="s" * 20, order=10)  # tokens=10
    return book([big, small], token_budget=token_budget)


def test_budget_prefers_high_order():
    w = [msg("big and small", turn=0, seq=0)]
    # 充足：全保留
    assert ids(LorebookEngine.scan(_budget_book(110), w)) == {"book-1:0", "book-1:1"}
    # 105：big(100) 保住，small 加不进（100+10>105）被裁
    assert ids(LorebookEngine.scan(_budget_book(105), w)) == {"book-1:0"}
    # 50：big 放不下被丢，small 仍可填入（贪心继续尝试更小的）
    assert ids(LorebookEngine.scan(_budget_book(50), w)) == {"book-1:1"}


def test_budget_param_overrides_default():
    b = _budget_book(105)
    w = [msg("big and small", turn=0, seq=0)]
    assert ids(LorebookEngine.scan(b, w, budget=200)) == {"book-1:0", "book-1:1"}


# ---------- 扫描窗口 ----------


def test_scan_depth_limits_window():
    e_old = entry(0, keys=["老地方"], content="x")
    e_new = entry(1, keys=["扎营"], content="y")
    b = book([e_old, e_new], scan_depth=2)
    w = [
        msg("我们在老地方碰头", turn=0, seq=0, actor="char-b"),
        msg("今天天气不错", turn=1, seq=1),
        msg("继续赶路", turn=2, seq=2, actor="char-a"),
        msg("今晚扎营", turn=3, seq=3, actor="char-a"),
    ]
    out = LorebookEngine.scan(b, w)
    assert ids(out) == {"book-1:1"}  # turn0 不在最近 2 回合，"老地方"不触发


def test_player_latest_always_included():
    e = entry(0, keys=["老地方"], content="x")
    b = book([e], scan_depth=1)
    w = [
        msg("我们到老地方集合", turn=0, seq=0),  # 玩家最新（唯一玩家消息）
        msg("好", turn=1, seq=1, actor="char-a"),  # 最近 1 回合只有这条
    ]
    out = LorebookEngine.scan(b, w)
    assert ids(out) == {"book-1:0"}


# ---------- Injection 字段与输出序 ----------


def test_injection_fields_passthrough():
    e = entry(0, keys=["k"], content="abcdefgh", anchor="at_depth", depth=7, order=42)
    out = LorebookEngine.scan(book([e]), [msg("k", turn=0, seq=0)])
    inj = out[0]
    assert inj.anchor == "at_depth" and inj.depth == 7 and inj.order == 42
    assert inj.content == "abcdefgh"
    assert inj.tokens == 4  # len//2
    assert inj.source == "lorebook"


def test_output_ordered_by_priority():
    lo = entry(0, keys=["a"], content="x", order=10)
    hi = entry(1, keys=["b"], content="y", order=200)
    out = LorebookEngine.scan(book([lo, hi]), [msg("a b", turn=0, seq=0)])
    assert [i.order for i in out] == [200, 10]  # order 降序（影响大者优先）
