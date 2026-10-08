"""Verified, credential-scrubbed story snapshots and recoverable trash."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mrp.orchestrator.story_archive import _read_package, export_story, import_story
from mrp.storage.atomic import write_json_atomic
from mrp.storage.paths import is_safe_name


class StoryBackupService:
    def __init__(self, container: Any) -> None:
        self.container = container
        self.root = container.data_root / "backups" / "stories"
        self.trash = container.data_root / "trash" / "stories"
        self._locks: dict[str, asyncio.Lock] = {}
        self._scheduled: dict[str, asyncio.Task] = {}
        self.errors: dict[str, str] = {}

    def _path(self, story_id: str) -> Path:
        if not is_safe_name(story_id):
            raise ValueError("无效故事 ID")
        return self.root / story_id

    def _trash_path(self, story_id: str) -> Path:
        if not is_safe_name(story_id):
            raise ValueError("无效故事 ID")
        return self.trash / f"{story_id}.json"

    async def snapshot(self, story_id: str, *, reason: str = "automatic") -> dict[str, Any]:
        async with self._locks.setdefault(story_id, asyncio.Lock()):
            data = await export_story(self.container, story_id, include_memory=True)
            manifest, states, saves, memories, _ = await asyncio.to_thread(_read_package, data)
            digest = hashlib.sha256(data).hexdigest()
            now = datetime.now(timezone.utc).isoformat()
            filename = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{digest[:12]}.story.zip"
            folder = self._path(story_id)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / filename
            temporary = target.with_suffix(".tmp")
            def write() -> None:
                try:
                    with temporary.open("wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                        raise OSError("快照校验失败")
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            await asyncio.to_thread(write)
            await asyncio.to_thread(self._prune)
            self.errors.pop(story_id, None)
            return {
                "story_id": story_id, "title": manifest["title"], "reason": reason,
                "created_at": now, "filename": filename, "sha256": digest,
                "branch_count": len(states), "message_count": sum(len(s.messages) for s in states.values()),
                "branch_ids": list(states),
                "save_count": len(saves), "memory_count": len(memories), "bytes": len(data),
                "external_avatar_characters": manifest.get("external_avatar_characters", []),
            }

    def _prune(self) -> None:
        """Keep the newest and every trash restore point, even beyond the quota."""
        if not self.root.exists():
            return
        protected = set()
        lifecycle=getattr(self.container,'story_lifecycle',None)
        if lifecycle:
            for row in lifecycle.repo.trash():
                if row.get('filename'): protected.add((row['story_id'],row['filename']))
        if self.trash.exists():
            for index in self.trash.glob("*.json"):
                try:
                    row = json.loads(index.read_text(encoding="utf-8"))
                    protected.add((row["story_id"], row["filename"]))
                except (OSError, KeyError, ValueError):
                    continue
        files = []
        now = datetime.now(timezone.utc).timestamp()
        keep_count = self.container.settings.backup_keep_count
        keep_days = self.container.settings.backup_retention_days
        for folder in self.root.iterdir():
            if not folder.is_dir():
                continue
            snapshots = sorted(folder.glob("*.story.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
            if snapshots:
                protected.add((folder.name, snapshots[0].name))
            for index, path in enumerate(snapshots):
                if ((folder.name, path.name) not in protected
                        and (index >= keep_count or now - path.stat().st_mtime > keep_days * 86400)):
                    path.unlink(missing_ok=True)
                else:
                    files.append(path)
        total = sum(path.stat().st_size for path in files if path.exists())
        limit = self.container.settings.backup_max_bytes
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            if total <= limit:
                break
            if (path.parent.name, path.name) in protected or not path.exists():
                continue
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size

    def schedule(self, story_id: str) -> None:
        if not is_safe_name(story_id) or story_id in self._scheduled:
            return
        async def delayed() -> None:
            try:
                await asyncio.sleep(45)
                await self.snapshot(story_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.errors[story_id] = f"自动备份失败（{type(exc).__name__}），当前故事已保存但尚无新的快照"
                import logging
                logging.getLogger("mrp.backups").exception("Automatic story snapshot failed for %s", story_id)
            finally:
                self._scheduled.pop(story_id, None)
        self._scheduled[story_id] = asyncio.create_task(delayed())

    async def seed_existing(self) -> None:
        """Give legacy stories one recovery point without rewriting their JSON."""
        rows = await self.container.sessions.list_summaries()
        story_ids = sorted({row.story_id or row.id for row in rows})
        for story_id in story_ids:
            if list(self._path(story_id).glob("*.story.zip")):
                continue
            try:
                await self.snapshot(story_id, reason="initial")
            except Exception as exc:
                self.errors[story_id] = f"初始备份失败（{type(exc).__name__}）"

    async def trash_story(self, story_id: str) -> dict[str, Any]:
        entry = await self.snapshot(story_id, reason="before_delete")
        entry["status"] = "pending"
        path = self._trash_path(story_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(write_json_atomic, path, entry, indent=2)
        return entry

    async def confirm_trashed(self, story_id: str) -> None:
        path = self._trash_path(story_id)
        entry = await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
        entry["status"] = "trashed"
        await asyncio.to_thread(write_json_atomic, path, entry, indent=2)

    async def list_trash(self) -> list[dict[str, Any]]:
        if not self.trash.exists():
            return []
        def read_all() -> list[dict[str, Any]]:
            rows = []
            for path in self.trash.glob("*.json"):
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                    if row.get("status") == "trashed":
                        rows.append(row)
                except (OSError, ValueError):
                    continue
            return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)
        return await asyncio.to_thread(read_all)

    async def restore(self, story_id: str) -> dict[str, Any]:
        path = self._trash_path(story_id)
        if not path.exists():
            raise FileNotFoundError("回收区中没有该故事")
        entry = await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
        if entry.get("status") != "trashed":
            raise ValueError("故事删除尚未完成，请先检查原故事")
        archive_path = self._path(story_id) / entry["filename"]
        data = await asyncio.to_thread(archive_path.read_bytes)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("故事备份校验失败，未执行恢复")
        result = await import_story(self.container, data, preserve_ids=True)
        await asyncio.to_thread(path.unlink)
        return result

    async def purge(self, story_id: str) -> None:
        path = self._trash_path(story_id)
        if not path.exists():
            raise FileNotFoundError("回收区中没有该故事")
        entry = await asyncio.to_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
        if entry.get("status") != "trashed":
            raise ValueError("故事删除尚未完成")
        folder = self._path(story_id).resolve(strict=False)
        if folder.parent != self.root.resolve(strict=False):
            raise ValueError("快照目录越界")
        def remove() -> None:
            if folder.is_dir():
                shutil.rmtree(folder)
            path.unlink(missing_ok=True)
        await asyncio.to_thread(remove)

    async def snapshot_bytes(self, story_id: str, filename: str) -> bytes:
        if not is_safe_name(filename):
            raise ValueError("无效快照名称")
        path = self._path(story_id) / filename
        data = await asyncio.to_thread(path.read_bytes)
        await asyncio.to_thread(_read_package, data)
        return data

    async def list_snapshots(self, story_id: str) -> list[dict[str, Any]]:
        folder = self._path(story_id)
        if not folder.exists():
            return []
        return [{"filename": p.name, "bytes": p.stat().st_size, "created_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}
                for p in sorted(folder.glob("*.story.zip"), reverse=True)]

    async def adopt_legacy_trash(self,story_id,repo):
        path=self._trash_path(story_id)
        if not path.exists(): return
        entry=json.loads(await asyncio.to_thread(path.read_text,encoding='utf-8'))
        if entry.get('status')!='trashed': raise ValueError('旧故事删除尚未完成')
        filename=entry['filename']
        if not is_safe_name(filename): raise ValueError('无效备份文件')
        data=await asyncio.to_thread((self._path(story_id)/filename).read_bytes)
        if hashlib.sha256(data).hexdigest()!=entry['sha256']: raise ValueError('旧回收备份校验失败')
        manifest,states,saves,records,_=await asyncio.to_thread(_read_package,data)
        if manifest['story_id']!=story_id: raise ValueError('旧回收故事身份不一致')
        import io,zipfile
        histories={}
        if manifest.get('format_version') in (2,3) and manifest.get('includes_memory'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                from mrp.orchestrator.story_archive import _scrub
                histories=_scrub(json.loads(archive.read('memory_history.json')),[0])
        await asyncio.to_thread(repo.adopt_legacy_trash,story_id,entry,states,saves,records,histories,manifest.get('creation_receipts',[]))
