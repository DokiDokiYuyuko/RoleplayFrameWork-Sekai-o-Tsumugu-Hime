"""Restore committed state while preserving existing message object identities."""
from mrp.shared.models import SessionState


def restore_state_in_place(state: SessionState, before: SessionState) -> None:
    live = {m.id: m for m in state.messages}
    messages = []
    for old in before.messages:
        message = live.get(old.id, old.model_copy(deep=True))
        restored = old.model_copy(deep=True)
        for name in type(old).model_fields:
            setattr(message, name, getattr(restored, name))
        messages.append(message)
    restored_state = before.model_copy(deep=True)
    for name in type(before).model_fields:
        if name != "messages":
            setattr(state, name, getattr(restored_state, name))
    state.messages = messages

