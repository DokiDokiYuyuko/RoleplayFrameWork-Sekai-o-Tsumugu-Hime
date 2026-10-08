"""Read complete model JSON without treating commentary as structured output."""
from __future__ import annotations

import json
from typing import Any


def json_object(raw: str, *, envelope_key: str | None = None) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("模型没有返回内容")
    text = raw.strip().lstrip("\ufeff")
    try:
        complete = json.loads(text)
    except json.JSONDecodeError:
        complete = None
    else:
        if not isinstance(complete, dict) or (envelope_key is not None and envelope_key not in complete):
            raise ValueError("模型必须返回所要求的 JSON 对象")
        return complete
    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    position = 0
    arrays = False
    while True:
        starts = [offset for offset in (text.find("{", position), text.find("[", position)) if offset >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            value, length = decoder.raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            # Never recover a nested object from a truncated outer envelope.
            if start == 0 or text[start + 1:].lstrip().startswith('"'):
                raise ValueError(f"模型 JSON 第 {exc.lineno} 行、第 {exc.colno} 列格式错误（{exc.msg}）；字符串内的双引号和换行必须正确转义") from exc
            position = start + 1
            continue
        position = start + length
        if isinstance(value, list):
            arrays = True
        if isinstance(value, dict) and (envelope_key is None or envelope_key in value):
            objects.append(value)
    if arrays:
        raise ValueError("模型必须返回一份 JSON 对象，不能返回数组")
    if not objects:
        raise ValueError("模型没有返回完整的 JSON 对象")
    unique = {json.dumps(value, ensure_ascii=False, sort_keys=True): value for value in objects}
    if len(unique) != 1:
        raise ValueError("模型返回多份不一致的 JSON 结果；请只返回一份最终结果")
    return next(iter(unique.values()))
