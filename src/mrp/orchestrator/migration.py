"""R35 schema v1→v2 迁移：scenes / active_scene_id / message.scene_id。

v2 语义：每条消息必有场景归属；会话恒有 active scene。
迁移是 dict 级纯函数（便于备份原文与单测）；幂等（版本号判定）。
迁移前备份由调用方（server/app.py）负责：`<file>.json.v1.bak` 永不覆盖第一份。
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone


def migrate_state_dict(raw: dict) -> tuple[dict, bool]:
    """v1 SessionState dict → v2。已 v2 原样返回 (raw, False)。

    步骤：
    1) 造初始场景（title="开场"，member_ids=全部角色，全回合范围）
    2) scenes / active_scene_id 落位
    3) 每条无 scene_id 的消息回填初始场景 id（统一"每条消息必有场景"不变式）
    4) schema_version=2
    """
    if raw.get("schema_version", 1) >= 2:
        return raw, False

    out = copy.deepcopy(raw)
    characters = out.get("characters") or []
    member_ids = [c.get("id") for c in characters if c.get("id")]

    scene = {
        "id": _scene_id(),
        "title": "开场",
        "description": "",
        "turn_start": 0,
        "turn_end": None,
        "member_ids": member_ids,
    }
    out["scenes"] = [scene]
    out["active_scene_id"] = scene["id"]
    for msg in out.get("messages") or []:
        if not msg.get("scene_id"):
            msg["scene_id"] = scene["id"]
    out["schema_version"] = 2
    return out, True


def migrate_save_dict(raw: dict) -> tuple[dict, bool]:
    """SaveFile 迁移：对 raw['state'] 做同样处理，顶层版本号同步。"""
    state = raw.get("state")
    if not isinstance(state, dict):
        return raw, False
    migrated_state, changed = migrate_state_dict(state)
    if not changed:
        return raw, False
    out = dict(raw)
    out["state"] = migrated_state
    out["schema_version"] = 2
    return out, True


from mrp.shared.session_policy import LEGACY_SNAPSHOT_META_FIELDS

_SNAPSHOT_META_FIELDS = LEGACY_SNAPSHOT_META_FIELDS


def migrate_state_to_v3_dict(raw: dict) -> tuple[dict, bool]:
    """Chain v1→v2→v3 without inventing state for old historical messages.

    Only the persisted head receives a legacy state revision. Its memory watermark
    stays unknown until the branch-aware memory store is installed in M17.
    """
    v2, changed_v2 = migrate_state_dict(raw)
    if v2.get("schema_version", 1) >= 3:
        return v2, changed_v2

    out = copy.deepcopy(v2)
    meta = out.get("meta")
    if not isinstance(meta, dict) or not meta.get("id"):
        raise ValueError("会话缺少有效的 meta.id，无法迁移世界线身份")
    branch_id = str(meta["id"])
    meta.setdefault("story_id", branch_id)
    meta.setdefault("branch_name", meta.get("title", ""))
    meta.setdefault("parent_branch_id", None)
    meta.setdefault("fork_message_id", None)
    meta.setdefault("fork_state_revision_id", None)
    meta.setdefault("branch_revision", 0)
    meta.setdefault("archived", False)

    messages = out.get("messages") or []
    last_final = next(
        (message for message in reversed(messages)
         if isinstance(message, dict) and message.get("id")
         and message.get("status", "final") == "final"),
        None,
    )
    anchor_id = str(last_final["id"]) if last_final else None
    revision_key = f"{branch_id}:{anchor_id or 'empty'}:v3-head"
    revision_id = "revision-" + hashlib.sha256(revision_key.encode("utf-8")).hexdigest()[:12]
    snapshot = {
        "meta": {key: copy.deepcopy(meta[key]) for key in _SNAPSHOT_META_FIELDS if key in meta},
        "characters": copy.deepcopy(out.get("characters") or []),
        "lorebooks": copy.deepcopy(out.get("lorebooks") or []),
        "pinned_facts": copy.deepcopy(out.get("pinned_facts") or []),
        "scenes": copy.deepcopy(out.get("scenes") or []),
        "active_scene_id": out.get("active_scene_id"),
    }
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    out["state_revisions"] = [{
        "id": revision_id,
        "after_message_id": anchor_id,
        "snapshot": snapshot,
        "content_hash": hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
        "memory_watermark": None,
        "source": "legacy_head",
        "created_at": (
            (last_final.get("created_at") if last_final else None)
            or meta.get("created_at")
            or datetime.now(timezone.utc).isoformat()
        ),
    }]
    out["head_state_revision_id"] = revision_id
    out["story_events"] = []
    if last_final is not None:
        last_final["post_state_revision_id"] = revision_id
    out["schema_version"] = 3
    return out, True


def migrate_save_to_v3_dict(raw: dict) -> tuple[dict, bool]:
    """Upgrade an entire save, preserving its original content for repo backup."""
    state = raw.get("state")
    if not isinstance(state, dict):
        return raw, False
    migrated_state, changed = migrate_state_to_v3_dict(state)
    if not changed and raw.get("schema_version") == 3:
        return raw, False
    out = dict(raw)
    out["state"] = migrated_state
    out["schema_version"] = 3
    return out, True


def _scene_id() -> str:
    import uuid

    return f"scene-{uuid.uuid4().hex[:12]}"
