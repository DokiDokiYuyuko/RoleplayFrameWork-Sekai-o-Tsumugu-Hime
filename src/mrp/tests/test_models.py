"""shared/models.py 单元测试：指纹、可见性、序列化往返、便捷方法。"""
from __future__ import annotations

from mrp.shared.models import (
    Character,
    CharacterCard,
    DirectorDecision,
    Lorebook,
    LorebookEntry,
    Message,
    SaveFile,
    SessionMeta,
    SessionState,
    fingerprint,
)


def make_session() -> SessionState:
    card = CharacterCard(name="测试甲", description="合成角色甲简介")
    char = Character(id="char-a", card=card, aliases=["测试别名甲三"])
    state = SessionState(meta=SessionMeta(id="sess-1", character_ids=["char-a"]), characters=[char])
    return state


def test_fingerprint_deterministic_and_sensitive():
    a = fingerprint("char-a", 1, "你好")
    assert a == fingerprint("char-a", 1, "你好")
    assert a != fingerprint("char-a", 2, "你好")  # seq 变
    assert a != fingerprint("char-b", 1, "你好")  # actor 变
    assert a != fingerprint("char-a", 1, "你好！")  # content 变


def test_message_autofingerprint():
    m = Message(session_id="s", seq=0, turn=0, actor="player", content="hi")
    assert m.fingerprint == fingerprint("player", 0, "hi")


def test_visibility_two_modes_and_retracted():
    pub = Message(session_id="s", seq=0, turn=0, actor="player", content="x")
    assert pub.can_see("char-a") and pub.can_see("char-b")

    priv = Message(session_id="s", seq=1, turn=0, actor="player", content="y", visible_to=["char-a"])
    assert priv.can_see("char-a")
    assert not priv.can_see("char-b")

    retracted = Message(session_id="s", seq=2, turn=0, actor="char-a", content="z", status="retracted")
    assert not retracted.can_see("char-a")  # 对角色等价于从未发生
    assert not retracted.can_see("player")


def test_session_roundtrip_json():
    state = make_session()
    state.messages.append(
        Message(session_id="sess-1", seq=0, turn=0, actor="player", content="大家好", visible_to=["char-a"])
    )
    state.director_log.append(
        DirectorDecision(turn=1, trigger="mention", chosen=["char-a"], rng_seed=42)
    )
    save = SaveFile(state=state, memory_refs=["mem-1"])

    restored = SaveFile.model_validate_json(save.model_dump_json())
    assert restored.state.meta.id == "sess-1"
    assert restored.state.messages[0].fingerprint == state.messages[0].fingerprint
    assert restored.state.director_log[0].rng_seed == 42
    assert restored.memory_refs == ["mem-1"]
    assert restored.schema_version == 1


def test_session_helpers():
    state = make_session()
    assert state.next_seq() == 0 and state.current_turn() == 0
    state.messages.append(Message(session_id="sess-1", seq=0, turn=0, actor="player", content="hi"))
    state.messages.append(
        Message(session_id="sess-1", seq=1, turn=0, actor="char-a", content="嗯", visible_to=["char-a", "player"])
    )
    assert state.next_seq() == 2
    assert state.current_turn() == 0

    # char-a 能看到全部两条；char-b 只能看到公开那条
    assert len(state.visible_messages_for("char-a")) == 2
    assert len(state.visible_messages_for("char-b")) == 1

    assert state.last_turn_of("char-a") == 0
    assert state.last_turn_of("char-b") is None
    assert state.character("char-a").card.name == "测试甲"
    assert state.character("char-b") is None


def test_character_mention_names_dedup():
    card = CharacterCard(name=" 测试乙 ")
    c = Character(card=card, aliases=["测试乙", "测试别名乙 ", ""])
    assert c.mention_names == ["测试乙", "测试别名乙"]


def test_lorebook_defaults_tolerant():
    """缺字段容错（R4.1）：几乎全空的条目也能构造。"""
    e = LorebookEntry(uid=0)
    assert e.keys == [] and e.constant is False and e.order == 100 and e.probability == 100
    book = Lorebook()
    assert book.entries == [] and book.scan_depth == 2
