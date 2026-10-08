"""Conservative single-character ST JSONL migration. No generated history or memory."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any

from mrp.shared.models import Message, MessageVariant, PlayerIdentity, Scene, SessionMeta, SessionState

MAX_BYTES = 20 * 1024 * 1024
MAX_MESSAGES = 20_000
DEGRADED = ["历史场景与状态修订", "历史模型请求及费用", "外部摘要和记忆", "世界书时效与脚本状态", "不可识别的旧时间与候选生成时间"]


class StImportConflict(RuntimeError):
    pass


def parse_st_chat(raw: bytes) -> tuple[dict, list[dict], dict]:
    if len(raw) > MAX_BYTES:
        raise ValueError("聊天文件超过 20 MB")
    try:
        text = raw.decode("utf-8-sig")
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("聊天文件必须是 UTF-8 的 ST JSONL，每行一个 JSON 对象") from exc
    if not rows or not isinstance(rows[0], dict):
        raise ValueError("缺少 ST 聊天头")
    header, entries = rows[0], rows[1:]
    if not any(key in header for key in ("user_name", "character_name", "chat_metadata")) or "mes" in header:
        raise ValueError("无法识别 ST 单人聊天头")
    if not entries or len(entries) > MAX_MESSAGES:
        raise ValueError("聊天为空或超过 20000 条消息")
    names, users = set(), set()
    variants = 0
    for row in entries:
        if not isinstance(row, dict) or not isinstance(row.get("mes"), str):
            raise ValueError("消息缺少文本 mes；本版本只迁入文本单人聊天")
        if len(row["mes"]) > 500_000 or not isinstance(row.get("is_user"), bool):
            raise ValueError("消息过长或缺少可靠的 is_user 身份标记")
        if not row.get("is_system"):
            name = str(row.get("name") or "").strip()
            (users if row["is_user"] else names).add(name)
        swipes = row.get("swipes", [])
        if not isinstance(swipes, list) or len(swipes) > 100 or any(not isinstance(x, str) or len(x) > 500_000 for x in swipes):
            raise ValueError("候选消息格式无效或超过限制")
        variants += len(swipes)
    if len(names) != 1 or len(users) > 1 or "" in names:
        raise ValueError("只支持一个 AI 人物、一个玩家身份的聊天；群聊或身份切换需要单独迁移")
    warnings = ["仅迁入原始文本、人物映射及候选。不会推测旧场景、历史状态、摘要、记忆或请求费用。",
                "历史消息没有可验证的状态检查点，不能从旧消息创建可靠历史分支；可从迁入后的新消息分支。"]
    if any(row.get("is_system") for row in entries):
        warnings.append("系统消息仅保留为玩家可见备注，不作为角色已知经历。")
    if any(row.get("swipes") for row in entries):
        warnings.append("保留候选文本和当前选中内容；候选生成参数与模型费用不迁入。")
    unverified_time_count = sum(_date(row.get("send_date")) is None for row in entries)
    if unverified_time_count:
        warnings.append(f"{unverified_time_count} 条消息的原时间无法可靠识别，显示迁入时间；候选时间也不视为原生成时间。")
    preview = {"source_sha256": hashlib.sha256(raw).hexdigest(),
               "user_name": next(iter(users), str(header.get("user_name") or "玩家")),
               "character_names": sorted(names), "message_count": len(entries),
               "variant_count": variants, "unverified_time_count": unverified_time_count,
               "warnings": warnings, "degraded_fields": DEGRADED}
    return header, entries, preview


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo is not None else None
    except ValueError:
        return None


def build_import_state(raw: bytes, character, *, user_name: str, title: str) -> tuple[SessionState, dict]:
    _, entries, preview = parse_st_chat(raw)
    user_name = user_name.strip()
    if not user_name or len(user_name) > 120:
        raise ValueError("请确认玩家身份名称（1–120 字）")
    source_hash = preview["source_sha256"]
    mapping = hashlib.sha256(f"{source_hash}\0{character.id}\0{user_name}".encode()).hexdigest()
    sid = f"sess-st-{mapping[:24]}"
    card = character.model_copy(deep=True)
    card.source_asset_id = card.source_asset_id or card.id
    card.source_asset_revision = card.source_asset_revision or card.revision
    scene = Scene(title="迁入后继续", member_ids=[card.id])
    identity = PlayerIdentity(person_id=f"person-st-{mapping[:16]}", name=user_name,
                              persona=f"姓名：{user_name}", start_seq=0)
    state = SessionState(schema_version=3, meta=SessionMeta(id=sid, title=title.strip() or "迁入聊天",
        character_ids=[card.id], player_identity_id=identity.id, player_persona=identity.persona),
        characters=[card], scenes=[scene], active_scene_id=scene.id, player_identities=[identity])
    turn = 0
    for seq, row in enumerate(entries):
        system = bool(row.get("is_system"))
        if row["is_user"] and not system:
            turn += 1
        actor = "director" if system else ("player" if row["is_user"] else card.id)
        content = row["mes"]
        choices = list(row.get("swipes") or [])
        if choices and content not in choices:
            choices.append(content)
        selected = row.get("swipe_id")
        if not isinstance(selected, int) or not 0 <= selected < len(choices) or choices[selected] != content:
            selected = choices.index(content) if choices else None
        message = Message(id=f"msg-st-{mapping[:12]}-{seq}", session_id=sid, seq=seq, turn=turn,
            actor=actor, content=content, kind="ooc" if system else "roleplay",
            visible_to=["player"] if system else "all", known_to=[] if system else [card.id, identity.person_id],
            player_identity_id=identity.id, person_id=None if system else (identity.person_id if row["is_user"] else card.id),
            scene_id=None, variants=[MessageVariant(id=f"var-st-{mapping[:12]}-{seq}-{i}", content=x) for i, x in enumerate(choices)],
            active_variant=selected)
        stamp = _date(row.get("send_date"))
        if stamp:
            message.created_at = stamp
        state.messages.append(message)
    state.generation_operations["import:st"] = {"source_sha256": source_hash, "mapped_character_id": card.id,
        "mapped_user_name": user_name, "degraded_fields": DEGRADED, "warnings": preview["warnings"]}
    return state, preview


async def import_st_chat(container, raw: bytes, *, source_sha256: str, character_id: str,
                         user_name: str, title: str, acknowledge_degraded: bool) -> dict:
    if not acknowledge_degraded:
        raise ValueError("请先确认身份映射和历史状态无法可靠迁入的说明")
    if hashlib.sha256(raw).hexdigest() != source_sha256:
        raise ValueError("文件已变化，请重新预览")
    character = container.characters.get(character_id)
    if character is None:
        raise ValueError("映射角色不存在")
    state, preview = build_import_state(raw, character, user_name=user_name, title=title)
    if not hasattr(container, "_st_import_lock"):
        container._st_import_lock = asyncio.Lock()
    async with container._st_import_lock:
        existing = await container.sessions.load_state_readonly(state.meta.id)
        if existing is not None:
            await container.sessions.adopt_creation_branch(existing)
            return {"story_id": existing.meta.story_id, "branch_id": existing.meta.id,
                    "branch_revision":existing.meta.branch_revision, "repeated": True, "warnings": preview["warnings"]}
        if container.sessions.path_for(state.meta.id).exists():
            raise StImportConflict("该聊天的迁入目标已存在但无法可靠读取，已保留原故事；请先检查或恢复备份")
        state.lorebooks = [container.lorebooks[x].model_copy(deep=True)
                          for x in character.bound_lorebook_ids if x in container.lorebooks]
        state.meta.lorebook_ids = [x.id for x in state.lorebooks]
        # Persist a head only. Historical messages intentionally have no state revision.
        try:
            await container.sessions.create_state_exclusive(state, 0)
        except FileExistsError as exc:
            existing = await container.sessions.load_state_readonly(state.meta.id)
            if existing is None:
                raise StImportConflict("迁入目标已被其他操作创建且无法可靠读取，已保留原文件，请重新检查") from exc
            await container.sessions.adopt_creation_branch(existing)
            return {"story_id": existing.meta.story_id, "branch_id": existing.meta.id,
                    "repeated": True, "warnings": preview["warnings"]}
        if not container.sessions.creation_active():
            container.story_search.schedule(state)
            container.story_backups.schedule(state.meta.story_id)
        return {"story_id": state.meta.story_id, "branch_id": state.meta.id,
                "branch_revision":state.meta.branch_revision,
                "repeated": False, "warnings": preview["warnings"]}
