"""Locate source evidence, tolerating layout whitespace but no rewritten facts."""
from __future__ import annotations


def _layout(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    positions: list[int] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            end = index + 1
            while end < len(text) and text[end].isspace():
                end += 1
            before = text[index - 1] if index else ""
            after = text[end] if end < len(text) else ""
            # Keep word boundaries in Latin text: "no tax" != "not ax".
            if before and after and before.isascii() and after.isascii() and before.isalnum() and after.isalnum():
                chars.append(" ")
                positions.append(index)
            index = end
            continue
        chars.append(char)
        positions.append(index)
        index += 1
    return "".join(chars), positions


def locate_quote(content: str, quote: str) -> tuple[int, int] | None:
    if not isinstance(quote, str) or not quote.strip():
        return None
    exact = content.find(quote)
    if exact >= 0:
        return exact, exact + len(quote)
    normalized, positions = _layout(content)
    needle, _ = _layout(quote)
    if not needle:
        return None
    found = normalized.find(needle)
    if found < 0:
        return None
    return positions[found], positions[found + len(needle) - 1] + 1
