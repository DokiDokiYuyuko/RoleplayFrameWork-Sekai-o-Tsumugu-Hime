"""Detect full-response copies without judging or rewriting their meaning."""
from __future__ import annotations

from difflib import SequenceMatcher
import unicodedata


def _comparison_text(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", text)
        if not char.isspace() and not unicodedata.category(char).startswith("P")
        and unicodedata.category(char) != "Cf"
    )


def is_duplicate_reply(reply: str, previous: str) -> bool:
    """Catch punctuation-only changes and near-verbatim long copies.

    Short conversational answers may legitimately repeat. Require nearly the
    entire long answer to match, rather than flagging shared wording or topics.
    """
    current, old = _comparison_text(reply), _comparison_text(previous)
    if min(len(current), len(old)) < 120:
        return False
    if current == old:
        return True
    if min(len(current), len(old)) / max(len(current), len(old)) < 0.985:
        return False
    matcher = SequenceMatcher(None, current, old)
    return matcher.quick_ratio() >= 0.985 and matcher.ratio() >= 0.985
