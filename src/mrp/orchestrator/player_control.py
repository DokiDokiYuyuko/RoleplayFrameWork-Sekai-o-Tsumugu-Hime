"""Atomic branch-local transfer of control; never a fictional transformation."""
from __future__ import annotations

import hashlib
import json
from mrp.shared.models import Message, PlayerIdentity
from mrp.shared.player_identity import current_identity
from mrp.shared.prompt import player_persona_from_card
from mrp.storage.story_media import capture_avatar


class PlayerSwitchConflict(ValueError):
    pass


async def switch_player(r, *, target_id, disposition, expected_revision,
                        expected_identity, idempotency_key, entry_brief="",
                        resolve_character, data_root, persist):
    if r.busy() or r.pending_director is not None:
        raise PlayerSwitchConflict("生成或导演确认尚未结束，暂时不能切换角色")
    async with r.runtime.turn_lock:
        state = r.state
        if r.runtime.closed or state.meta.archived:
            raise PlayerSwitchConflict("故事已关闭或归档，不能切换控制角色")
        request_hash = hashlib.sha256(json.dumps([target_id, disposition, entry_brief], ensure_ascii=False).encode()).hexdigest()
        previous = next((x for x in state.player_identities if x.idempotency_key == idempotency_key), None)
        if previous is not None:
            if previous.request_fingerprint != request_hash:
                raise PlayerSwitchConflict("重复请求的切换内容不同")
            return False
        if state.meta.branch_revision != expected_revision or state.meta.player_identity_id != expected_identity:
            raise PlayerSwitchConflict("故事或控制角色已变化，请刷新后重试")
        old = current_identity(state)
        if old is None:
            raise PlayerSwitchConflict("当前身份尚未加载，请刷新")
        if old.person_id == target_id:
            raise ValueError("已经在控制这个人物")
        if disposition not in {"npc", "leave"}:
            raise ValueError("请选择原角色留作 NPC 或离场")
        if disposition == "npc" and old.character is None:
            raise ValueError("原身份没有完整角色卡，不能自动创建 NPC；请选择离场")
        scene = r.active_scene()
        if scene is None:
            raise ValueError("故事缺少当前场景")
        target = state.character(target_id) or state.player_people.get(target_id) or resolve_character(target_id)
        if target is None:
            raise ValueError("目标人物不存在或已删除")
        # Validate the final roster before touching any live state.
        roster = [c for c in state.characters if c.id != target_id and c.id != old.person_id]
        old_character = state.player_people.get(old.person_id) or old.character
        if old_character is not None:
            old_character = old_character.model_copy(deep=True)
            old_character.id = old.person_id
            old_character.present = disposition == "npc"
            old_character.muted = False
            roster.append(old_character)
        if len(roster) > 20:
            raise ValueError("本故事的 NPC 人数已达到上限，请先移除人物")
        original = state.model_copy(deep=True)
        original_pending = r.pending_director
        was_present = state.character(target_id) is not None and target.present
        is_new = state.character(target_id) is None and target_id not in state.player_people
        avatar = capture_avatar(data_root, target.source_asset_id or target_id)
        old_stage = next((x for x in reversed(state.player_identities) if x.person_id == target_id), None)
        if old_stage is not None:
            avatar = old_stage.avatar_ref if old_stage.media_captured else (old_stage.avatar_ref or avatar)
        try:
            target = target.model_copy(deep=True)
            target.present = True
            state.player_people[target_id] = target.model_copy(deep=True)
            if old_character is not None:
                state.player_people[old.person_id] = old_character.model_copy(deep=True)
            state.characters = roster
            state.meta.character_ids = [c.id for c in roster]
            scene.member_ids = [x for x in scene.member_ids if x not in {target_id, old.person_id}]
            if disposition == "npc" and old_character is not None:
                scene.member_ids.append(old.person_id)
            # The controlled person is still physically in this scene.
            scene.member_ids.append(target_id)
            state.character_joined_at_seq.setdefault(old.person_id, old.start_seq)
            state.character_joined_at_seq.setdefault(target_id, state.next_seq() if is_new else 0)
            if is_new:
                state.character_entry_briefs[target_id] = entry_brief.strip() or "\n".join(
                    part for part in (scene.title, scene.description) if part)
            identity = PlayerIdentity(person_id=target_id, source_character_id=target.source_asset_id or target.id,
                name=target.card.name, persona=player_persona_from_card(target.card),
                character=target.model_copy(deep=True), avatar_ref=avatar, media_captured=True, start_seq=state.next_seq(),
                idempotency_key=idempotency_key, request_fingerprint=request_hash, previous_disposition=disposition)
            state.player_identities.append(identity)
            state.meta.player_identity_id = identity.id
            state.meta.player_character_id = identity.source_character_id
            state.meta.player_persona = identity.persona
            r._append_message(Message(session_id=state.meta.id, seq=state.next_seq(), turn=state.current_turn(),
                actor="director", content=f"控制角色已切换：{old.name} → {identity.name}", kind="system_event",
                visible_to=["player"], known_to=[], control_event=True))
            events = []
            if disposition == "leave":
                events.append(f"{old.name}暂时离开当前场景。")
            if not was_present:
                events.append(f"{identity.name}加入当前场景。")
            for content in events:
                r._append_message(Message(session_id=state.meta.id, seq=state.next_seq(), turn=state.current_turn(),
                    actor="director", content=content, kind="system_event"))
            await persist(r)
        except BaseException:
            r.state = original
            r.pending_director = original_pending
            raise
        # Publication happens only after a complete durable commit. Snapshot recovery
        # repairs a missed notification; a sink failure must not undo committed state.
        try:
            await r._emit("session.player.changed", {"player_identity_id": identity.id,
                "branch_revision": state.meta.branch_revision})
        except Exception:
            pass
        return True
