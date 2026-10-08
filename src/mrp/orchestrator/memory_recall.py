"""Shared deterministic recall for characters and groups; no per-reply auxiliary LLM."""
from __future__ import annotations

from mrp.shared.models import Injection
from mrp.orchestrator.important_memory import visible_memory_messages
from mrp.orchestrator.generation_sources import memory_matches_baseline
from mrp.shared.player_identity import player_key, message_person, memory_participants

LABELS = {"experience": "共同经历", "relationship": "关系与称呼", "unfinished": "事项"}
STATUS = {"open": "尚未完成", "completed": "已完成", "cancelled": "已取消", "unknown": "历史记录"}


def recall_memories(store, state, owner, visible, participants=None, query="", *, audit=None):
    audit = audit if audit is not None else []
    peers = {player_key(state), *(participants or [])} - {owner}
    current = {m.id: m for m in visible_memory_messages(state, owner)}
    recent = {m.id for m in visible[-12:] if m.status == "final"}
    candidates = []
    overrides = getattr(state, "_generation_memory_overrides", {})
    candidates.extend((-1, -r.importance, -r.turn_end, r, "玩家确认的记忆纠错修订")
                      for r in overrides.values() if r.character_id == owner)
    if hasattr(store, "records_for"):
        records = store.records_for(owner, session_id=state.meta.id)
        records = [r for r in records if r.id not in overrides] + [r for r in overrides.values() if r.character_id == owner]
        for r in records:
            participant_ids = memory_participants(state, r) if state.player_identities else set(r.participant_ids)
            if not participant_ids:
                # Compatible old memories can recover identities from their actual sources.
                # Names alone never merge two same-name characters.
                participant_ids.update(message_person(state, current[mid]) or current[mid].actor for mid in r.source_message_ids if mid in current)
            relevant = bool(peers.intersection(participant_ids))
            if not r.invalidated and (relevant or (r.important and not r.participant_ids)):
                priority = 0 if r.important else (1 if r.category in {"relationship", "unfinished"} else 2)
                candidates.append((priority, -r.importance, -r.turn_end, r, "重要标记" if r.important else "当前参与者相关往事"))
    if query.strip():
        for r, score in store.search(owner, query, k=state.meta.memory_top_k,
                                     current_turn=state.current_turn(), session_id=state.meta.id):
            r = overrides.get(r.id, r)
            candidates.append((3, -score, -r.turn_end, r, "普通检索"))
    candidates.sort(key=lambda row: row[:3])
    seen_ids, seen_evidence = set(), set()
    injections = []
    # Newer facts explicitly supersede an earlier agreement, even when the old record was corrected by a player.
    superseded = {i for _, _, _, r, _ in candidates
                  if not r.invalidated and not r.source_changed
                  and memory_matches_baseline(state, r)
                  and all(mid in current for mid in r.source_message_ids)
                  and all(mid in current and current[mid].fingerprint == fp for mid, fp in r.source_fingerprints.items())
                  for i in r.supersedes}
    for _, _, _, r, why in candidates:
        reason = None
        if r.id in seen_ids:
            continue
        seen_ids.add(r.id)
        if r.id in superseded:
            reason = "已被有新证据的事项进展取代"
        elif state.player_identities and "player" in memory_participants(state, r):
            reason = "历史玩家身份未确认，待玩家纠正"
        elif r.invalidated or r.source_changed:
            reason = "来源已改变，待重新整理或玩家确认"
        elif r.session_id and r.session_id != state.meta.id:
            reason = "不属于当前分支"
        elif not memory_matches_baseline(state, r):
            reason = "记忆晚于本次发言的生成时点"
        elif any(i not in current for i in r.source_message_ids):
            reason = "来源不可见或已删除"
        elif any(i not in current or current[i].fingerprint != f for i, f in r.source_fingerprints.items()):
            reason = "来源指纹已改变"
        elif r.id not in overrides and r.source_message_ids and set(r.source_message_ids).issubset(recent):
            reason = "近期对话已经包含来源原文"
        key = (tuple(sorted(r.source_message_ids)), r.category, r.content)
        if reason is None and key in seen_evidence:
            reason = "重复来源与事实"
        row = {"memory_id": r.id, "category": r.category, "source_message_ids": r.source_message_ids,
               "turn_start": r.turn_start, "turn_end": r.turn_end, "important": r.important,
               "disposition": "omitted" if reason else "candidate", "reason": reason or why}
        audit.append(row)
        if reason:
            continue
        seen_evidence.add(key)
        status = f"；{STATUS[r.matter_status]}" if r.category == "unfinished" else ""
        people = memory_participants(state, r) if state.player_identities else set(r.participant_ids)
        identities = {x.person_id: x.name for x in state.player_identities}
        identities.update({c.id: c.card.name for c in state.characters})
        ownership = "、".join(f"{identities.get(i, i)}〔{i}〕" for i in sorted(people))
        content = (f"[历史回忆·第{r.turn_start}–{r.turn_end}轮·{LABELS[r.category]}{status}]\n"
                   f"相关人物：{ownership or '未明确'}\n{r.content}\n"
                   "这记录的是当时的经历；历史地点、去向与状态不代表现在，当前局势以近期对话为准。")
        injections.append(Injection(source="memory", entry_id=r.id, content=content,
                                    anchor="system", order=50, reason=why))
    return injections


def finish_recall_audit(audit, included_ids, omitted_reasons=None):
    included = set(included_ids)
    for row in audit:
        if row["disposition"] == "candidate":
            row["disposition"] = "injected" if row["memory_id"] in included else "omitted"
            if row["memory_id"] not in included:
                row["reason"] = (omitted_reasons or {}).get(row["memory_id"], "模型输入容量不足")
    return audit
