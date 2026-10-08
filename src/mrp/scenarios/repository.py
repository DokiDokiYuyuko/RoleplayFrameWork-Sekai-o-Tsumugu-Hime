"""Small JSON repository for local scenario presets."""
from __future__ import annotations

import asyncio
import json
import hashlib
from pathlib import Path

from mrp.storage.atomic import write_json_atomic
from mrp.storage.paths import AppPaths, is_safe_name

from .schema import ScenarioPackage, ScenarioSummary, legacy_package_id


class ScenarioRepository:
    def __init__(self, paths: AppPaths) -> None:
        self.directory = paths.scenarios_dir
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, scenario_id: str) -> Path:
        if not is_safe_name(scenario_id):
            raise ValueError("场景预设 ID 不合法")
        return self.directory / f"{scenario_id}.json"

    async def list(self) -> list[ScenarioSummary]:
        return await asyncio.to_thread(self._list_sync)

    def _list_sync(self) -> list[ScenarioSummary]:
        rows: list[ScenarioSummary] = []
        for path in sorted(self.directory.glob("*.json"), key=lambda p: p.name):
            try:
                package = self._parse_package(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — 单份损坏不影响其他预设
                continue
            rows.append(
                ScenarioSummary(
                    id=package.id,
                    package_id=package.package_id,
                    revision=package.revision,
                    title=package.title,
                    description=package.description,
                    author=package.author,
                    tags=package.tags,
                    character_names=[member.card.name for member in package.cast],
                    lorebook_count=len(package.lorebooks),
                    created_at=package.created_at,
                    updated_at=package.updated_at,
                    imported_at=package.imported_at,
                )
            )
        return rows

    async def get(self, scenario_id: str) -> ScenarioPackage | None:
        return await asyncio.to_thread(self._get_sync, scenario_id)

    def _get_sync(self, scenario_id: str) -> ScenarioPackage | None:
        path = self._path(scenario_id)
        try:
            return self._parse_package(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    @staticmethod
    def _parse_package(text: str) -> ScenarioPackage:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("预设正文必须是 JSON 对象")
        if not data.get("package_id"):
            source_id = str(data.get("id") or hashlib.sha256(text.encode("utf-8")).hexdigest())
            data["package_id"] = legacy_package_id(source_id)
        if not data.get("updated_at") and data.get("created_at"):
            data["updated_at"] = data["created_at"]
        return ScenarioPackage.model_validate(data)

    async def save(self, package: ScenarioPackage) -> None:
        await asyncio.to_thread(write_json_atomic, self._path(package.id), package, indent=2)

    async def delete(self, scenario_id: str) -> bool:
        return await asyncio.to_thread(self._delete_sync, scenario_id)

    def _delete_sync(self, scenario_id: str) -> bool:
        path = self._path(scenario_id)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
