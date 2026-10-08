"""Story, branch, and event operations over branch-local SessionState files."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mrp.orchestrator.worldline_state import (
    BranchPointUnavailable, apply_projection, record_persisted_head, state_at,
)
from mrp.shared.models import SessionState, StoryEvent, new_id, utcnow
from mrp.storage.atomic import write_json_atomic
from mrp.orchestrator.memory import MemoryHistoryUnavailable
from mrp.storage.command_receipts import inherit_receipt_audit


class WorldlineError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ForkResult:
    branch_id: str
    story_id: str
    parent_branch_id: str
    fork_message_id: str | None
    branch_revision: int
    repeated: bool = False
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class WorldlineService:
    """One container's branch coordinator; never shares mutable story state."""

    def __init__(self, container: Any) -> None:
        self.c = container
        self.pending_dir: Path = container.paths.data_root / "pending_forks"
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        from mrp.orchestrator.story_archive import recover_pending_imports
        from mrp.storage.creation_operations import recover
        recover(container.sessions.story_db, container.memory_store)
        recover_pending_imports(container)
        self._recover_pending()

    def _recover_pending(self) -> None:
        for marker in self.pending_dir.glob("sess-*.json"):
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                child_id = str(payload["child_id"])
                if marker.stem != child_id:
                    continue
                if self.c.sessions.exists_sync(child_id):
                    if self.c.sessions.load_state_readonly_sync(child_id) is None:
                        raise ValueError("Pending fork has no readable committed state")
                else:
                    self.c.memory_store.purge_branch(child_id)
                marker.unlink(missing_ok=True)
            except Exception:
                # Leave uncertain transactions for an explicit recovery pass.
                continue

    @staticmethod
    def _request_hash(parent_id: str, anchor_id: str | None, title: str, save_id: str | None) -> str:
        payload = json.dumps([parent_id, anchor_id, title, save_id], ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _child_id(parent_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(f"{parent_id}\0{idempotency_key}".encode("utf-8")).hexdigest()
        return f"sess-{digest[:24]}"

    async def stories(self) -> list[dict[str, Any]]:
        rows = await self.c.sessions.list_summaries()
        groups: dict[str, list] = {}
        for row in rows:
            groups.setdefault(row.story_id or row.id, []).append(row)
        save_rows = await self.c.saves.list()
        save_count_by_branch: dict[str, int] = {}
        for save in save_rows:
            save_count_by_branch[save.session_id] = save_count_by_branch.get(save.session_id, 0) + 1
        output = []
        for story_id, branches in groups.items():
            root = next((row for row in branches if row.id == story_id), branches[0])
            latest = max(branches, key=lambda row: (row.updated_at, row.id))
            source_world = self.c.worlds.get(root.source_world_id) if root.source_world_id else None
            output.append({
                "story_id": story_id,
                "title": root.title,
                "cover_id": source_world.cover_id if source_world else None,
                "branch_count": len(branches),
                "event_count": sum(len(row.event_nodes) for row in branches),
                "save_count": sum(save_count_by_branch.get(row.id, 0) for row in branches),
                "latest_branch_id": latest.id,
                "updated_at": latest.updated_at,
            })
        return sorted(output, key=lambda item: (-item["updated_at"], item["story_id"]))

    async def delete_story(self, story_id: str) -> dict[str, Any]:
        """Remove every branch and save belonging to one story, leaving shared assets intact."""
        rows = [row for row in await self.c.sessions.list_summaries()
                if (row.story_id or row.id) == story_id]
        if not rows:
            previous = next((entry for entry in await self.c.story_backups.list_trash()
                             if entry.get("story_id") == story_id), None)
            if previous is not None:
                return {"ok": True, "branch_ids": previous.get("branch_ids", []), "saves_deleted": previous.get("save_count", 0),
                        "recoverable": True, "backup_sha256": previous["sha256"], "repeated": True}
            raise WorldlineError(404, "故事不存在")
        branch_ids = {row.id for row in rows}
        if any(runner.busy() for branch_id, runner in self.c.runners.items()
               if branch_id in branch_ids):
            raise WorldlineError(409, "故事正在生成回复，请等待本轮完成后再删除")
        saves = [save for save in await self.c.saves.list()
                 if save.session_id in branch_ids]

        try:
            backup = await self.c.story_backups.trash_story(story_id)
        except Exception as exc:
            raise WorldlineError(503, "删除前备份失败，故事未被删除") from exc
        pending = self.c.story_backups._scheduled.pop(story_id, None)
        if pending is not None:
            pending.cancel()

        for branch_id in branch_ids:
            await self.c.drop_runner(branch_id)
        for save in saves:
            await self.c.saves.delete(save.id)
        # Keep the root until last, so an interrupted deletion still appears
        # on the shelf and can be retried.
        deletion_order = sorted(branch_ids - {story_id})
        if story_id in branch_ids:
            deletion_order.append(story_id)
        for branch_id in deletion_order:
            indexing = self.c.story_search._scheduled.pop(branch_id, None)
            if indexing is not None:
                indexing.cancel()
            await self.c.sessions.delete_state(branch_id)
            await asyncio.to_thread(self.c.story_search.delete_branch, branch_id)
            await asyncio.to_thread(self.c.memory_store.purge_branch, branch_id)
            await asyncio.to_thread(self.c.request_archive.delete_session, branch_id)
        await self.c.story_backups.confirm_trashed(story_id)
        return {"ok": True, "branch_ids": sorted(branch_ids), "saves_deleted": len(saves),
                "recoverable": True, "backup_sha256": backup["sha256"]}

    async def worldline(self, story_id: str, cursor: int = 0, limit: int = 40, query: str = "") -> dict[str, Any]:
        rows = [row for row in await self.c.sessions.list_summaries()
                if (row.story_id or row.id) == story_id]
        if not rows:
            raise WorldlineError(404, "故事不存在")
        rows.sort(key=lambda row: (row.created_at, row.id))
        total_branches = len(rows)
        needle = query.strip().casefold()
        if needle:
            rows = [row for row in rows if needle in (row.branch_name or row.title).casefold()
                    or any(needle in str(event.get("title", "")).casefold() for event in row.event_nodes)]
        offset = max(0, cursor)
        page = rows[offset:offset + limit]
        return {
            "story_id": story_id,
            "total_branches": total_branches,
            "matched_branches": len(rows),
            "next_cursor": str(offset + limit) if offset + limit < len(rows) else None,
            "branches": [{
                "id": row.id,
                "name": row.branch_name or row.title,
                "parent_branch_id": row.parent_branch_id,
                "fork_message_id": row.fork_message_id,
                "fork_save_id": row.fork_save_id,
                "branch_revision": row.branch_revision,
                "archived": row.archived,
                "message_count": row.messages,
                "first_message_id": row.first_message_id,
                "last_message_id": row.last_message_id,
                "events": row.event_nodes,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at,
            } for row in page],
        }

    async def fork_message(
        self, parent_id: str, message_id: str, title: str,
        expected_revision: int, idempotency_key: str,
    ) -> ForkResult:
        runner = await self.c.load_session(parent_id)
        if runner is None:
            raise WorldlineError(404, "世界线不存在")
        if runner.busy():
            raise WorldlineError(409, "回合进行中，稍后再创建分支")
        async with runner.runtime.turn_lock:
            return await self._fork_locked(
                runner.state, message_id, title, expected_revision,
                idempotency_key, save_id=None,
            )

    async def fork_save(
        self, save_id: str, title: str, idempotency_key: str,
    ) -> ForkResult:
        save = await self.c.saves.load(save_id)
        if save is None:
            raise WorldlineError(404, "存档不存在")
        parent_id = save.state.meta.id
        runner = await self.c.load_session(parent_id)
        if runner is None:
            raise WorldlineError(404, "存档所属世界线不存在")
        if runner.busy():
            raise WorldlineError(409, "回合进行中，稍后再创建分支")
        async with runner.runtime.turn_lock:
            snapshot = save.memory_snapshot if save.memory_snapshot_complete else None
            if snapshot is None:
                revision = next((item for item in save.state.state_revisions
                                 if item.id == save.state.head_state_revision_id), None)
                if revision is None or revision.memory_watermark is None or revision.memory_watermark > 0:
                    raise WorldlineError(422, "该旧存档没有完整的记忆快照，无法安全开线")
            return await self._fork_locked(
                save.state, save.state.messages[-1].id if save.state.messages else None,
                title, None, idempotency_key, save_id=save_id,
                save_memory_snapshot=snapshot,
            )

    async def _fork_locked(
        self, source: SessionState, message_id: str | None, title: str,
        expected_revision: int | None, idempotency_key: str,
        save_id: str | None,
        save_memory_snapshot: list | None = None,
    ) -> ForkResult:
        parent_id = source.meta.id
        name = title.strip() or "新世界线"
        child_id = self._child_id(parent_id, idempotency_key)
        request_hash = self._request_hash(parent_id, message_id, name, save_id)
        existing = await self.c.sessions.load_state_readonly(child_id)
        if existing is not None:
            if (existing.meta.parent_branch_id != parent_id
                    or existing.meta.fork_request_hash != request_hash):
                raise WorldlineError(409, "幂等键已用于另一项分支请求")
            await self.c.sessions.adopt_creation_branch(existing)
            return ForkResult(child_id, existing.meta.story_id, parent_id,
                              existing.meta.fork_message_id,
                              existing.meta.branch_revision, repeated=True,
                              warnings=tuple(existing.generation_operations.get('fork-history', {}).get('warnings', ())))
        if expected_revision is not None and source.meta.branch_revision != expected_revision:
            raise WorldlineError(409, "世界线已更新，请刷新后重试")

        if save_id is None:
            # Legacy v2 sessions have one trustworthy current-head checkpoint.
            # Freeze its books and current memory watermark before using it.
            if message_id and source.messages and source.messages[-1].id == message_id:
                last = source.messages[-1]
                rev = next((item for item in source.state_revisions
                            if item.id == last.post_state_revision_id), None)
                if rev is not None and rev.source == "legacy_head" and rev.memory_watermark is None:
                    source = source.model_copy(deep=True)
                    record_persisted_head(
                        source, self.c.memory_store.current_watermark(parent_id)
                    )
            try:
                point = state_at(source, message_id or "")
            except BranchPointUnavailable as exc:
                raise WorldlineError(422, str(exc)) from exc
            prefix = list(point.messages)
            events = list(point.events)
            revision = point.revision
        else:
            prefix = [message.model_copy(deep=True) for message in source.messages]
            events = [event.model_copy(deep=True) for event in source.story_events]
            revision = next((item for item in source.state_revisions
                             if item.id == source.head_state_revision_id), None)
            if revision is None:
                raise WorldlineError(422, "该旧存档缺少状态快照，无法安全开线")
        warnings = []
        if revision.memory_watermark is None and save_id is None:
            warnings.append("该历史节点缺少可靠记忆水位；新分支不继承未验证记忆。")
        watermark = revision.memory_watermark or 0

        child = source.model_copy(deep=True)
        apply_projection(child, revision.snapshot)
        child.meta.id = child_id
        child.meta.story_id = source.meta.story_id
        child.meta.title = name
        child.meta.branch_name = name
        child.meta.parent_branch_id = parent_id
        child.meta.fork_message_id = message_id
        child.meta.fork_state_revision_id = revision.id
        child.meta.fork_save_id = save_id
        child.meta.fork_request_hash = request_hash
        child.meta.branch_revision = 0
        child.meta.created_at = utcnow()
        # Runtime journals belong to the new branch, not the parent's future.
        child.turn_runs = []
        child.conversation_runs = []
        child.generation_operations = {}
        inherit_receipt_audit(child, source)
        child.usage_records = []
        child.pending_director = None
        child.messages = [message.model_copy(update={"session_id": child_id}, deep=True)
                          for message in prefix]
        child.story_events = events
        kept_message_ids = {item.id for item in prefix}
        child.bookmarks = [item.model_copy(update={
            "id": new_id("bookmark"), "branch_id": child_id,
            "source_anchor": f"{item.branch_id}:{item.message_id}",
        }, deep=True) for item in source.bookmarks if item.message_id in kept_message_ids]
        kept_revisions = {message.post_state_revision_id for message in prefix}
        kept_revisions.add(revision.id)
        child.state_revisions = [item.model_copy(deep=True) for item in source.state_revisions
                                 if item.id in kept_revisions]
        child.head_state_revision_id = revision.id
        if prefix:
            anchor_time = prefix[-1].created_at
            child.director_log = [item.model_copy(deep=True) for item in source.director_log
                                  if item.created_at <= anchor_time]
        else:
            child.director_log = []

        marker = self.pending_dir / f"{child_id}.json"
        await self.c.sessions.claim_creation_entity('branch', child_id)
        await asyncio.to_thread(write_json_atomic, marker,
                                {"child_id": child_id, "parent_id": parent_id}, indent=None)
        try:
            if save_memory_snapshot is not None:
                copied = []
                memory_ids = {source.id: new_id("mem") for source in save_memory_snapshot}
                for source in save_memory_snapshot:
                    clone = source.model_copy(deep=True)
                    clone.id = memory_ids[source.id]
                    clone.session_id = child_id
                    clone.inherited_from_id = source.inherited_from_id or source.id
                    clone.supersedes = [memory_ids[mid] for mid in source.supersedes if mid in memory_ids]
                    copied.append(clone)
                await asyncio.to_thread(
                    self.c.memory_store.import_branch_records, child_id, copied, watermark,
                )
            elif revision.memory_watermark is not None:
                try:
                    await asyncio.to_thread(
                        self.c.memory_store.copy_at_fork,
                        parent_id, child_id, watermark, prefix,
                    )
                except MemoryHistoryUnavailable:
                    warnings.append("该节点早于保留的记忆历史；新分支不继承未验证记忆。")
                    watermark = 0
            if warnings:
                child.generation_operations['fork-history'] = {'warnings': warnings, 'anchor_revision_id': revision.id, 'material_hash': revision.content_hash, 'memory_history_complete': False}
            summary = await self.c.sessions.save_state(child, watermark)
            marker.unlink(missing_ok=True)
            return ForkResult(child_id, child.meta.story_id, parent_id,
                              message_id, summary.branch_revision, warnings=tuple(warnings))
        except Exception:
            await self.c.sessions.delete_state(child_id)
            await asyncio.to_thread(self.c.memory_store.purge_branch, child_id)
            marker.unlink(missing_ok=True)
            raise

    async def rename_or_archive(
        self, branch_id: str, *, name: str | None, archived: bool | None,
        expected_revision: int,
    ) -> dict[str, Any]:
        runner = await self.c.load_session(branch_id)
        if runner is None:
            raise WorldlineError(404, "世界线不存在")
        if runner.busy():
            raise WorldlineError(409, "回合进行中，稍后再修改路线")
        async with runner.runtime.turn_lock:
            if runner.state.meta.branch_revision != expected_revision:
                raise WorldlineError(409, "世界线已更新，请刷新后重试")
            if name is not None:
                runner.state.meta.branch_name = name.strip() or runner.state.meta.branch_name
                runner.state.meta.title = runner.state.meta.branch_name
            if archived is not None:
                runner.state.meta.archived = archived
            await self.c.persist_session(runner)
            return {"id": branch_id, "name": runner.state.meta.branch_name,
                    "archived": runner.state.meta.archived,
                    "branch_revision": runner.state.meta.branch_revision}

    async def event_create(self, branch_id: str, event: StoryEvent) -> StoryEvent:
        runner = await self.c.load_session(branch_id)
        if runner is None:
            raise WorldlineError(404, "世界线不存在")
        if runner.busy():
            raise WorldlineError(409, "回合进行中，稍后再标记事件")
        async with runner.runtime.turn_lock:
            self._validate_event(runner.state, event)
            runner.state.story_events.append(event)
            await self.c.persist_session(runner)
            return event

    async def event_update(
        self, branch_id: str, event_id: str, changes: dict[str, Any],
    ) -> StoryEvent:
        runner = await self.c.load_session(branch_id)
        if runner is None:
            raise WorldlineError(404, "世界线不存在")
        if runner.busy():
            raise WorldlineError(409, "回合进行中，稍后再修改事件")
        async with runner.runtime.turn_lock:
            old = next((item for item in runner.state.story_events if item.id == event_id), None)
            if old is None:
                raise WorldlineError(404, "剧情事件不存在")
            try:
                updated = StoryEvent.model_validate({**old.model_dump(), **changes})
            except ValueError as exc:
                raise WorldlineError(422, "剧情事件字段无效") from exc
            self._validate_event(runner.state, updated)
            runner.state.story_events = [updated if item.id == event_id else item
                                         for item in runner.state.story_events]
            await self.c.persist_session(runner)
            return updated

    async def event_delete(self, branch_id: str, event_id: str) -> None:
        runner = await self.c.load_session(branch_id)
        if runner is None:
            raise WorldlineError(404, "世界线不存在")
        if runner.busy():
            raise WorldlineError(409, "回合进行中，稍后再删除事件")
        async with runner.runtime.turn_lock:
            before = len(runner.state.story_events)
            runner.state.story_events = [item for item in runner.state.story_events
                                         if item.id != event_id]
            if len(runner.state.story_events) == before:
                raise WorldlineError(404, "剧情事件不存在")
            await self.c.persist_session(runner)

    @staticmethod
    def _validate_event(state: SessionState, event: StoryEvent) -> None:
        message = next((item for item in state.messages
                        if item.id == event.anchor_message_id), None)
        if message is None or message.status != "final":
            raise WorldlineError(422, "剧情事件必须锚定已完成的本线消息")
        allowed = set(state.meta.character_ids) | {"player"}
        if event.visible_to != "all":
            if not event.visible_to or not set(event.visible_to).issubset(allowed):
                raise WorldlineError(422, "事件可见角色不在本线中")
        if message.visible_to != "all":
            if event.visible_to == "all" or not set(event.visible_to).issubset(
                set(message.visible_to)
            ):
                raise WorldlineError(422, "事件不能扩大原消息的可见范围")
