"""Evidence-only branch recaps. Never synthesize facts or mix branch histories."""
from __future__ import annotations

from mrp.shared.player_identity import message_person


def _actor_exists(state, actor):
    return actor == "player" or actor in {c.id for c in state.characters} | {g.id for g in state.groups} | {x.person_id for x in state.player_identities}


def review_branch(state, memory_store, *, actor_id="player", anchor_message_id=None, after_seq=None, limit=100):
    if not _actor_exists(state, actor_id):
        raise ValueError("视角人物不属于这条路线")
    ordered = sorted(state.messages, key=lambda m: m.seq)
    anchor = next((m for m in ordered if m.id == anchor_message_id), None) if anchor_message_id else None
    if anchor_message_id and (anchor is None or not anchor.can_see(actor_id)):
        raise ValueError("回顾锚点不存在或当前人物不可见")
    prefix = [m for m in ordered if anchor is None or m.seq <= anchor.seq]
    # Session visibility includes entry boundaries, known_to, retractions and stale dependencies.
    visible_ids = {m.id for m in state.visible_messages_for(actor_id) if m.status == "final"}
    if anchor is not None and anchor.id not in visible_ids:
        raise ValueError("回顾锚点不是该人物当前有效可见的消息")
    visible = [m for m in prefix if m.id in visible_ids]
    evidence = {m.id: m for m in visible}
    displayed = [m for m in visible if after_seq is None or m.seq > after_seq]
    labels = {c.id: c.card.name for c in state.characters}
    labels.update({g.id: g.label for g in state.groups})
    labels.update({x.person_id: x.name for x in state.player_identities})
    memories = []
    owners = [actor_id] if actor_id != "player" else [c.id for c in state.characters] + [g.id for g in state.groups]
    for owner in owners:
        for record in memory_store.records_for(owner, session_id=state.meta.id):
            source_ids = record.source_message_ids
            if record.invalidated or record.source_changed or not source_ids or not all(x in evidence for x in source_ids):
                continue
            if record.effective_message_id and record.effective_message_id not in evidence:
                continue
            if any(x not in evidence or evidence[x].fingerprint != fp for x, fp in record.source_fingerprints.items()):
                continue
            if anchor is not None and (record.created_at > anchor.created_at or record.revision > 1):
                # A later extraction/edit is not proof that this fact was known at an earlier anchor.
                continue
            if after_seq is not None and not any(evidence[x].seq > after_seq for x in source_ids):
                continue
            memories.append({"id": record.id, "owner_id": owner, "content": record.content,
                "category": record.category, "matter_status": record.matter_status,
                "message_ids": source_ids, "important": record.important})
    superseded = {x for owner in owners for r in memory_store.records_for(owner, session_id=state.meta.id)
                  if not r.invalidated and not r.source_changed and r.id in {x["id"] for x in memories} for x in r.supersedes}
    memories = [r for r in memories if r["id"] not in superseded]
    events = [{"id": e.id, "title": e.title, "summary": e.summary, "kind": e.kind,
               "message_ids": [e.anchor_message_id]} for e in state.story_events
              if e.anchor_message_id in evidence and (e.visible_to == "all" or actor_id in e.visible_to)
              and (anchor is None or e.created_at <= anchor.created_at)
              and (after_seq is None or evidence[e.anchor_message_id].seq > after_seq)]
    return {"branch_id": state.meta.id, "branch_name": state.meta.branch_name, "actor_id": actor_id,
        "anchor_message_id": anchor.id if anchor else (visible[-1].id if visible else None),
        "mode": "evidence_only", "messages": [{"id": m.id, "seq": m.seq, "turn": m.turn,
            "actor_id": m.actor, "actor_name": labels.get(message_person(state, m), "旁白" if m.actor == "director" else "玩家"),
            "content": m.content, "kind": m.kind, "message_ids": [m.id]} for m in displayed[-limit:]],
        "memories": memories[-limit:], "unfinished": [r for r in memories
            if r["category"] == "unfinished" and r["matter_status"] == "open"][-limit:],
        "events": events[-limit:], "visible_message_count": len(displayed), "truncated": len(displayed) > limit,
        "notes": ["仅展示本路线、该人物可见且来源仍有效的记录；不会补写未记录的剧情。"]}


def compare_branches(left, right, memory_store, *, actor_id="player", limit=100):
    if left.meta.story_id != right.meta.story_id:
        raise ValueError("只能比较同一故事的路线")
    a, b = sorted(left.messages, key=lambda m: m.seq), sorted(right.messages, key=lambda m: m.seq)
    common = []
    for x, y in zip(a, b):
        if x.id != y.id or x.fingerprint != y.fingerprint:
            break
        common.append((x, y))
    cutoff_a = common[-1][0].seq if common else -1
    cutoff_b = common[-1][1].seq if common else -1
    left_visible = {m.id for m in left.visible_messages_for(actor_id) if m.status == "final"}
    right_visible = {m.id for m in right.visible_messages_for(actor_id) if m.status == "final"}
    visible_common = [(x, y) for x, y in common if x.id in left_visible and y.id in right_visible]
    anchor = visible_common[-1][0] if visible_common else None
    return {"mode": "evidence_only", "common_anchor": None if anchor is None else {
        "message_id": anchor.id, "content": anchor.content, "turn": anchor.turn},
        "left": review_branch(left, memory_store, actor_id=actor_id, after_seq=cutoff_a, limit=limit),
        "right": review_branch(right, memory_store, actor_id=actor_id, after_seq=cutoff_b, limit=limit),
        "notes": ["两侧分别从各自有效证据派生，互不注入；共同锚点按消息身份及内容指纹确定。"]}
