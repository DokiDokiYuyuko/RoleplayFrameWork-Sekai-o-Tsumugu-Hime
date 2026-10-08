"""orchestrator/director.py + visibility.py 单元测试（纯逻辑，无网络/数据库）。"""
from __future__ import annotations

import pytest

from mrp.orchestrator.director import Director, extract_mentions
from mrp.orchestrator.visibility import (
    character_context,
    compute_visible_to,
    presence_change,
)
from mrp.shared.models import Character, CharacterCard, Message, SessionMeta, SessionState

SESSION_ID = "sess-test"


# ---------- 构造工具 ----------


def make_char(
    name: str,
    cid: str,
    aliases: list[str] | None = None,
    talkativeness: float = 0.5,
    muted: bool = False,
    present: bool = True,
) -> Character:
    return Character(
        id=cid,
        card=CharacterCard(name=name),
        aliases=aliases or [],
        talkativeness=talkativeness,
        muted=muted,
        present=present,
    )


def make_msg(
    actor: str,
    content: str,
    *,
    turn: int,
    seq: int,
    visible_to="all",
    status: str = "final",
    kind: str = "roleplay",
) -> Message:
    return Message(
        session_id=SESSION_ID,
        seq=seq,
        turn=turn,
        actor=actor,
        content=content,
        visible_to=visible_to,
        status=status,
        kind=kind,
    )


def make_session(
    characters: list[Character], messages: list[Message]
) -> SessionState:
    return SessionState(
        meta=SessionMeta(id=SESSION_ID, character_ids=[c.id for c in characters]),
        characters=characters,
        messages=messages,
    )


# ---------- 提及检测 ----------


def test_mention_chinese_full_name_and_alias_substring():
    cy = make_char("林小雨", "char-cy", aliases=["小雨"])
    hits = extract_mentions("林小雨，你怎么看？", [cy])
    assert hits == [("char-cy", "林小雨")]
    hits = extract_mentions("小雨别闹了", [cy])
    assert hits == [("char-cy", "小雨")]


def test_mention_latin_word_boundary():
    ali = make_char("Alice", "char-ali", aliases=["Alicia"])
    # "Alice" 不匹配 "Alicia"（且互不为子串）；"Alicia" 是别名，全名不命中时别名命中
    hits = extract_mentions("Alicia went home", [ali])
    assert hits == [("char-ali", "Alicia")]
    # 全词边界：Bob 不匹配 Bobcat
    bob = make_char("Bob", "char-bob")
    assert extract_mentions("Look at that Bobcat!", [bob]) == []
    assert extract_mentions("Bob, come here", [bob]) == [("char-bob", "Bob")]
    # 撇号/中西混排均视为边界
    assert extract_mentions("I love Alice's hat", [ali]) == [("char-ali", "Alice")]
    assert extract_mentions("我说Alice你听见没", [ali]) == [("char-ali", "Alice")]


def test_mention_multiple_order_by_position():
    bob = make_char("Bob", "char-bob")
    ali = make_char("Alice", "char-ali")
    hits = extract_mentions("Bob turned to Alice", [ali, bob])
    assert hits == [("char-bob", "Bob"), ("char-ali", "Alice")]
    hits = extract_mentions("Alice turned to Bob", [ali, bob])
    assert hits == [("char-ali", "Alice"), ("char-bob", "Bob")]


def test_mention_same_char_multi_name_takes_earliest():
    cy = make_char("林小雨", "char-cy", aliases=["小雨"])
    hits = extract_mentions("先叫小雨再叫林小雨", [cy])
    assert hits == [("char-cy", "小雨")]


# ---------- decide: mention ----------


def test_decide_mention_queue_in_order():
    bob = make_char("Bob", "char-bob")
    ali = make_char("Alice", "char-ali")
    d = Director().decide("mention", "Bob, ask Alice", [bob, ali], [], rng_seed=1)
    assert d.chosen == ["char-bob", "char-ali"]
    assert [(c.character_id, c.reasons) for c in d.candidates] == [
        ("char-bob", ["mention", "Bob"]),
        ("char-ali", ["mention", "Alice"]),
    ]
    assert d.trigger == "mention"
    assert d.rng_seed == 1
    assert d.turn == 0  # 空 history


def test_decide_mention_excludes_muted_and_absent():
    bob = make_char("Bob", "char-bob", muted=True)
    ali = make_char("Alice", "char-ali", present=False)
    d = Director().decide("mention", "Bob! Alice!", [bob, ali], [], rng_seed=0)
    assert d.chosen == []
    assert d.candidates == []


# ---------- decide: talkativeness 轮盘 ----------


def test_decide_roulette_excludes_muted_and_absent():
    a = make_char("A", "char-a", talkativeness=1.0, muted=True)
    b = make_char("B", "char-b", talkativeness=1.0, present=False)
    c = make_char("C", "char-c", talkativeness=0.2)
    history = [make_msg("player", "大家说说", turn=1, seq=0)]
    d = Director().decide("talkativeness", "", [a, b, c], history, rng_seed=0)
    assert d.chosen == ["char-c"]


def test_decide_roulette_distance_bonus():
    # a: t=0.5，上次发言 turn 0，当前 turn 4 → 距离 4 → 0.5*(1+1.2)=1.1
    # b: t=0.8，上次发言 turn 4 → 距离 0 → 0.8
    a = make_char("A", "char-a", talkativeness=0.5)
    b = make_char("B", "char-b", talkativeness=0.8)
    history = [
        make_msg("player", "开始", turn=0, seq=0),
        make_msg("char-a", "嗯", turn=0, seq=1),
        make_msg("char-b", "好", turn=4, seq=2),
        make_msg("player", "继续", turn=4, seq=3),
    ]
    d = Director().decide("talkativeness", "", [a, b], history, rng_seed=0)
    assert d.chosen == ["char-a"]
    scores = {c.character_id: c.score for c in d.candidates}
    assert scores["char-a"] == pytest.approx(0.5 * (1 + 0.3 * 4))
    assert scores["char-b"] == pytest.approx(0.8)


def test_decide_roulette_never_spoken_counts_elapsed_turns():
    # 从未发言：距离 = 当前 turn → a: 0.5*(1+0.3*4)=1.1 > b(刚在 turn 4 说过): 0.9
    a = make_char("A", "char-a", talkativeness=0.5)
    b = make_char("B", "char-b", talkativeness=0.9)
    history = [make_msg("player", f"t{i}", turn=i, seq=i) for i in range(4)]
    history.append(make_msg("char-b", "我刚说过", turn=4, seq=4))
    d = Director().decide("talkativeness", "", [a, b], history, rng_seed=0)
    assert d.chosen == ["char-a"]


def test_decide_roulette_tie_same_seed_deterministic():
    a = make_char("A", "char-a", talkativeness=0.7)
    b = make_char("B", "char-b", talkativeness=0.7)
    history = [make_msg("player", "你们谁来说", turn=0, seq=0)]
    d1 = Director().decide("talkativeness", "", [a, b], history, rng_seed=42)
    d2 = Director().decide("talkativeness", "", [a, b], history, rng_seed=42)
    assert d1.chosen == d2.chosen
    assert d1.chosen[0] in ("char-a", "char-b")


def test_decide_roulette_consecutive_two_rounds_excluded():
    # 最后两条 final 消息都是 char-b（连续发言 2 轮）→ 即使分数最高也不胜出
    a = make_char("A", "char-a", talkativeness=0.3)
    b = make_char("B", "char-b", talkativeness=1.0)
    history = [
        make_msg("player", "开始", turn=0, seq=0),
        make_msg("char-b", "我说", turn=1, seq=1),
        make_msg("char-b", "我再说", turn=2, seq=2),
    ]
    d = Director().decide("talkativeness", "", [a, b], history, rng_seed=0)
    assert d.chosen == ["char-a"]
    # b 的打分与排除原因保留在 candidates（审计：最高分为何没被选）
    cand_b = next(c for c in d.candidates if c.character_id == "char-b")
    assert cand_b.score == pytest.approx(1.0)  # 距离 0，仍是最高分
    assert "excluded:consecutive_speak" in cand_b.reasons


def test_decide_roulette_all_excluded_empty_chosen():
    a = make_char("A", "char-a", talkativeness=1.0, present=False)
    history = [make_msg("player", "有人吗", turn=0, seq=0)]
    d = Director().decide("talkativeness", "", [a], history, rng_seed=0)
    assert d.chosen == []


def test_decide_roulette_records_turn_and_seed():
    a = make_char("A", "char-a")
    history = [make_msg("player", "说话", turn=7, seq=0)]
    d = Director().decide("talkativeness", "", [a], history, rng_seed=99)
    assert d.turn == 7
    assert d.rng_seed == 99
    assert d.candidates[0].reasons == [
        "talkativeness=0.5",
        "turns_since_last_speak=7",
    ]


# ---------- decide: open_round / manual_force ----------


def test_decide_open_round_order_skips_muted_absent():
    a = make_char("A", "char-a")
    b = make_char("B", "char-b", muted=True)
    c = make_char("C", "char-c", present=False)
    d = make_char("D", "char-d")
    d = Director().decide("open_round", "", [a, b, c, d], [], rng_seed=0)
    assert d.chosen == ["char-a", "char-d"]
    assert [x.character_id for x in d.candidates] == ["char-a", "char-d"]
    assert all(x.reasons == ["open_round"] for x in d.candidates)


def test_decide_manual_force():
    a = make_char("A", "char-a")
    b = make_char("B", "char-b")
    d = Director().decide(
        "manual_force", "", [a, b], [], rng_seed=0, forced_character_id="char-b"
    )
    assert d.chosen == ["char-b"]
    assert d.candidates[0].reasons == ["manual"]


def test_decide_manual_force_requires_target():
    with pytest.raises(ValueError):
        Director().decide("manual_force", "", [], [], rng_seed=0)


# ---------- compute_visible_to ----------


def test_compute_visible_to_all_present():
    chars = [make_char("A", "char-a"), make_char("B", "char-b")]
    assert compute_visible_to(chars) == "all"


def test_compute_visible_to_one_absent():
    chars = [
        make_char("A", "char-a"),
        make_char("B", "char-b", present=False),
        make_char("C", "char-c"),
    ]
    vis = compute_visible_to(chars)
    assert vis == ["char-a", "char-c", "player"]
    assert "char-b" not in vis
    # 不含玩家
    assert compute_visible_to(chars, include_player=False) == ["char-a", "char-c"]


# ---------- character_context ----------


def test_character_context_excludes_absent_period_messages():
    a = make_char("A", "char-a")
    b = make_char("B", "char-b")
    m1 = make_msg("player", "开场白", turn=0, seq=0)  # all
    m2 = make_msg(
        "char-a", "悄悄话", turn=1, seq=1, visible_to=["char-a", "player"]
    )  # b 离席期间
    m3 = make_msg("player", "欢迎回来", turn=2, seq=2)  # all
    session = make_session([a, b], [m1, m2, m3])
    assert character_context(session, "char-b") == [m1, m3]  # 看不到 m2
    assert character_context(session, "char-a") == [m1, m2, m3]
    # Message.can_see 语义兜底
    assert not m2.can_see("char-b")


def test_character_context_scan_turns():
    a = make_char("A", "char-a")
    msgs = [make_msg("player", f"t{i}", turn=i, seq=i) for i in range(5)]
    session = make_session([a], msgs)
    assert character_context(session, "char-a", scan_turns=2) == msgs[3:]
    assert character_context(session, "char-a", scan_turns=1) == [msgs[4]]
    assert character_context(session, "char-a") == msgs


def test_character_context_excludes_retracted():
    a = make_char("A", "char-a")
    m1 = make_msg("player", "留", turn=0, seq=0)
    m2 = make_msg("char-a", "撤回", turn=0, seq=1, status="retracted")
    session = make_session([a], [m1, m2])
    assert character_context(session, "char-a") == [m1]


# ---------- presence_change ----------


def test_presence_change_returns_present_others():
    a = make_char("A", "char-a")
    b = make_char("B", "char-b")
    c = make_char("C", "char-c", present=False)
    session = make_session([a, b, c], [])
    # A 离席：通知当时在场的 B（不含 C，也不含 A 自己）
    assert presence_change(session, "char-a", False) == ["char-b"]
    # C 回归：通知在场 A、B
    assert presence_change(session, "char-c", True) == ["char-a", "char-b"]
