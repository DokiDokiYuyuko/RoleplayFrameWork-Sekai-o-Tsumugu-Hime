import pytest
from mrp.shared.models import Character, CharacterCard
from mrp.shared.player_identity import current_identity, player_key
from mrp.orchestrator.player_control import switch_player, PlayerSwitchConflict
from mrp.shared.actor_labels import build_actor_labels
from mrp.shared.prompt import format_message


async def setup(container):
    for id, name in [("guide", "向导"), ("guard", "守卫"), ("scholar", "学者")]:
        await container.save_character(Character(id=id, card=CharacterCard(name=name, description=f"{name}的背景", traits="观察线索")))
    r = await container.create_session("切换验收", ["guard", "scholar"], "", [], player_character_id="guide")
    await container.persist_session(r)
    return r


async def switch(container, r, target, disposition="npc", key="one", persist=None):
    return await switch_player(r, target_id=target, disposition=disposition,
        expected_revision=r.state.meta.branch_revision, expected_identity=r.state.meta.player_identity_id,
        idempotency_key=key, resolve_character=container.characters.get,
        data_root=container.data_root, persist=persist or container.persist_session)


@pytest.mark.asyncio
async def test_a_b_a_keeps_ownership_and_npc_knowledge(mrp_container):
    r = await setup(mrp_container)
    old = current_identity(r.state)
    secret = (await r.player_say("只有向导知道的秘密", channel="inner"))[0]
    public = (await r.player_say("守卫，请看这张地图", mentions=["guard"]))[0]
    await mrp_container.persist_session(r)
    joined_before = r.state.character_joined_at_seq.get("guard")
    await switch(mrp_container, r, "guard")
    assert player_key(r.state) == "guard"
    assert r.state.character("guard") is None
    assert r.state.character("guide").present
    assert r.state.character_joined_at_seq.get("guard") == (joined_before or 0)
    assert secret in r.state.visible_messages_for("guide")
    assert secret not in r.state.visible_messages_for("guard")
    assert "玩家·向导" in format_message(public, build_actor_labels(r.state, [public]))
    assert await r.regenerate_turn(public.id) is None
    await r.player_say("学者，请继续", mentions=["scholar"])
    await switch(mrp_container, r, "guide", key="two")
    assert current_identity(r.state).id != old.id
    assert current_identity(r.state).character.card.traits == "观察线索"
    assert secret.player_identity_id == old.id
    assert r.state.character("guard") is not None
    await mrp_container.persist_session(r)
    await mrp_container.drop_runner(r.state.meta.id)
    restored = await mrp_container.load_session(r.state.meta.id)
    assert player_key(restored.state) == "guide"
    assert restored.state.messages[0].player_identity_id == old.id


@pytest.mark.asyncio
async def test_duplicate_conflict_and_failed_save_are_atomic(mrp_container):
    r = await setup(mrp_container)
    before = r.state.model_dump()
    async def fail(_):
        raise OSError("模拟保存失败")
    with pytest.raises(OSError):
        await switch(mrp_container, r, "guard", persist=fail)
    assert r.state.model_dump() == before
    await switch(mrp_container, r, "guard")
    count = len(r.state.player_identities)
    assert not await switch_player(r, target_id="guard", disposition="npc", expected_revision=0,
        expected_identity="stale", idempotency_key="one", resolve_character=mrp_container.characters.get,
        data_root=mrp_container.data_root, persist=mrp_container.persist_session)
    assert len(r.state.player_identities) == count
    with pytest.raises(PlayerSwitchConflict):
        await switch_player(r, target_id="guide", disposition="leave", expected_revision=0,
            expected_identity="stale", idempotency_key="other", resolve_character=mrp_container.characters.get,
            data_root=mrp_container.data_root, persist=mrp_container.persist_session)


@pytest.mark.asyncio
async def test_stale_send_and_busy_switch_do_not_mutate(mrp_container):
    r = await setup(mrp_container)
    old = r.state.meta.player_identity_id
    await switch(mrp_container, r, "guard")
    before = r.state.model_dump()
    with pytest.raises(PlayerSwitchConflict):
        await r.turns.player_say("旧草稿", expected_player_identity_id=old)
    assert r.state.model_dump() == before
    async with r.runtime.turn_lock:
        with pytest.raises(PlayerSwitchConflict):
            await switch(mrp_container, r, "guide", key="busy")


@pytest.mark.asyncio
async def test_absent_actor_does_not_gain_away_history(mrp_container):
    r = await setup(mrp_container)
    await switch(mrp_container, r, "guard", "leave")
    response = await r.player_say("学者，讨论一条新线索", mentions=["scholar"])
    assert all(m not in r.state.visible_messages_for("guide") for m in response)
    await switch(mrp_container, r, "guide", key="back")
    assert all(m not in r.state.visible_messages_for("guide") for m in response)
