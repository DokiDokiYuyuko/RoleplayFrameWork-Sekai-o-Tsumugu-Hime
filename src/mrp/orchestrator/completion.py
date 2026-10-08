"""Normalize provider finish reasons without guessing when no reason is supplied."""


def completion_state(reason: str | None) -> str:
    if reason in {"length", "max_tokens", "max-tokens", "max_output_tokens"}:
        return "truncated"
    if reason in {"content_filter", "safety"}:
        return "filtered"
    if reason in {"stop", "end_turn", "eos", "stop_sequence"}:
        return "complete"
    return "unknown"
