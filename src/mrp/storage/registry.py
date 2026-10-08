"""角色/世界书注册表：`characters/*.json`、`lorebooks/*.json` 的加载与维护。

- 加载失败不再静默消失：`logger.warning(exc_info=True)` + `quarantine: list[(path, reason)]`，
  其余条目照常加载。
- 写用原子替换（旧实现直接 `write_text`，中断会留半文件）。
- 角色删除连带清理头像目录 `<characters>/<id>/`（对齐 app.py DELETE 语义）。
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from mrp.shared.models import Character, Lorebook, utcnow
from mrp.worlds.schema import World

from .atomic import write_json_atomic
from .avatar_ref import bind_library_avatar, write_bound_avatar_path
from .json_store import read_text_many_sync, scan_json_files_sync
from .paths import AppPaths, is_safe_name

logger = logging.getLogger("mrp.storage.registry")


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class _JsonRegistry:
    """同构注册表骨架（角色/世界书共用；差异只有模型与头像清理）。"""

    model_cls: type
    kind: str = "条目"

    def __init__(self, paths: AppPaths, directory: Path) -> None:
        self.paths = paths
        self.directory = directory
        self.by_id: dict[str, object] = {}
        self.quarantine: list[tuple[Path, str]] = []

    # ---------- 读 ----------

    async def load_all(self) -> list:
        return await asyncio.to_thread(self.load_all_sync)

    def load_all_sync(self) -> list:
        self.quarantine = []
        entries = scan_json_files_sync(self.directory)
        texts = read_text_many_sync([p for p, _ in entries])
        out: dict[str, object] = {}
        for (path, _), text in zip(entries, texts):
            if text is None:
                self._quarantine(path, "读取失败（OSError/编码错误）")
                continue
            try:
                item = self.model_cls.model_validate_json(text)
            except Exception as exc:  # noqa: BLE001 —— 坏条目隔离，不拖垮其余
                self._quarantine(path, _reason(exc))
                continue
            out[item.id] = item
        self.by_id = out
        return list(out.values())

    def _quarantine(self, path: Path, reason: str) -> None:
        self.quarantine.append((path, reason))
        logger.warning("%s加载失败（已隔离）: %s —— %s", self.kind, path, reason, exc_info=True)

    # ---------- 写 ----------

    async def save(self, item) -> None:
        await asyncio.to_thread(self.save_sync, item)

    def save_sync(self, item) -> None:
        path = self._item_path(item.id)
        write_json_atomic(path, item, indent=None)  # 与旧 `model_dump_json()` 逐字节一致
        self.by_id[item.id] = item

    async def delete(self, item_id: str, *, avatar: bool = False) -> bool:
        return await asyncio.to_thread(self.delete_sync, item_id, avatar=avatar)

    def delete_sync(self, item_id: str, *, avatar: bool = False) -> bool:
        path = self._item_path(item_id)
        existed = path.exists()
        if existed:
            path.unlink()
        if avatar:
            shutil.rmtree(self.directory / item_id, ignore_errors=True)
        self.by_id.pop(item_id, None)
        return existed

    async def reload_one(self, item_id: str):
        """单条重读；坏文件进 quarantine 并返回 None。"""
        return await asyncio.to_thread(self.reload_one_sync, item_id)

    def reload_one_sync(self, item_id: str):
        path = self._item_path(item_id)
        text = None
        try:
            text = path.read_text(encoding="utf-8")
            item = self.model_cls.model_validate_json(text)
        except FileNotFoundError:
            self.by_id.pop(item_id, None)
            return None
        except Exception as exc:  # noqa: BLE001
            self._quarantine(path, _reason(exc))
            return None
        self.by_id[item_id] = item
        return item

    def _item_path(self, item_id: str) -> Path:
        if not is_safe_name(item_id):
            raise ValueError(f"非法 {self.kind} id: {item_id!r}")
        return self.directory / f"{item_id}.json"


class CharacterRegistry(_JsonRegistry):
    """`data/characters/<id>.json`；头像在 `<characters>/<id>/avatar.png`。

    加载时若标准头像文件已在，把 `card.avatar_path` 记成相对路径。
    这一步不走 `save_sync`，避免整表重写打乱字段顺序，也不动 revision。
    """

    model_cls = Character
    kind = "角色卡"

    def __init__(self, paths: AppPaths) -> None:
        super().__init__(paths, paths.characters_dir)

    def load_all_sync(self) -> list:
        items = super().load_all_sync()
        for item in items:
            self._bind_avatar(item)
        return items

    def reload_one_sync(self, item_id: str):
        item = super().reload_one_sync(item_id)
        if item is not None:
            self._bind_avatar(item)
        return item

    def _bind_avatar(self, item: Character) -> None:
        if not bind_library_avatar(item, self.directory):
            return
        avatar_path = item.card.avatar_path
        if avatar_path:
            write_bound_avatar_path(self._item_path(item.id), avatar_path)

    async def delete(self, character_id: str, *, avatar: bool = True) -> bool:
        return await super().delete(character_id, avatar=avatar)

    def delete_sync(self, character_id: str, *, avatar: bool = True) -> bool:
        return super().delete_sync(character_id, avatar=avatar)

    def avatar_dir(self, character_id: str) -> Path:
        if not is_safe_name(character_id):
            raise ValueError(f"非法角色 id: {character_id!r}")
        return self.paths.avatars_dir / character_id


class LorebookRegistry(_JsonRegistry):
    """`data/lorebooks/<id>.json`。"""

    model_cls = Lorebook
    kind = "世界书"

    def __init__(self, paths: AppPaths) -> None:
        super().__init__(paths, paths.lorebooks_dir)
        self._write_lock = asyncio.Lock()

    async def revise(
        self, book_id: str, expected_revision: int, change: Callable[[Lorebook], None]
    ) -> Lorebook:
        """Apply a compare-and-set edit to one lorebook without exposing a partial write."""
        async with self._write_lock:
            original = self.by_id.get(book_id)
            if original is None:
                raise KeyError(book_id)
            if original.revision != expected_revision:
                raise RuntimeError("世界书已在另一处更新，请刷新后重试")
            draft = original.model_copy(deep=True)
            change(draft)
            draft.revision += 1
            draft.updated_at = utcnow()
            validated = Lorebook.model_validate(draft.model_dump(mode="python"))
            await self.save(validated)
            return validated


class WorldRegistry(_JsonRegistry):
    """World and archive changes share one revisioned, atomic JSON document."""

    model_cls = World
    kind = "世界"

    def __init__(self, paths: AppPaths) -> None:
        super().__init__(paths, paths.worlds_dir)
        self._write_lock = asyncio.Lock()

    async def create(self, world: World) -> World:
        async with self._write_lock:
            if world.id in self.by_id:
                raise ValueError("世界 ID 已存在")
            await self.save(world)
            return world

    async def revise(
        self, world_id: str, expected_revision: int, change: Callable[[World], None]
    ) -> World:
        async with self._write_lock:
            original = self.by_id.get(world_id)
            if original is None:
                raise KeyError(world_id)
            if original.revision != expected_revision:
                raise RuntimeError("世界已在另一处更新，请刷新后重试")
            draft = original.model_copy(deep=True)
            change(draft)
            draft.revision += 1
            draft.updated_at = utcnow()
            validated = World.model_validate(draft.model_dump(mode="python"))
            await self.save(validated)
            return validated
