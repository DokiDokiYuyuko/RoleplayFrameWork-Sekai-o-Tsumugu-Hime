"""Read-only task-bound tools for the card ideation Agent."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mrp.storage.atomic import read_json, write_json_atomic

MAX_TOOL_CALLS = 80
MAX_READ_CHARS = 5000
CARD_FIELDS = (
    "name", "description", "appearance", "traits_label", "traits", "personality", "scenario", "first_mes",
    "mes_example", "alternate_greetings", "creator_notes", "creator", "tags",
)


def _snapshot(task_file: str | Path) -> tuple[dict[str, Any], Path]:
    path = Path(task_file).resolve()
    snapshot = read_json(path)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("references"), list):
        raise ValueError("角色卡参考快照不可用")
    return snapshot, path


def _reference(snapshot: dict[str, Any], reference_id: str) -> dict[str, Any]:
    for row in snapshot["references"]:
        if row.get("id") == reference_id:
            return row
    raise ValueError("reference_id 不属于当前任务快照")


def _record_call(path: Path, name: str) -> None:
    telemetry_path = path.parent / "telemetry.json"
    telemetry = read_json(telemetry_path, default={}) or {}
    count = int(telemetry.get("tool_calls", 0)) + 1
    if count > MAX_TOOL_CALLS:
        raise ValueError("本任务工具调用数已达安全上限")
    telemetry.update({"tool_calls": count, "last_tool": name})
    write_json_atomic(telemetry_path, telemetry)


def _field_text(card: dict[str, Any], field: str) -> str:
    value = card.get(field)
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if isinstance(item, (str, int, float)))
    return str(value) if isinstance(value, (str, int, float)) else ""


def call_tool(task_file: str | Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    snapshot, path = _snapshot(task_file)
    _record_call(path, name)
    if name == "list_references":
        return {"references": [{key: row.get(key) for key in (
            "id", "source_id", "title", "creator", "source_url", "tags", "content_sha256",
        )} for row in snapshot["references"]]}
    if name == "read_reference":
        reference = _reference(snapshot, str(arguments.get("reference_id", "")))
        field = str(arguments.get("field", "description"))
        if field not in CARD_FIELDS:
            raise ValueError("不支持读取该角色卡字段")
        text = _field_text(reference.get("card", {}), field)
        offset = max(0, min(len(text), int(arguments.get("offset", 0))))
        limit = min(MAX_READ_CHARS, max(1, int(arguments.get("max_chars", 3000))))
        return {"reference_id": reference["id"], "title": reference.get("title", ""),
                "field": field, "offset": offset, "text": text[offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(text) else None,
                "total_chars": len(text)}
    if name == "search_references":
        query = str(arguments.get("query", "")).strip()
        if len(query) < 2:
            raise ValueError("检索词至少需要两个字符")
        limit = min(10, max(1, int(arguments.get("limit", 5))))
        needle = query.casefold()
        found = []
        for reference in snapshot["references"]:
            card = reference.get("card", {})
            for field in CARD_FIELDS:
                content = _field_text(card, field)
                folded = content.casefold()
                start = 0
                while len(found) < limit:
                    index = folded.find(needle, start)
                    if index < 0:
                        break
                    left, right = max(0, index - 400), min(len(content), index + len(query) + 800)
                    found.append({"reference_id": reference["id"], "title": reference.get("title", ""),
                                  "field": field, "offset": index, "excerpt": content[left:right]})
                    start = index + len(query)
                if len(found) >= limit:
                    break
            if len(found) >= limit:
                break
        return {"query": query, "results": found}
    raise ValueError("不支持的只读工具")


TOOL_DEFINITIONS = [
    {"name": "list_references", "description": "列出用户为当前创作任务选定并冻结的角色卡来源。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "read_reference", "description": "读取任务快照中某一张卡的一个文本字段，最多 5000 字符；支持 offset 继续读取。",
     "inputSchema": {"type": "object", "required": ["reference_id", "field"], "properties": {
         "reference_id": {"type": "string"}, "field": {"type": "string", "enum": list(CARD_FIELDS)},
         "offset": {"type": "integer"}, "max_chars": {"type": "integer"}}}},
    {"name": "search_references", "description": "只在当前任务已选的卡片字段中搜索短语并返回上下文片段。",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {
         "query": {"type": "string"}, "limit": {"type": "integer"}}}},
]


def _response(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _dispatch(task_file: str, request: dict[str, Any]) -> dict[str, Any] | None:
    method, request_id = request.get("method"), request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _response(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                                      "serverInfo": {"name": "mrp-card-inspiration", "version": "0.1.0"}})
    if method == "ping":
        return _response(request_id, {})
    if method == "tools/list":
        return _response(request_id, {"tools": TOOL_DEFINITIONS})
    if method == "tools/call":
        params = request.get("params") or {}
        try:
            result = call_tool(task_file, str(params.get("name", "")), params.get("arguments") or {})
            return _response(request_id, {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False})
        except Exception as exc:
            return _response(request_id, {"content": [{"type": "text", "text": f"工具校验失败：{type(exc).__name__}: {exc}"}], "isError": True})
    if request_id is not None:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return None


def main() -> None:
    import os
    import sys

    task_file = os.environ.get("MRP_CARD_MCP_TASK_FILE", "")
    if not task_file:
        raise SystemExit("MRP_CARD_MCP_TASK_FILE is required")
    _snapshot(task_file)
    for line in sys.stdin:
        try:
            result = _dispatch(task_file, json.loads(line))
            if result is not None:
                sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            sys.stderr.write(f"MCP request error: {type(exc).__name__}: {exc}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
