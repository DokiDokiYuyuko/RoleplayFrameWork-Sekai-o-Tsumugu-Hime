"""Request-local ownership and usage hooks; no tasks enter persisted models."""
from contextvars import ContextVar
from typing import Callable

owner: ContextVar[tuple[str, str] | None] = ContextVar("conversation_owner", default=None)
usage_hook: ContextVar[Callable | None] = ContextVar("conversation_usage", default=None)


from mrp.shared.story_errors import ConversationConflict


def charge(usage, purpose: str) -> None:
    callback = usage_hook.get()
    if callback is not None:
        callback(usage, purpose)
