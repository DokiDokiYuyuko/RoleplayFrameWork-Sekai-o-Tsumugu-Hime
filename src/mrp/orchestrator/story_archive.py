"""Portable story archive: branch files, immutable saves, optional scoped memory.

Only validated JSON members are read; archives are never extracted to disk.
Settings and model credentials are deliberately outside this format.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from collections import defaultdict
from typing import Any
from pathlib import Path

from mrp.orchestrator.worldline_state import projection_hash
from mrp.shared.models import MemoryRecord, SaveFile, SessionState, fingerprint, new_id, utcnow
from mrp.storage.atomic import write_json_atomic
from mrp.storage.command_receipts import inherit_receipt_audit
from mrp.storage.creation_operations import export_receipts


class StoryArchiveError(ValueError):
    pass


_SECRET_KEYS = {"api_key", "apikey", "access_token", "refresh_token", "authorization", "password", "secret"}
_TOKEN_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_MAX_TOTAL = 200 * 1024 * 1024
_MAX_FILE = 40 * 1024 * 1024


def _media_refs(value):
    if isinstance(value, dict):
        refs = {value["avatar_ref"]} if isinstance(value.get("avatar_ref"), str) else set()
        for item in value.values():
            refs.update(_media_refs(item))
        return refs
    if isinstance(value, list):
        return set().union(*(_media_refs(x) for x in value)) if value else set()
    return set()


def _clear_local_avatars(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "avatar_path":
                value[key] = None
            else:
                _clear_local_avatars(item)
    elif isinstance(value, list):
        for item in value:
            _clear_local_avatars(item)


def _scrub(value: Any, removed: list[int]) -> Any:
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                removed[0] += 1
            else:
                clean[key] = _scrub(item, removed)
        return clean
    if isinstance(value, list):
        return [_scrub(item, removed) for item in value]
    if isinstance(value, str):
        clean, count = _TOKEN_PATTERN.subn("[已移除密钥]", value)
        removed[0] += count
        return clean
    return value


def _sanitize_state(state: SessionState, removed: list[int]) -> dict[str, Any]:
    data = _scrub(state.model_dump(mode="json"), removed)
    _clear_local_avatars(data)
    # Machine-local avatars are references, not portable assets. Do not expose
    # absolute paths in a shared archive or automatic backup.
    for character in data.get("characters", []):
        if character.get("card", {}).get("avatar_path"):
            character["card"]["avatar_path"] = None
    for message in data.get("messages", []):
        message["fingerprint"] = fingerprint(message["actor"], message["seq"], message["content"])
    for revision in data.get("state_revisions", []):
        for character in revision.get("snapshot", {}).get("characters", []):
            if character.get("card", {}).get("avatar_path"):
                character["card"]["avatar_path"] = None
        revision["content_hash"] = projection_hash(revision["snapshot"])
    return data


async def _snapshot_token(container, story_id):
    rows = [row for row in await container.sessions.list_summaries() if (row.story_id or row.id) == story_id]
    return (tuple(sorted((row.id, row.branch_revision, row.mtime_ns, row.size,
                          container.memory_store.current_watermark(row.id)) for row in rows)),
            tuple(sorted((row.id, row.mtime_ns, row.size) for row in await container.saves.list()
                         if row.session_id in {item.id for item in rows})))


async def export_story(container: Any, story_id: str, include_memory: bool = True) -> bytes:
    """Optimistic consistent export; never silently package a moving story."""
    for _ in range(3):
        before = await _snapshot_token(container, story_id)
        data = await _export_story_once(container, story_id, include_memory)
        if before == await _snapshot_token(container, story_id):
            return data
    raise StoryArchiveError("故事在导出期间持续变化，请稍后重试")


async def _export_story_once(container: Any, story_id: str, include_memory: bool = True) -> bytes:
    rows = [row for row in await container.sessions.list_summaries()
            if (row.story_id or row.id) == story_id]
    if not rows:
        raise StoryArchiveError("故事不存在")
    rows.sort(key=lambda row: (row.created_at, row.id))
    states: dict[str, SessionState] = {}
    for row in rows:
        state = await container.sessions.load_state_readonly(row.id)
        if state is None:
            raise StoryArchiveError(f"路线无法读取：{row.id}")
        states[row.id] = state
    saves = {}
    for row in await container.saves.list():
        if row.session_id in states:
            save = await container.saves.load(row.id)
            if save is not None:
                saves[row.id] = save
    removed = [0]
    external_assets = sorted({character.card.name for state in states.values()
                              for character in state.characters if character.card.avatar_path})
    buf = io.BytesIO()
    media_refs = set().union(*(_media_refs(s.model_dump(mode="json")) for s in states.values()),
                            *(_media_refs(s.model_dump(mode="json")) for s in saves.values()))
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        from mrp.storage.story_media import media_path
        for ref in sorted(media_refs):
            path = media_path(container.data_root, ref)
            if not path.is_file():
                raise StoryArchiveError("故事头像缺失，无法生成完整备份")
            archive.writestr(f"media/{ref}", path.read_bytes())
        for branch_id, state in states.items():
            archive.writestr(f"branches/{branch_id}.json", json.dumps(
                _sanitize_state(state, removed), ensure_ascii=False, separators=(",", ":")))
        for save_id, save in saves.items():
            payload = _scrub(save.model_dump(mode="json"), removed)
            payload["state"] = _sanitize_state(save.state, removed)
            if not include_memory:
                payload["memory_snapshot"] = []
                payload["memory_refs"] = []
                payload["memory_snapshot_complete"] = False
            archive.writestr(f"saves/{save_id}.json", json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")))
        memory_ids = []
        if include_memory:
            records = []
            for branch_id, state in states.items():
                for character_id in {character.id for character in state.characters} | set(state.player_people):
                    records.extend(container.memory_store.records_for(character_id, session_id=branch_id))
            records.sort(key=lambda record: (record.session_id, record.commit_seq, record.id))
            memory_ids = [record.id for record in records]
            archive.writestr("memories.json", json.dumps(
                _scrub([record.model_dump(mode="json") for record in records], removed),
                ensure_ascii=False, separators=(",", ":")))
            histories = {branch: container.memory_store.export_history(branch) for branch in states}
            archive.writestr('memory_history.json', json.dumps(_scrub(histories, removed), ensure_ascii=False, separators=(',', ':')))
        manifest = {
            "format": "mrp.story", "format_version": 3,
            "exported_at": utcnow().isoformat(), "story_id": story_id,
            "title": states.get(story_id, next(iter(states.values()))).meta.title,
            "branches": list(states), "saves": list(saves),
            "includes_memory": include_memory, "memory_records": len(memory_ids),
            "credential_fields_redacted": removed[0],
            "materials": "角色和世界书版本随每条路线的状态快照保存",
            "external_avatar_characters": external_assets,
            "media": sorted(media_refs),
            "creation_receipts": _scrub(export_receipts(
                container.sessions.story_db, set(states) | set(saves)), removed),
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    return buf.getvalue()


def _read_package(data: bytes) -> tuple[dict[str, Any], dict[str, SessionState], dict[str, SaveFile], list[MemoryRecord], int]:
    if len(data) > _MAX_TOTAL:
        raise StoryArchiveError("故事包超过大小限制")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise StoryArchiveError("不是有效的故事 ZIP") from exc
    removed = [0]
    with archive:
        infos = archive.infolist()
        if len(infos) > 1000 or sum(item.file_size for item in infos) > _MAX_TOTAL:
            raise StoryArchiveError("故事包条目或解压后大小超限")
        if any(item.file_size > _MAX_FILE for item in infos):
            raise StoryArchiveError("故事包中有过大的文件")
        names = [item.filename for item in infos]
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise StoryArchiveError("故事包目录重复或缺少清单")
        try:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "mrp.story" or manifest.get("format_version") not in (1, 2, 3):
                raise StoryArchiveError("故事包格式或版本不支持")
            story_id = manifest["story_id"]
            branch_ids = manifest["branches"]
            save_ids = manifest.get("saves", [])
            if not isinstance(story_id, str) or not isinstance(branch_ids, list) or not branch_ids:
                raise StoryArchiveError("故事清单无效")
            if len(branch_ids) != len(set(branch_ids)) or len(save_ids) != len(set(save_ids)):
                raise StoryArchiveError("故事清单含重复 ID")
            expected = {"manifest.json"} | {f"branches/{bid}.json" for bid in branch_ids} | {f"saves/{sid}.json" for sid in save_ids}
            if manifest.get("includes_memory"):
                expected.add("memories.json")
                if manifest.get('format_version') in (2, 3):
                    expected.add('memory_history.json')
            media = manifest.get("media", [])
            if not isinstance(media, list) or len(media) != len(set(media)):
                raise StoryArchiveError("故事媒体清单无效")
            from mrp.storage.story_media import media_path
            import hashlib
            from pathlib import Path
            for ref in media:
                media_path(Path("."), ref)
                raw_media = archive.read(f"media/{ref}")
                if hashlib.sha256(raw_media).hexdigest() + ".png" != ref:
                    raise StoryArchiveError("故事媒体指纹错误")
            expected.update(f"media/{ref}" for ref in media)
            if set(names) != expected or any("/" in bid or "\\" in bid or bid in {".", ".."} for bid in branch_ids + save_ids):
                raise StoryArchiveError("故事包文件与清单不一致")
            states = {bid: SessionState.model_validate(_scrub(json.loads(archive.read(f"branches/{bid}.json")), removed)) for bid in branch_ids}
            saves = {sid: SaveFile.model_validate(_scrub(json.loads(archive.read(f"saves/{sid}.json")), removed)) for sid in save_ids}
            records = [MemoryRecord.model_validate(item) for item in _scrub(json.loads(archive.read("memories.json")), removed)] if manifest.get("includes_memory") else []
            if manifest.get('format_version') in (2, 3) and manifest.get('includes_memory'):
                histories = _scrub(json.loads(archive.read('memory_history.json')), removed)
                if not isinstance(histories, dict) or set(histories) != set(branch_ids):
                    raise StoryArchiveError('Historical memory branch closure is incomplete')
                for branch, history in histories.items():
                    floor, clock = history['floor'], history['clock']
                    versions = history['versions']
                    if not isinstance(floor, int) or not isinstance(clock, int) or not 0 <= floor <= clock or not isinstance(versions, list) or len(versions) > 100000:
                        raise StoryArchiveError('Historical memory metadata is invalid')
                    intervals = defaultdict(list)
                    for version in versions:
                        record = MemoryRecord.model_validate(version['record'])
                        start, end = version['valid_from'], version['valid_until']
                        if record.session_id != branch or not isinstance(start, int) or not floor <= start <= clock or (end is not None and (not isinstance(end, int) or not start < end <= clock)):
                            raise StoryArchiveError('Historical memory interval is invalid')
                        intervals[record.id].append((start, end))
                    for spans in intervals.values():
                        spans.sort()
                        if any(left[1] is None or left[1] > right[0] for left, right in zip(spans, spans[1:])):
                            raise StoryArchiveError('Historical memory versions overlap')
            referenced = set().union(*(_media_refs(x.model_dump(mode="json")) for x in states.values()),
                                     *(_media_refs(x.model_dump(mode="json")) for x in saves.values()))
            if not referenced <= set(media):
                raise StoryArchiveError("故事头像未包含在媒体清单中")
        except StoryArchiveError:
            raise
        except Exception as exc:
            raise StoryArchiveError("故事包内容损坏或模型不兼容") from exc
    if story_id not in states:
        raise StoryArchiveError("故事根路线缺失")
    for bid, state in states.items():
        if state.meta.id != bid or state.meta.story_id != story_id or state.schema_version != 3:
            raise StoryArchiveError(f"路线身份或版本无效：{bid}")
        parent = state.meta.parent_branch_id
        if (bid == story_id and parent is not None) or (bid != story_id and parent not in states):
            raise StoryArchiveError(f"路线关系不完整：{bid}")
        seen = {bid}
        while parent:
            if parent in seen:
                raise StoryArchiveError("世界线出现环路")
            seen.add(parent)
            parent = states[parent].meta.parent_branch_id
    for save in saves.values():
        if save.state.meta.id not in states:
            raise StoryArchiveError("存档所属路线缺失")
    if any(record.session_id not in states for record in records):
        raise StoryArchiveError("记忆所属路线缺失")
    return manifest, states, saves, records, removed[0]


async def import_story(container: Any, data: bytes, *, preserve_ids: bool = False) -> dict[str, Any]:
    manifest, original, saves, records, redacted = await asyncio.to_thread(_read_package, data)
    histories = {}
    if manifest.get('format_version') in (2, 3) and manifest.get('includes_memory'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            histories = _scrub(json.loads(archive.read('memory_history.json')), [0])
        if set(histories) != set(original):
            raise StoryArchiveError('Historical memory branch closure is incomplete')
    from mrp.storage.story_media import store_media
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for ref in manifest.get("media", []):
            await asyncio.to_thread(store_media, container.data_root, archive.read(f"media/{ref}"), expected_ref=ref)
    original_available = preserve_ids and all(
        not container.sessions.exists_sync(old) for old in original
    ) and all(not container.saves.exists_sync(old) for old in saves)
    id_map = {old: old if original_available else new_id("sess") for old in original}
    while any(container.sessions.exists_sync(new) for new in id_map.values()):
        id_map = {old: new_id("sess") for old in original}
    new_story_id = id_map[manifest["story_id"]]
    save_map = {old: old if original_available else new_id("save") for old in saves}
    all_record_ids = {record.id for record in records} | {record.id for save in saves.values() for record in save.memory_snapshot} | {version['record']['id'] for history in histories.values() for version in history['versions']}
    record_map = {rid: rid if original_available else new_id("mem") for rid in all_record_ids}
    by_branch: dict[str, list[MemoryRecord]] = defaultdict(list)
    for record in records:
        cloned = record.model_copy(deep=True)
        cloned.id = record_map[record.id]
        cloned.session_id = id_map[record.session_id]
        cloned.inherited_from_id = record_map.get(record.inherited_from_id or "")
        cloned.supersedes = [record_map[mid] for mid in record.supersedes if mid in record_map]
        by_branch[cloned.session_id].append(cloned)
    missing_assets: set[str] = set(manifest.get("external_avatar_characters") or [])
    def remap_state(old: SessionState) -> SessionState:
        old_id = old.meta.id
        state = old.model_copy(deep=True)
        state.meta.id = id_map[old_id]
        state.meta.story_id = new_story_id
        state.meta.parent_branch_id = id_map.get(old.meta.parent_branch_id or "")
        state.meta.fork_save_id = save_map.get(old.meta.fork_save_id or "")
        state.meta.fork_request_hash = None
        if state.meta.id != old.meta.id:
            state.meta.branch_revision = 0
            inherit_receipt_audit(state, old)
            if manifest.get('creation_receipts'):
                state.generation_operations['__mrp_creation_audit_v1__'] = {
                    'version':1, 'executable':False, 'receipts':manifest['creation_receipts']}
        for message in state.messages:
            message.session_id = state.meta.id
        for bookmark in state.bookmarks:
            bookmark.id = new_id("bookmark") if not original_available else bookmark.id
            bookmark.story_id = new_story_id
            bookmark.branch_id = state.meta.id
        for character in state.characters:
            if character.card.avatar_path:
                missing_assets.add(character.card.avatar_path)
                character.card.avatar_path = None
        for revision in state.state_revisions:
            for character in revision.snapshot.get("characters", []):
                if character.get("card", {}).get("avatar_path"):
                    character["card"]["avatar_path"] = None
            revision.content_hash = projection_hash(revision.snapshot)
            if not manifest.get("includes_memory"):
                revision.memory_watermark = None
        return state

    new_states = {id_map[old_id]: remap_state(old) for old_id, old in original.items()}
    for identity in id_map.values():
        await container.sessions.claim_creation_entity('branch', identity)
    for identity in save_map.values():
        await container.sessions.claim_creation_entity('save', identity)
    transaction_id = new_id('import')
    marker = container.paths.data_root / 'pending_imports' / f'{transaction_id}.json'
    for state in new_states.values():
        state.generation_operations['import-publication'] = {'transaction_id': transaction_id}
    await asyncio.to_thread(write_json_atomic, marker,
                            {'transaction_id': transaction_id, 'branches': list(id_map.values()),
                             'saves': list(save_map.values())}, fsync=True)
    created_sessions: list[str] = []
    created_saves: list[str] = []
    try:
        if manifest.get("includes_memory"):
            for old_id, old in original.items():
                bid = id_map[old_id]
                watermark = max((rev.memory_watermark or 0 for rev in old.state_revisions), default=0)
                await asyncio.to_thread(container.memory_store.import_branch_records, bid, by_branch[bid], watermark)
                if old_id in histories:
                    history = histories[old_id]
                    for version in history['versions']:
                        record = version['record']
                        record['id'] = record_map[record['id']]
                        record['session_id'] = bid
                        record['inherited_from_id'] = record_map.get(record.get('inherited_from_id') or '')
                        record['supersedes'] = [record_map[mid] for mid in record.get('supersedes', []) if mid in record_map]
                    await asyncio.to_thread(container.memory_store.import_history, bid, history)
                else:
                    await asyncio.to_thread(container.memory_store.declare_snapshot_history, bid, watermark)
        for old_id in sorted(original,key=lambda identity:identity != manifest['story_id']):
            state = new_states[id_map[old_id]]
            watermark = container.memory_store.current_watermark(state.meta.id)
            await container.sessions.create_state_exclusive(state, watermark)
            created_sessions.append(state.meta.id)
        for old_save_id, old_save in saves.items():
            copy = old_save.model_copy(deep=True)
            copy.state = remap_state(old_save.state)
            copy.memory_refs = [record_map[ref] for ref in old_save.memory_refs if ref in record_map]
            if manifest.get("includes_memory"):
                for record in copy.memory_snapshot:
                    record.id = record_map.get(record.id, new_id("mem"))
                    record.session_id = id_map.get(record.session_id, copy.state.meta.id)
                    record.inherited_from_id = record_map.get(record.inherited_from_id or "")
                    record.supersedes = [record_map[mid] for mid in record.supersedes if mid in record_map]
            else:
                copy.memory_snapshot = []
                copy.memory_refs = []
                copy.memory_snapshot_complete = False
            new_save_id = save_map[old_save_id]
            await container.saves.publish(new_save_id, copy)
            created_saves.append(new_save_id)
        await container.saves.list()
        if original_available:
            from mrp.storage.creation_operations import restore_receipts
            await asyncio.to_thread(restore_receipts, container.sessions.story_db, manifest.get('creation_receipts', []))
        marker.unlink(missing_ok=True)
        return {
            "story_id": new_story_id, "source_story_id": manifest["story_id"],
            "branch_revision": new_states[new_story_id].meta.branch_revision,
            "branches": [{"old_id": old, "new_id": new} for old, new in id_map.items()],
            "saves": [{"old_id": old, "new_id": new} for old, new in save_map.items()],
            "memory_records": len(records), "credential_fields_redacted": redacted,
            "missing_assets": sorted(missing_assets),
            "note": "角色和世界书快照已随路线恢复；原设备头像路径不会复用。",
        }
    except Exception:
        for save_id in created_saves:
            await container.saves.delete(save_id)
        for branch_id in created_sessions:
            await container.sessions.delete_state(branch_id)
        for branch_id in id_map.values():
            # Never purge another story that won an exclusive-publish race.
            existing = await container.sessions.load_state_readonly(branch_id)
            if existing is None or existing.generation_operations.get('import-publication', {}).get('transaction_id') == transaction_id:
                await asyncio.to_thread(container.memory_store.purge_branch, branch_id)
        marker.unlink(missing_ok=True)
        raise


def recover_pending_imports(container):
    """Startup recovery only touches identities owned by this import journal."""
    folder = container.paths.data_root / 'pending_imports'
    for marker in folder.glob('import-*.json'):
        try:
            transaction = json.loads(marker.read_text(encoding='utf-8'))
            identity = transaction['transaction_id']
            if marker.stem != identity:
                continue
            states = {branch: container.sessions.load_state_readonly_sync(branch) for branch in transaction['branches']}
            owned = {branch for branch, state in states.items() if state is not None and state.generation_operations.get('import-publication', {}).get('transaction_id') == identity}
            complete = len(owned) == len(states) and all(container.saves.exists_sync(save) for save in transaction['saves'])
            if not complete:
                for branch in owned:
                    if container.sessions.story_db.owns(branch):
                        container.sessions.story_db.delete(branch)
                    else:
                        container.sessions.path_for(branch).unlink(missing_ok=True)
                    container.memory_store.purge_branch(branch)
                for branch, state in states.items():
                    if state is None:
                        container.memory_store.purge_branch(branch)
                # Save identities are randomly allocated unless preserve_ids was
                # validated. Require an owned branch before removing a save.
                for save_id in transaction['saves']:
                    save = container.saves.story_db.load_save(save_id)
                    if save is not None and save.state.meta.id in owned:
                        container.saves.story_db.delete_save(save_id)
            marker.unlink(missing_ok=True)
        except Exception:
            # Unknown or corrupt journals remain for explicit recovery; never
            # turn uncertainty into broad cleanup of user data.
            continue
