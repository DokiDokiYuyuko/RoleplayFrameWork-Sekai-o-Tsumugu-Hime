from mrp.shared.models import Character, CharacterCard, Message, SessionMeta, SessionState
from mrp.shared.player_identity import ensure_player_identity, current_identity, bind_message
from mrp.orchestrator.worldline_state import current_state_projection, apply_projection
from mrp.shared.actor_labels import build_actor_labels
from mrp.shared.prompt import format_message


def state_with_player():
    c = Character(id="char-guide", card=CharacterCard(name="向导", appearance="蓝色外套", traits="辨认星象"))
    s = SessionState(meta=SessionMeta(id="test-story", player_character_id=c.id))
    ensure_player_identity(s, c)
    return s


def test_player_snapshot_and_private_knowledge_survive_roundtrip():
    s = state_with_player()
    epoch = current_identity(s)
    m = Message(session_id=s.meta.id, seq=0, turn=1, actor="player", content="我的秘密", kind="inner", visible_to=["player"])
    bind_message(s, m)
    s.messages.append(m)
    restored = SessionState.model_validate_json(s.model_dump_json())
    assert restored.messages[0].can_see("player")
    assert restored.messages[0].can_see(epoch.person_id)
    assert not restored.messages[0].can_see("char-guard")
    assert current_identity(restored).character.card.traits == "辨认星象"


def test_checkpoint_restores_control_and_drops_future_identity():
    s = state_with_player()
    projection = current_state_projection(s)
    old = current_identity(s).id
    s.meta.player_identity_id = "future"
    s.player_identities = []
    apply_projection(s, projection)
    assert current_identity(s).id == old


def test_legacy_unknown_identity_is_not_relabelled_to_current_card():
    s = state_with_player()
    old = Message(session_id=s.meta.id, seq=0, turn=1, actor="player", content="过去的话")
    labels = build_actor_labels(s, [old])
    assert "历史身份未确认" in format_message(old, labels)


def test_frozen_audience_does_not_give_a_later_person_old_history():
    s = state_with_player()
    s.characters.append(Character(id="guard", card=CharacterCard(name="守卫")))
    m = Message(session_id=s.meta.id, seq=0, turn=1, actor="player", content="公开交谈")
    bind_message(s, m)
    assert m.can_see("guard")
    assert not m.can_see("newcomer")


def test_media_is_portable_and_validated(tmp_path):
    import io
    from PIL import Image
    from mrp.storage.story_media import store_media, media_path
    b = io.BytesIO(); Image.new("RGB", (24, 24), "blue").save(b, format="PNG")
    ref = store_media(tmp_path, b.getvalue())
    assert media_path(tmp_path, ref).read_bytes() == b.getvalue()
    import pytest
    with pytest.raises(ValueError):
        media_path(tmp_path, "../secret.png")
    with pytest.raises(ValueError):
        store_media(tmp_path, b.getvalue(), expected_ref="0" * 64 + ".png")


def test_proven_legacy_a_b_a_creates_separate_control_stages():
    from mrp.shared.models import StateRevision
    s = SessionState(meta=SessionMeta(id="legacy", player_character_id="a", player_persona="姓名：向导"))
    for seq, person, name in [(0, "a", "向导"), (1, "b", "守卫"), (2, "a", "向导")]:
        revision = StateRevision(id=f"revision-{seq}", branch_id="legacy", content_hash="hash",
            snapshot={"meta": {"player_character_id": person, "player_persona": f"姓名：{name}"}})
        s.state_revisions.append(revision)
        s.messages.append(Message(session_id="legacy", seq=seq, turn=seq+1, actor="player", content=name,
            post_state_revision_id=revision.id))
    ensure_player_identity(s)
    assert len(s.player_identities) == 3
    assert s.messages[0].player_identity_id != s.messages[2].player_identity_id
    assert s.messages[0].person_id == s.messages[2].person_id == "a"
    assert current_identity(s).start_seq == 2
