"""Explicit adoption of verified corrections at an existing node's old boundary."""
from __future__ import annotations

import asyncio
import hashlib
import json

from pydantic import Field

from mrp.orchestrator.asset_updates import CARD_FIELDS, BOOK_FIELDS, FIELD_LABELS
from mrp.orchestrator.generation_sources import generation_baseline
from mrp.orchestrator.important_memory import visible_memory_messages
from mrp.orchestrator.message_regeneration import RegenerationConflict, RegenerationRequest
from mrp.orchestrator.worldline_state import current_state_projection


class CorrectedRegenerationRequest(RegenerationRequest):
    options_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    selected_static_fields: list[str] = Field(default_factory=list, max_length=1000)
    selected_memory_revisions: dict[str, int] = Field(default_factory=dict, max_length=1000)


def correction_options(runner, target, memory_store):
    runner.regeneration._validate_node(target)
    snap, provenance = generation_baseline(runner.state, target)
    if getattr(snap, "_generation_policy_injections", None) is None:
        record = runner.runtime.inspections.generation(target.id, target.generation_id)
        if record is None:
            raise RegenerationConflict("该候选缺少原始全局指令材料，无法可靠限定纠错范围")
        policies = [inj.model_dump(mode="json") for inj in record["ctx"].injections
                    if inj.entry_id == "break_armor_prompt" or inj.entry_id.startswith("response_style:")]
        object.__setattr__(snap, "_generation_policy_injections", policies)
    state = runner.state
    changes, targets = [], {}
    def add(key, label, before, after, target):
        if before != after:
            changes.append({"key": key, "label": label, "before": before, "after": after})
            targets[key] = target
    for old in snap.characters:
        current = state.character(old.id)
        if current is not None:
            old_card, new_card = old.card.model_dump(mode="json"), current.card.model_dump(mode="json")
            for field in CARD_FIELDS:
                add(f"character:{old.id}:{field}", f"{old.card.name} · {FIELD_LABELS[field]}", old_card[field], new_card[field],
                    ("character", old.id, field, current.card.model_copy(deep=True)))
    current_books = {book.id: book for book in state.lorebooks}
    for old in snap.lorebooks:
        current = current_books.get(old.id)
        if current is not None:
            old_book, new_book = old.model_dump(mode="json"), current.model_dump(mode="json")
            for field in BOOK_FIELDS:
                add(f"lorebook:{old.id}:{field}", f"{old.name} · {FIELD_LABELS[field]}", old_book[field], new_book[field],
                    ("lorebook", old.id, field, current.model_copy(deep=True)))
    for field, label in (("world_core_brief", "世界核心"), ("world_archive_records", "世界档案"), ('world_runtime_policy', '世界资料使用方式')):
        add(f"meta:{field}", label, getattr(snap.meta, field), getattr(state.meta, field),
            ("meta", "", field, getattr(state.meta, field)))
    preset = runner.context_builder.active_prompt_preset()
    new_preset = preset.model_dump(mode="json") if preset is not None else None
    add("meta:prompt_preset_snapshot", "提示词方案", snap.meta.prompt_preset_snapshot, new_preset,
        ("meta", "", "prompt_preset_snapshot", new_preset))
    actual_ids = set(target.generation_meta.injected_entry_ids if target.generation_meta else [])
    visible = {m.id: m for m in visible_memory_messages(snap, target.actor)}
    memories, allowed_memories = [], {}
    for record in memory_store.records_for(target.actor, session_id=state.meta.id):
        if record.id not in actual_ids:
            continue
        reason = None
        if record.invalidated or record.source_changed:
            reason = "记忆已失效或来源已改变，请先核对来源并修订"
        elif not record.source_message_ids or not all(mid in visible for mid in record.source_message_ids):
            reason = "来源不完全属于该回应的原历史边界"
        elif not all(mid in record.source_fingerprints for mid in record.source_message_ids):
            reason = "缺少完整来源指纹，请先核对来源并修订记忆"
        elif any(mid not in visible or visible[mid].fingerprint != fp for mid, fp in record.source_fingerprints.items()):
            reason = "来源指纹已改变，请先核对并修订记忆"
        elif record.effective_message_id and record.effective_message_id not in visible:
            reason = "生效锚点晚于该回应的原历史边界"
        if reason is None:
            allowed_memories[record.id] = record.model_copy(deep=True)
        readable = all(mid in visible for mid in record.source_message_ids)
        memories.append({"id": record.id, "revision": record.revision,
            "content": record.content if readable else None, "source_message_ids": record.source_message_ids,
            "source_changed": record.source_changed, "invalidated": record.invalidated,
            "eligible": reason is None, "reason": reason})
    payload = {"branch_id": state.meta.id, "message_id": target.id, "generation_id": target.generation_id,
        "expected_branch_revision": state.meta.branch_revision, "expected_fingerprint": target.fingerprint,
        "expected_player_identity_id": state.meta.player_identity_id, "static_changes": changes, "memories": memories}
    payload["options_digest"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                                         separators=(",", ":")).encode()).hexdigest()
    payload["notes"] = ["仅重新生成当前控制阶段、当前场景最新交互段的 NPC 回应，保留旧候选。",
        "静态差异来自该回应原始材料与当前路线已采用的设定；不会自动采用资料库的其他变更。",
        "只允许确认原请求使用过的记忆修订，所有来源必须在原历史边界内；不引入后续消息或旁支。"]
    return payload, targets, allowed_memories


def correction_transform(runner, req, memory_store):
    async def transform(snap, provenance, target):
        if not req.selected_static_fields and not req.selected_memory_revisions:
            raise RegenerationConflict("请至少选择一项已核对的纠错材料")
        if len(set(req.selected_static_fields)) != len(req.selected_static_fields):
            raise RegenerationConflict("纠错字段不能重复")
        report, fields, memories = await asyncio.to_thread(correction_options, runner, target, memory_store)
        if report["options_digest"] != req.options_digest:
            raise RegenerationConflict("纠错材料或记忆修订已改变，请重新核对")
        if any(key not in fields for key in req.selected_static_fields):
            raise RegenerationConflict("选中的静态字段不属于已核对差异")
        if any(identity not in memories or memories[identity].revision != revision
               for identity, revision in req.selected_memory_revisions.items()):
            raise RegenerationConflict("所选记忆修订已变化、已失效或包含后续来源")
        for key in req.selected_static_fields:
            kind, identity, field, source = fields[key]
            if kind == "character":
                setattr(snap.character(identity).card, field, getattr(source, field))
            elif kind == "lorebook":
                book = next(b for b in snap.lorebooks if b.id == identity)
                setattr(book, field, getattr(source, field))
            else:
                setattr(snap.meta, field, source)
        overrides = dict(getattr(snap, "_generation_memory_overrides", {}))
        overrides.update({identity: memories[identity] for identity in req.selected_memory_revisions})
        object.__setattr__(snap, "_generation_memory_overrides", overrides)
        origin = {"operation_id": req.operation_id, "static_fields": req.selected_static_fields,
                  "memory_revisions": req.selected_memory_revisions}
        object.__setattr__(snap, "_correction_origin", origin)
        if getattr(snap, "_generation_policy_injections", None) is None:
            record = runner.runtime.inspections.generation(target.id, target.generation_id)
            if record is None:
                raise RegenerationConflict("原始全局指令材料未保留，不能可靠纠错")
            policies = [inj.model_dump(mode="json") for inj in record["ctx"].injections
                        if inj.entry_id == "break_armor_prompt" or inj.entry_id.startswith("response_style:")]
            object.__setattr__(snap, "_generation_policy_injections", policies)
        provenance.baseline_state = current_state_projection(snap)
        provenance.baseline_state["_corrected_memory_records"] = [r.model_dump(mode="json") for r in overrides.values()]
        provenance.baseline_state["_correction_origin"] = origin
        provenance.baseline_state["_generation_policy_injections"] = snap._generation_policy_injections
        return snap, provenance
    return transform
