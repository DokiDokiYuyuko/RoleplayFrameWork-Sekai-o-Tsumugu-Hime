"""Chinese MCP transport and unambiguous model-output parsing, without live data."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mrp.asset_import.service import _json_object
from mrp.lorebook_generation.validation import parse_agent_envelope
from mrp.lorebook_generation import tools as lorebook_tools
from mrp.tests.test_world_organize import (
    ENDPOINT, archive_candidate, install_archive_worker, make_world, start_job, wait_job,
)
from mrp.tests.test_world_organize_lorebook import install_lore_worker, lore_candidate


CHINESE_SOURCE = (
    "月潮晶石只在满月涨潮时出现，离水后十分钟失去光泽。"
    "它需要盐水保存，每次释放潮汐魔力都消耗两枚晶石，寒冷天气无法使用。"
    "特殊标记：🌙、𠮷，必须按原文保存。"
)


def test_chinese_mcp_stdio_is_utf8_despite_windows_cp936_environment(tmp_path):
    source_id = "合成晶石-🌙"
    title = "月潮晶石与𠮷字标记"
    task_file = tmp_path / "snapshot.json"
    task_file.write_text(json.dumps({
        "sources": [{"id": source_id, "title": title, "content": CHINESE_SOURCE,
                     "char_count": len(CHINESE_SOURCE)}],
        "target_lorebook": None,
    }, ensure_ascii=False), encoding="utf-8")
    positive = "我想在满月涨潮时寻找月潮晶石，看到🌙标记后该如何保存？"
    negative = "明天去山上的旧驿站散步，不带任何魔法材料。"
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "list_sources", "arguments": {"page": 0, "limit": 20},
        }},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "read_source", "arguments": {"source_id": source_id},
        }},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
            "name": "simulate_trigger", "arguments": {
                "payload": {"keys": ["月潮晶石"], "content": CHINESE_SOURCE},
                "positive_examples": [positive], "negative_examples": [negative],
            },
        }},
    ]
    env = os.environ.copy()
    env.update(MRP_LOREBOOK_TASK_FILE=str(task_file), PYTHONIOENCODING="cp936", PYTHONUTF8="0",
               PYTHONDONTWRITEBYTECODE="1")
    server = Path(lorebook_tools.__file__).with_name("mcp_server.py")
    process = subprocess.run(
        [sys.executable, str(server)],
        input="".join(json.dumps(row, ensure_ascii=False) + "\n" for row in messages).encode("utf-8"),
        capture_output=True, timeout=15, cwd=server.parents[3], env=env, check=False,
    )
    assert process.returncode == 0, process.stderr.decode("utf-8", errors="backslashreplace")
    stdout = process.stdout.decode("utf-8", errors="strict")
    stderr = process.stderr.decode("utf-8", errors="strict")
    assert stderr == "" and "\ufffd" not in stdout
    responses = [json.loads(line) for line in stdout.splitlines()]
    assert [row["id"] for row in responses] == [1, 2, 3, 4, 5]
    assert any("冻结" in row["description"] for row in responses[1]["result"]["tools"])
    for response in responses[2:]:
        assert response["result"]["isError"] is False, response
    listed = json.loads(responses[2]["result"]["content"][0]["text"])
    read = json.loads(responses[3]["result"]["content"][0]["text"])
    simulation = json.loads(responses[4]["result"]["content"][0]["text"])
    assert listed["sources"][0]["id"] == source_id and listed["sources"][0]["title"] == title
    assert read["text"] == CHINESE_SOURCE and read["next_offset"] is None
    assert simulation["positive"][0]["text"] == positive and simulation["positive"][0]["triggered"] is True
    assert simulation["negative"][0]["text"] == negative and simulation["negative"][0]["triggered"] is False
    assert simulation["valid"] is True
    telemetry = (tmp_path / "telemetry.json").read_text(encoding="utf-8")
    assert "\ufffd" not in telemetry and source_id in telemetry


def envelope(note="合成说明：括号 {条件}、引号 \"限制\" 和🌙必须保留。"):
    source = {"id": "pasted-source", "content": CHINESE_SOURCE}
    entry = lore_candidate(source, "月潮晶石", body=CHINESE_SOURCE)
    entry["positive_examples"] = ["如何在满月涨潮时保存月潮晶石？"]
    entry["negative_examples"] = ["明天去远山驿站散步。"]
    entry["rationale"] = note
    return {"entries": [entry], "coverage_notes": [note]}


def render_reply(value, style="preface_fence"):
    raw = json.dumps(value, ensure_ascii=False)
    if style == "bare":
        return raw
    if style == "fence":
        return "```JSON\n" + raw + "\n```"
    return "已根据原文完成整理，候选如下：\n```json\n" + raw + "\n```\n请审核后保存。"


@pytest.mark.parametrize("parser", [_json_object, parse_agent_envelope], ids=["archive", "lorebook"])
@pytest.mark.parametrize("style", ["bare", "fence", "preface_fence"])
def test_model_output_with_chinese_preface_and_fence_preserves_nested_json(parser, style):
    expected = envelope()
    actual = parser(render_reply(expected, style))
    if hasattr(actual, "model_dump"):
        actual = actual.model_dump(mode="json", exclude_unset=True)
    assert actual == expected


@pytest.mark.parametrize("parser", [_json_object, parse_agent_envelope], ids=["archive", "lorebook"])
@pytest.mark.parametrize("style", ["two_fences", "two_bare", "fence_and_bare"])
def test_conflicting_model_json_objects_are_rejected(parser, style):
    first = envelope("第一份候选：使用盐水保存。")
    second = envelope("第二份候选：使用淡水保存，与第一份不一致。")
    if style == "two_fences":
        reply = render_reply(first, "preface_fence") + "\n另一种结果：\n" + render_reply(second, "fence")
    elif style == "two_bare":
        reply = json.dumps(first, ensure_ascii=False) + "\n" + json.dumps(second, ensure_ascii=False)
    else:
        reply = render_reply(first, "preface_fence") + "\n" + json.dumps(second, ensure_ascii=False)
    with pytest.raises(ValueError):
        parser(reply)


@pytest.mark.parametrize("category", ["archives", "lorebook"])
@pytest.mark.parametrize("ambiguous", [False, True], ids=["preface_fence", "conflicting_objects"])
def test_world_organizer_model_reply_parsing_through_task_pipeline(mrp_client, category, ambiguous):
    world = make_world(mrp_client)
    if category == "archives":
        candidate = archive_candidate("月潮晶石", kind="biology", quote=CHINESE_SOURCE, body=CHINESE_SOURCE)
        output = {"archives": [candidate]}
        other = {"archives": [archive_candidate("冲突的晶石候选", kind="biology", quote=CHINESE_SOURCE, body=CHINESE_SOURCE)]}
        installer = install_archive_worker
    else:
        output = envelope()
        other = envelope("另一份不一致的候选，请勿自动选择。")
        installer = install_lore_worker
    reply = render_reply(output)
    if ambiguous:
        reply += "\n" + render_reply(other, "fence")
    _, calls = installer(mrp_client, reply)
    job = wait_job(mrp_client, start_job(mrp_client, world, category=category, source_text=CHINESE_SOURCE)["id"])
    if ambiguous:
        assert job["status"] == "failed", job
        assert len(calls) == 2 and job["drafts"] == [] and job["errors"]
    else:
        assert job["status"] == "review", job.get("errors")
        assert len(calls) == 1 and len(job["drafts"]) == 1
        payload = job["drafts"][0]["payload"]
        assert payload["body" if category == "archives" else "content"] == CHINESE_SOURCE
    assert job["source_text"] == CHINESE_SOURCE
    assert mrp_client.get(f"/api/v1/worlds/{world['id']}").json() == world
    assert mrp_client.app.state.container.lorebooks == {}



