from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from mrp.orchestrator.lorebook import LorebookEngine
from mrp.shared.models import Lorebook, LorebookEntry, Message
from mrp.shared.prompt import estimate_tokens
from mrp.shared.model_output import json_object
from mrp.shared.source_quotes import locate_quote
from mrp.lorebook_generation.schemas import AgentResult


def parse_agent_envelope(raw: str) -> AgentResult:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("整理助手没有返回内容")
    data = json_object(raw, envelope_key="entries")
    return AgentResult.model_validate(data)


def parse_agent_result(raw: str) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in parse_agent_envelope(raw).entries]


def validate_candidate(candidate: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate contract, source quotes, and real positive/negative trigger behavior."""
    sources = {row["id"]: row for row in snapshot.get("sources", [])}
    candidate = AgentResult.model_validate({"entries": [candidate]}).entries[0].model_dump(mode="json")
    refs = candidate["source_refs"]
    if any(ref["source_id"] not in sources for ref in refs):
        raise ValueError("草稿引用了本任务快照之外的来源")
    for ref in refs:
        source = sources[ref["source_id"]]
        match = locate_quote(source["content"], ref["quote"])
        if match is None:
            raise ValueError("草稿来源摘录无法在已保存的来源快照中定位")
        ref["quote"] = source["content"][match[0]:match[1]]
        start = ref.get("start")
        if not isinstance(start, int) or start < 0 or source["content"][start:start + len(ref["quote"])] != ref["quote"]:
            start = source["content"].find(ref["quote"])
        ref.update(revision=source.get("revision"), content_sha256=source.get("content_sha256"),
                   start=start, end=start + len(ref["quote"]))
    payload = dict(candidate["payload"])
    payload.pop("uid", None)
    entry = LorebookEntry.model_validate({"uid": 1, **payload})
    if entry.constant:
        raise ValueError("自动生成条目不能设为常驻；请使用具体关键词触发")
    if not entry.keys or len(entry.keys) > 20:
        raise ValueError("每条草稿需要 1–20 个明确触发关键词")
    if any(not key.strip() or len(key.strip()) < 2 for key in entry.keys):
        raise ValueError("触发关键词不能留空或只有一个字符")
    if len(entry.content.strip()) < 20:
        raise ValueError("条目正文太短，至少需要 20 个字符")
    if len(entry.content) > 50000:
        raise ValueError("条目正文超过 50,000 个字符")
    if not 0 <= entry.probability <= 100:
        raise ValueError("触发概率必须在 0–100 之间")
    if not candidate["positive_examples"] or not candidate["negative_examples"]:
        raise ValueError("草稿必须提供至少一个正例和反例")
    rule_entry = entry.model_copy(deep=True)
    rule_entry.extensions.pop("mrp.runtime_scope", None)
    book = Lorebook(id="agent-preview", name="Agent 触发验收", entries=[rule_entry], scan_depth=8,
                    token_budget=100000, recursive_scanning=False)
    positives = [_triggered(book, example) for example in candidate["positive_examples"]]
    negatives = [_triggered(book, example) for example in candidate["negative_examples"]]
    if entry.enabled:
        if not all(positives):
            raise ValueError("至少一个正例没有触发候选条目")
        if any(negatives):
            raise ValueError("至少一个反例意外触发候选条目；请收窄关键词或补充选择条件")
    elif any(positives) or any(negatives):
        raise ValueError("停用条目在正反例中仍被投递，请检查停用规则")
    candidate["payload"] = entry.model_dump(mode="json", exclude={"uid"})
    candidate["simulation"] = {
        "positive": [{"text": example, "triggered": triggered} for example, triggered in zip(candidate["positive_examples"], positives)],
        "negative": [{"text": example, "triggered": triggered} for example, triggered in zip(candidate["negative_examples"], negatives)],
        "estimated_tokens": estimate_tokens(entry.content),
        "valid": True,
        "mode": "active" if entry.enabled else "disabled",
        "note": "正例须触发，反例不得触发。" if entry.enabled else
                "条目已停用：正反例均不应投递；样例保留，重新启用后恢复严格触发校验。",
    }
    return candidate


def validate_batch_coverage(candidates: list[dict[str, Any]], snapshot: dict[str, Any]) -> None:
    """Coverage is reported for review; character count is not a fact-count test."""
    sources = {str(row["id"]): row for row in snapshot.get("sources", [])}
    for candidate_index, row in enumerate(candidates):
        for ref_index, ref in enumerate(row.get("source_refs", [])):
            source = sources.get(ref["source_id"])
            match = locate_quote(source["content"], ref["quote"]) if source is not None else None
            if match is None:
                raise ValueError(f"第 {candidate_index + 1} 个词条的第 {ref_index + 1} 条引用无法在本批次来源 {ref['source_id']} 定位；请逐字复制 12–180 字的连续原文，不要改写或拼接。草稿引用了批次之外的来源或资料片段")
            ref["quote"] = source["content"][match[0]:match[1]]
            ref["start"] = source.get("source_offset", 0) + match[0]
            ref["end"] = source.get("source_offset", 0) + match[1]


def _triggered(book: Lorebook, text: str) -> bool:
    message = Message(session_id="agent-preview", seq=0, turn=1, actor="player", content=text)
    selected, _ = LorebookEngine.scan_with_diagnostics(book, [message], rng_seed=0)
    return any(item.entry_id == f"{book.id}:1" for item in selected)


def simulation(candidate: dict[str, Any], book_settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run candidate positive/negative examples through the production trigger engine."""
    payload = dict(candidate["payload"])
    payload.pop("uid", None)
    entry = LorebookEntry.model_validate({"uid": 1, **payload})
    settings = book_settings or {}
    existing = [LorebookEntry.model_validate(item) for item in settings.get("entries", [])
                if item.get("uid") != candidate.get("target_uid")
                and item.get("extensions", {}).get("mrp.runtime_scope") != "author"
                and not (item.get("content") == payload.get("content") and item.get("keys") == payload.get("keys"))]
    entry.extensions.pop("mrp.runtime_scope", None)
    entry.uid = max((item.uid for item in existing), default=0) + 1
    candidate_uid = entry.uid
    book = Lorebook(id="agent-preview", name="Agent 触发验收", entries=[*existing, entry],
                    scan_depth=int(settings.get("scan_depth", 8)),
                    token_budget=int(settings.get("token_budget", 100000)),
                    recursive_scanning=bool(settings.get("recursive_scanning", False)))
    def run(values: list[str]) -> list[dict[str, Any]]:
        output = []
        for text in values:
            message = Message(session_id="agent-preview", seq=0, turn=1, actor="player", content=text)
            selected, excluded = LorebookEngine.scan_with_diagnostics(book, [message], rng_seed=0)
            triggered = any(item.entry_id == f"{book.id}:{candidate_uid}" for item in selected)
            output.append({"text": text, "triggered": triggered,
                           "reason": next((item.reason for item in selected if item.entry_id == f"{book.id}:{candidate_uid}"),
                                          excluded.get(f"{book.id}:{candidate_uid}", "未触发"))})
        return output
    positive = run(candidate.get("positive_examples", []))
    negative = run(candidate.get("negative_examples", []))
    return {"positive": positive, "negative": negative,
            "estimated_tokens": estimate_tokens(entry.content),
            "mode": "active" if entry.enabled else "disabled",
            "note": "正例须触发，反例不得触发。" if entry.enabled else
                    "条目已停用：正反例均不应投递；样例保留，重新启用后恢复严格触发校验。",
            "valid": (bool(positive) and all(row["triggered"] for row in positive)
                      and all(not row["triggered"] for row in negative)) if entry.enabled else
                     all(not row["triggered"] for row in [*positive, *negative])}
