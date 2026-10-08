"""Story coordination failures shared by use cases and runtime adapters."""

class RegenerationConflict(ValueError):
    pass


class TurnRunConflict(RuntimeError):
    pass


class ConversationConflict(RuntimeError):
    pass
