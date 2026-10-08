"""Explicit, selected static-library changes applied to a single branch snapshot."""
from __future__ import annotations

import hashlib
import json

from mrp.orchestrator.message_regeneration import restore_state_in_place

CARD_FIELDS = ("name", "description", "appearance", "traits_label", "traits", "personality", "scenario",
               "mes_example", "system_prompt", "post_history_instructions", "creator_notes", "tags")
BOOK_FIELDS = ("name", "description", "entries", "scan_depth", "token_budget", "recursive_scanning")
FIELD_LABELS = {"name": "名称", "description": "描述", "appearance": "外貌", "traits_label": "特征名称",
    "traits": "特征", "personality": "性格", "scenario": "背景情境", "mes_example": "对话示例",
    "system_prompt": "角色指令", "post_history_instructions": "回复补充指令", "creator_notes": "创作者说明",
    "tags": "标签", "entries": "世界书条目", "scan_depth": "扫描范围", "token_budget": "内容预算",
    "recursive_scanning": "递归扫描", "core_brief": "世界核心", "archive_records": "世界档案", "runtime_policy": "世界资料使用方式"}


def asset_updates(container, state):
    changes, targets = [], {}
    def add(key, label, before, after, source_id, apply):
        if before != after:
            changes.append({"key": key, "label": label, "before": before, "after": after, "source_id": source_id})
            targets[key] = apply
    for character in state.characters:
        source_id = character.source_asset_id or character.id
        source = container.characters.get(source_id)
        if source is None:
            continue
        for field in CARD_FIELDS:
            add(f"character:{character.id}:{field}", f"{character.card.name} · {FIELD_LABELS[field]}",
                character.card.model_dump(mode="json").get(field), source.card.model_dump(mode="json").get(field), source_id,
                ("character", character.id, field, source.card.model_copy(deep=True), source.revision))
    for book in state.lorebooks:
        source = container.lorebooks.get(book.id)
        if source is None:
            continue
        old, new = book.model_dump(mode="json"), source.model_dump(mode="json")
        for field in BOOK_FIELDS:
            add(f"lorebook:{book.id}:{field}", f"{book.name} · {FIELD_LABELS[field]}", old[field], new[field], source.id,
                ("lorebook", book.id, field, source.model_copy(deep=True), source.revision))
    world = container.worlds.get(state.meta.source_world_id or "")
    if world is not None:
        for field, target, new in (
            ('core_brief', 'world_core_brief', world.core_brief),
            ('archive_records', 'world_archive_records', [r.model_dump(mode='json') for r in world.archive_records]),
            ('runtime_policy', 'world_runtime_policy', world.runtime_policy),
        ):
            add(f"world:{world.id}:{field}", f"{world.title} · {FIELD_LABELS[field]}", getattr(state.meta, target), new, world.id,
                ("world", world.id, target, new, world.revision))
    digest = hashlib.sha256(json.dumps(changes, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"branch_id": state.meta.id, "branch_revision": state.meta.branch_revision,
            "source_digest": digest, "changes": changes,
            "notes": ["仅采用选中的静态设定；不更换人物身份、不改写历史、不修改其他路线。"]}, targets


async def apply_asset_updates(container, runner, *, expected_revision, source_digest, selected_fields, defer_commit=False):
    if not selected_fields or len(set(selected_fields)) != len(selected_fields):
        raise ValueError("请选择不重复的设定字段")
    if runner.busy() and not defer_commit:
        raise RuntimeError("路线正在生成，请稍后查看差异")
    async with runner.runtime.turn_lock:
        if (runner.runtime.active_conversation_run_id or runner.runtime.active_turn_run_id
                or runner.state.meta.branch_revision != expected_revision):
            raise RuntimeError("路线正在生成或已改变，请重新查看差异")
        report, targets = asset_updates(container, runner.state)
        if report["source_digest"] != source_digest or any(key not in targets for key in selected_fields):
            raise RuntimeError("资料库已改变，请重新查看差异")
        # Capture a recoverable story before applying changes. Errors abort before mutation.
        await container.story_backups.snapshot(runner.state.meta.story_id, reason="before_asset_update")
        before = runner.state.model_copy(deep=True)
        try:
            for key in selected_fields:
                kind, identity, field, source, revision = targets[key]
                if kind == "character":
                    target = runner.state.character(identity)
                    setattr(target.card, field, getattr(source, field))
                    target.source_asset_id = target.source_asset_id or identity
                    # Partial adoption must not claim the complete source revision was adopted.
                elif kind == "lorebook":
                    target = next(x for x in runner.state.lorebooks if x.id == identity)
                    setattr(target, field, getattr(source, field))
                else:
                    setattr(runner.state.meta, field, source)
            runner.lorebooks = runner.state.lorebooks
            if not defer_commit:
                await container.persist_session(runner)
        except BaseException:
            restore_state_in_place(runner.state, before)
            runner.lorebooks = runner.state.lorebooks
            if not defer_commit:
                await container.sessions.restore_state(runner.state)
            raise
        return {"branch_id": runner.state.meta.id, "branch_revision": runner.state.meta.branch_revision,
                "applied_fields": selected_fields, "changes": (asset_updates(container, runner.state)[0])["changes"]}
