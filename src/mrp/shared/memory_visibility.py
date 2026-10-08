"""Pure character/group memory source visibility."""
from mrp.shared.models import SessionState, Message


def visible_memory_messages(state: SessionState, actor_id: str) -> list[Message]:
    group = next((g for g in state.groups if g.id == actor_id), None)
    messages = state.visible_messages_for(actor_id)
    if group:
        messages = [m for m in messages if m.seq >= group.joined_seq and m.scene_id == group.scene_id]
    return [m for m in messages if m.status == "final" and not m.dependency_stale]
