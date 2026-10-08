"""Compare recorded inputs with a current preview, without explaining model thoughts."""
from __future__ import annotations

import hashlib

from mrp.orchestrator.important_memory import visible_memory_messages

def source_materials(ctx, composed) -> list[dict]:
    materials = [{"entry_id": inj.entry_id, "source": inj.source, "content": inj.content}
                 for inj in ctx.injections]
    if ctx.world_core_brief.strip() and ctx.world_runtime_policy != 'raw':
        materials.insert(0, {"entry_id": "world_core", "source": "world", "content": ctx.world_core_brief})
    persona = getattr(ctx, "_planned_persona", None)
    if persona is not None:
        materials.insert(0, {"entry_id": "character_persona", "source": "character", "content": persona})
    return materials


def _source_rows(inspection, materials):
    contents = {(item["source"], item["entry_id"]): item["content"] for item in materials}
    rows = {}
    for source in inspection["sources"]:
        row = dict(source)
        row["content"] = contents.get((row["source"], row["entry_id"]), "")
        row["content_sha256"] = hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
        rows[(row["source"], row["entry_id"])] = row
    persona = contents.get(("character", "character_persona"))
    if persona is not None:
        rows[("character", "character_persona")] = {
            "entry_id": "character_persona", "source": "character", "included": True,
            "content": persona, "content_sha256": hashlib.sha256(persona.encode("utf-8")).hexdigest(),
            "reason": "本次请求的角色与回复基础设定", "section": "persona"}
    return rows


def _correction_target(source, entry_id, state, character_id):
    base = {"branch_id": state.meta.id}
    character = state.character(character_id)
    if source == "character" and character is not None:
        return {**base, "type": "character", "character_id": character_id,
                "source_character_id": character.source_asset_id or character.id,
                "scope": "library_then_explicit_branch_adoption"}, True
    if source == "memory":
        return {**base, "type": "memory", "character_id": character_id, "memory_id": entry_id,
                "scope": "current_branch"}, True
    if source == "lorebook":
        for book in state.lorebooks:
            entry = next((e for e in book.entries if f"{book.id}:{e.uid}" == entry_id), None)
            if entry is not None:
                return {**base, "type": "lorebook", "lorebook_id": book.id, "entry_uid": entry.uid,
                        "scope": "library_then_explicit_branch_adoption"}, True
    if source in {"world", "archive"}:
        return {**base, "type": "world", "world_id": state.meta.source_world_id,
                "entry_id": entry_id, "scope": "library_then_explicit_branch_adoption"}, bool(state.meta.source_world_id)
    if entry_id.startswith("preset:"):
        parts = entry_id.split(":", 2)
        if len(parts) == 3:
            return {**base, "type": "prompt_preset", "preset_id": parts[1], "segment_id": parts[2]}, True
    if entry_id == "pinned_facts":
        return {**base, "type": "pinned_facts", "scope": "current_branch"}, True
    if entry_id in {"scenario_instructions", "scene_card", "break_armor_prompt"}:
        return {**base, "type": "story_settings", "entry_id": entry_id}, True
    return {**base, "type": "input_snapshot", "entry_id": entry_id}, False


def compare_context(actual, actual_materials, preview, state, character_id):
    """All actual text comes from the exact recorded generation; never reconstruct it."""
    old = _source_rows(actual, actual_materials)
    new = _source_rows(preview, preview.get("source_materials", []))
    sources, corrections = [], []
    compared_fields = ("content_sha256", "included", "anchor", "order", "section", "excluded_reason")
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key), new.get(key)
        changed_fields = [field for field in compared_fields
                          if (before or {}).get(field) != (after or {}).get(field)]
        status = "added" if before is None else "removed" if after is None else "changed" if changed_fields else "unchanged"
        sources.append({"source": key[0], "entry_id": key[1], "status": status,
                        "changed_fields": changed_fields, "actual": before, "preview": after})
        # Correction material is the historical source, including omissions; it is
        # never a speculative cause for the generated answer.
        if before is not None:
            target, editable = _correction_target(*key, state, character_id)
            corrections.append({"source": key[0], "entry_id": key[1], "actual_content": before["content"],
                                "included_in_actual": bool(before.get("included")),
                                "current_content": after["content"] if after is not None else None,
                                "target": target, "editable": editable,
                                "note": "修改后需重新预览；已生成回复和其他路线保持原记录。"})
    old_sections = {x["kind"]: x for x in actual["sections"]}
    new_sections = {x["kind"]: x for x in preview["sections"]}
    old_persona = (old.get(("character", "character_persona")) or {}).get("content")
    new_persona = (new.get(("character", "character_persona")) or {}).get("content")
    if old_persona is not None:
        old_sections["persona"] = {"kind": "persona", "title": "角色基础设定", "content": old_persona}
    if new_persona is not None:
        new_sections["persona"] = {"kind": "persona", "title": "角色基础设定", "content": new_persona}
    sections = [{"kind": key, "actual": old_sections.get(key), "preview": new_sections.get(key)}
                for key in sorted(old_sections.keys() | new_sections.keys())
                if (old_sections.get(key) or {}).get("content") != (new_sections.get(key) or {}).get("content")]
    return {"mode": "actual_vs_next_preview", "actual": actual, "preview": preview,
            "sources": sources, "changed_sections": sections, "corrections": corrections,
            "input_same": actual["prompt"] == preview["prompt"] and old_persona == new_persona,
            "notes": ["左侧是该候选留档的实际输入，右侧是同一角色在当前路线的下一轮预览。",
                      "两侧对话时点可能不同；差异不能证明回复变化由某一设定导致，也不代表模型思考过程。",
                      "历史输入缺失时不重建或冒充原请求。资料库修改需显式采用到当前路线。"]}


def correction_view(actual, materials, state, character_id):
    """Actual-source navigation also works for groups, which have no character preview."""
    rows = _source_rows(actual, materials)
    corrections = []
    for (source, entry_id), row in rows.items():
        target, editable = _correction_target(source, entry_id, state, character_id)
        corrections.append({"source": source, "entry_id": entry_id, "actual_content": row["content"],
            "included_in_actual": bool(row.get("included")), "current_content": None,
            "target": target, "editable": editable,
            "note": "仅提供原始输入与编辑入口；修改不会自动恢复过期记忆或重写回复。"})
    return {"mode": "actual_materials", "actual": actual, "corrections": corrections,
            "notes": ["仅展示该候选留档的输入材料；不推断生成原因或模型思考过程。"]}


def enrich_memory_materials(result, record, state, memory_store, character_id):
    """Read-only revision/evidence links and stale records from this owner and branch."""
    records = memory_store.records_for(character_id, session_id=state.meta.id)
    current_records = {item.id: item for item in records}
    audit = {item["memory_id"]: item for item in record.get("memory_recall", []) if item.get("memory_id")}
    ctx = record["ctx"]
    refs = {item.message_id: item for item in ctx.provenance.sources} if ctx.provenance else {}
    source_ids = set(refs)
    for row in audit.values():
        source_ids.update(row.get("source_message_ids", []))
    old_state = getattr(ctx, "_generation_state", None)
    old_messages = {m.id: m for m in (old_state.messages if old_state is not None else ctx.visible_messages)}
    current_messages = {m.id: m for m in visible_memory_messages(state, character_id)}
    evidence = []
    for identity in sorted(source_ids):
        old, current = old_messages.get(identity), current_messages.get(identity)
        ref = refs.get(identity)
        # A metadata reference cannot make a missing original body magically available.
        if ref is None or (old is not None and old.fingerprint != ref.fingerprint):
            old = None
        evidence.append({"message_id": identity,
            "actual": old.model_dump(mode="json") if old is not None else None,
            "current": current.model_dump(mode="json") if current is not None else None,
            "changed": bool(old is not None and current is not None and old.fingerprint != current.fingerprint),
            "currently_visible": current is not None})
    for correction in result["corrections"]:
        if correction["source"] != "memory":
            continue
        identity = correction["entry_id"]
        current = current_records.get(identity)
        actual_ids = audit.get(identity, {}).get("source_message_ids", [])
        correction["memory"] = {"id": identity, "actual_source_message_ids": actual_ids,
            "actual_sources_retained": identity in audit,
            "current_revision": current.revision if current else None,
            "current_source_message_ids": current.source_message_ids if current else [],
            "source_changed": current.source_changed if current else None,
            "invalidated": current.invalidated if current else None,
            "exists": current is not None}
        correction["editable"] = current is not None
    superseded = {identity for item in records if not item.invalidated and not item.source_changed
                  for identity in item.supersedes}
    stale = []
    for item in records:
        if not source_ids.intersection(item.source_message_ids):
            continue
        reasons = []
        if item.invalidated:
            reasons.append("已失效")
        if item.source_changed:
            reasons.append("来源已改变")
        if item.id in superseded:
            reasons.append("已被后续事项取代")
        if any(identity not in current_messages or current_messages[identity].fingerprint != fingerprint
               for identity, fingerprint in item.source_fingerprints.items()):
            reasons.append("来源不可见或指纹改变")
        if not reasons:
            continue
        readable = all(identity in current_messages for identity in item.source_message_ids)
        stale.append({"id": item.id, "character_id": character_id, "revision": item.revision,
            "source_message_ids": item.source_message_ids, "source_changed": item.source_changed,
            "invalidated": item.invalidated, "category": item.category, "matter_status": item.matter_status,
            "content": item.content if readable else None, "reasons": reasons,
            "content_visible": readable})
    result["branch_revision"] = state.meta.branch_revision
    result["evidence_messages"] = evidence
    result["related_stale_memories"] = stale
    result["notes"].append("相关过期记忆只列出本路线、该人物的记录；不会自动恢复或重新整理。")
    return result
