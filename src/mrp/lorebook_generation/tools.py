from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from mrp.lorebook_generation.validation import simulation
from mrp.storage.atomic import read_json, write_json_atomic


MAX_TOOL_CALLS = 100
MAX_READ_CHARS = 6000


def _snapshot(task_file: str | Path) -> tuple[dict[str, Any], Path]:
    path = Path(task_file).resolve()
    snapshot = read_json(path)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("sources"), list):
        raise ValueError("任务来源快照不可用")
    return snapshot, path


def _record_call(path: Path, tool_name: str, call_scope: str = "") -> None:
    telemetry_path = path.parent / "telemetry.json"
    telemetry = read_json(telemetry_path, default={}) or {}
    calls = int(telemetry.get("tool_calls", 0)) + 1
    batch_calls = dict(telemetry.get("batch_calls") or {})
    scope = call_scope or path.name
    batch_count = int(batch_calls.get(scope, 0)) + 1
    max_calls = MAX_TOOL_CALLS
    task = read_json(path, default={}) or {}
    if task.get("scoped_organizer"):
        # A dense batch may contain more than twenty legitimate fact clusters.
        # Budget reading, overlap and trigger checks from the supplied material,
        # while keeping the original fixed guard for older task types.
        max_calls += sum(len(str(row.get("content", ""))) for row in task.get("sources", [])) // 4
    if batch_count > max_calls:
        raise ValueError("本批工具调用数已达安全上限")
    batch_calls[scope] = batch_count
    telemetry.update({"tool_calls": calls, "last_tool": tool_name})
    telemetry["batch_calls"] = batch_calls
    write_json_atomic(telemetry_path, telemetry)


def _source(sources: list[dict[str, Any]], source_id: str) -> dict[str, Any]:
    for row in sources:
        if row.get("id") == source_id:
            return row
    raise ValueError("source_id 不属于当前任务快照")


def _similarity(a: str, b: str) -> float:
    normalize = lambda value: re.sub(r"\s+", " ", value.casefold()).strip()
    left, right = normalize(a), normalize(b)
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return 1.0
    return SequenceMatcher(None, left[:8000], right[:8000], autojunk=False).ratio()


def call_tool(task_file: str | Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    snapshot, path = _snapshot(task_file)
    _record_call(path, name, str(snapshot.get("tool_session_id") or ""))
    sources = snapshot["sources"]
    if name == "validate_source_refs":
        from mrp.lorebook_generation.schemas import SourceReference
        from mrp.shared.source_quotes import locate_quote
        refs = arguments.get("source_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError("需要待核对的 source_refs 列表")
        results = []
        source_map = {row["id"]: row for row in sources}
        for index, ref in enumerate(refs):
            try:
                SourceReference.model_validate(ref)
            except ValueError:
                results.append({"index": index, "valid": False,
                    "error": "引用格式不合规；source_id 必须有效，quote 必须为 12–500 字的连续原文，请用 read_source/search_sources 重新复制"})
                continue
            source = source_map.get(ref.get("source_id")) if isinstance(ref, dict) else None
            match = locate_quote(source["content"], ref.get("quote")) if source else None
            if match is None:
                results.append({"index": index, "valid": False,
                    "error": "此引用无法在当前批次逐字定位；请用 read_source/search_sources 复制连续的原文，禁止改写或拼接"})
            else:
                canonical = source["content"][match[0]:match[1]]
                if not 12 <= len(canonical) <= 500:
                    results.append({"index": index, "valid": False,
                        "error": "定位后的原文引用长度不在 12–500 字范围内，请复制一段较短的连续原文"})
                    continue
                results.append({"index": index, "valid": True, "source_id": source["id"],
                    "quote": canonical,
                    "start": source.get("source_offset", 0) + match[0],
                    "end": source.get("source_offset", 0) + match[1]})
        return {"valid": all(row["valid"] for row in results), "references": results}
    if name == "list_sources":
        page = max(0, int(arguments.get("page", 0)))
        limit = min(20, max(1, int(arguments.get("limit", 20))))
        rows = [{key: item.get(key) for key in ("id", "kind", "title", "aliases", "revision", "char_count", "excerpt")}
                for item in sources[page * limit:(page + 1) * limit]
        ]
        return {"sources": rows, "page": page, "has_more": (page + 1) * limit < len(sources)}
    if name == "read_source":
        source = _source(sources, str(arguments.get("source_id", "")))
        offset = max(0, min(len(source["content"]), int(arguments.get("offset", 0))))
        limit = min(MAX_READ_CHARS, max(1, int(arguments.get("max_chars", 4000))))
        telemetry_path = path.parent / "telemetry.json"
        telemetry = read_json(telemetry_path, default={}) or {}
        reads = telemetry.setdefault("reads", {}).setdefault(path.name, {}).setdefault(source["id"], [])
        reads.append([offset, min(len(source["content"]), offset + limit)])
        write_json_atomic(telemetry_path, telemetry)
        return {"source_id": source["id"], "title": source["title"], "offset": offset,
                "text": source["content"][offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(source["content"]) else None,
                "total_chars": len(source["content"])}
    if name == "search_sources":
        query = str(arguments.get("query", "")).strip()
        if len(query) < 2:
            raise ValueError("查询至少需要两个字符")
        limit = min(8, max(1, int(arguments.get("limit", 5))))
        needle = query.casefold()
        results = []
        for source in sources:
            text = source["content"]
            offset = 0
            folded = text.casefold()
            while len(results) < limit:
                found = folded.find(needle, offset)
                if found < 0:
                    break
                start, end = max(0, found - 500), min(len(text), found + len(query) + 1000)
                results.append({"source_id": source["id"], "title": source["title"],
                                "offset": found, "text": text[start:end]})
                offset = found + max(1, len(query))
            if len(results) >= limit:
                break
        return {"query": query, "results": results}
    if name == "list_lorebook_entries":
        book = snapshot.get("target_lorebook")
        if not book:
            return {"target": "new book", "entries": []}
        return {"target": {"id": book["id"], "name": book["name"], "revision": book["revision"]},
                "entries": [{"uid": entry["uid"], "keys": entry.get("keys", []),
                             "content": entry.get("content", "")[:1600],
                             "comment": entry.get("comment", "")}
                            for entry in book.get("entries", [])]}
    if name == "check_overlap":
        content = str(arguments.get("content", ""))
        if not content:
            raise ValueError("候选正文不能为空")
        existing = (snapshot.get("target_lorebook") or {}).get("entries", [])
        for linked in snapshot.get("related_lorebooks", []):
            existing = [*existing, *linked.get("entries", [])]
        matches = []
        for entry in existing:
            score = _similarity(content, str(entry.get("content", "")))
            if score >= 0.45:
                matches.append({"uid": entry.get("uid"), "keys": entry.get("keys", []),
                                "similarity": round(score, 3), "content": entry.get("content", "")[:500]})
        source_matches = []
        for source in sources:
            score = _similarity(content, source["content"])
            if score >= 0.45:
                source_matches.append({"source_id": source["id"], "title": source["title"],
                                       "similarity": round(score, 3)})
        return {"existing_entry_overlap": sorted(matches, key=lambda row: -row["similarity"])[:5],
                "source_overlap": sorted(source_matches, key=lambda row: -row["similarity"])[:5],
                "core_overlap": round(_similarity(content, snapshot.get("core_brief", "")), 3),
                "note": "与来源相似不代表应删除；世界书条目应保留触发时真正需要的增量事实。"}
    if name == "simulate_trigger":
        candidate = {key: arguments.get(key) for key in (
            "payload", "positive_examples", "negative_examples"
        )}
        if not isinstance(candidate["payload"], dict):
            raise ValueError("payload 必须是对象")
        candidate["positive_examples"] = candidate["positive_examples"] or []
        candidate["negative_examples"] = candidate["negative_examples"] or []
        return simulation(candidate, snapshot.get("target_lorebook") or {})
    raise ValueError("不支持的工具")


TOOL_DEFINITIONS = [
    {"name": "validate_source_refs", "description": "逐字核对所有来源引用是否属于当前批次，并返回规范的原文引用。最终输出前请检查每条引用，修正所有无效项。",
     "inputSchema": {"type": "object", "required": ["source_refs"], "properties": {
         "source_refs": {"type": "array", "items": {"type": "object", "required": ["source_id", "quote"], "properties": {
             "source_id": {"type": "string", "minLength": 1, "maxLength": 200},
             "quote": {"type": "string", "minLength": 12, "maxLength": 500}}}}}}},
    {"name": "list_sources", "description": "列出本任务限定世界或角色的冻结来源，包含已授权作者资料，可分页。",
     "inputSchema": {"type": "object", "properties": {"page": {"type": "integer"}, "limit": {"type": "integer"}}}},
    {"name": "read_source", "description": "读取一段冻结的来源正文，最多 6000 个字符；可用 next_offset 继续阅读。",
     "inputSchema": {"type": "object", "required": ["source_id"], "properties": {"source_id": {"type": "string"}, "offset": {"type": "integer"}, "max_chars": {"type": "integer"}}}},
    {"name": "search_sources", "description": "在已选来源中检索具体名称、术语或设定。",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "list_lorebook_entries", "description": "只读查看目标世界书已有条目的触发词与正文摘要。",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "check_overlap", "description": "检查候选正文与已有条目、选定来源的文字重叠。",
     "inputSchema": {"type": "object", "required": ["content"], "properties": {"content": {"type": "string"}}}},
    {"name": "simulate_trigger", "description": "用生产环境同一套世界书触发引擎检查候选正例与反例。",
     "inputSchema": {"type": "object", "required": ["payload", "positive_examples", "negative_examples"], "properties": {
         "payload": {"type": "object"}, "positive_examples": {"type": "array", "items": {"type": "string"}},
         "negative_examples": {"type": "array", "items": {"type": "string"}}}}},
]


def _response(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _dispatch(task_file: str, request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _response(request_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mrp-lorebook", "version": "0.1.0"},
        })
    if method == "ping":
        return _response(request_id, {})
    if method == "tools/list":
        return _response(request_id, {"tools": TOOL_DEFINITIONS})
    if method == "tools/call":
        params = request.get("params") or {}
        try:
            result = call_tool(task_file, str(params.get("name", "")), params.get("arguments") or {})
            text = json.dumps(result, ensure_ascii=False)
            return _response(request_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:  # tool errors are data for the Agent, never process diagnostics on stdout
            return _response(request_id, {
                "content": [{"type": "text", "text": f"工具校验失败：{type(exc).__name__}: {exc}"}],
                "isError": True,
            })
    if request_id is not None:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return None


def main() -> None:
    import os
    import sys

    # MCP stdio is UTF-8 even when the Windows parent uses a legacy code page.
    # Both directions matter: keys and examples arrive in tool-call arguments.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    task_file = os.environ.get("MRP_LOREBOOK_TASK_FILE", "")
    if not task_file:
        raise SystemExit("MRP_LOREBOOK_TASK_FILE is required")
    _snapshot(task_file)  # fail closed before accepting any requests
    for line in sys.stdin:
        try:
            request = json.loads(line)
            result = _dispatch(task_file, request)
            if result is not None:
                sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as exc:  # never write diagnostics to stdout
            sys.stderr.write(f"MCP request error: {type(exc).__name__}: {exc}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
