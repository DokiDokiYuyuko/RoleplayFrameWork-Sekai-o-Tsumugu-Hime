"""Portable declarative prompt schemes; foreign scripts are retained as data only."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from mrp.shared.models import new_id
from mrp.storage.atomic import write_json_atomic
from mrp.storage.paths import is_safe_name
from mrp.orchestrator.story_archive import _scrub
from mrp.importers.compatibility import unknown_macros


class PromptSegment(BaseModel):
    id: str = Field(default_factory=lambda: new_id("seg"))
    name: str = Field(min_length=1, max_length=120)
    content: str = Field("", max_length=100_000)
    enabled: bool = True
    anchor: Literal["system", "near", "at_depth"] = "system"
    depth: int = Field(0, ge=0, le=100)
    order: int = Field(100, ge=-10_000, le=10_000)


class PromptTransform(BaseModel):
    id: str = Field(default_factory=lambda: new_id("transform"))
    name: str = Field(min_length=1, max_length=120)
    pattern: str = Field(min_length=1, max_length=500)
    replacement: str = Field("", max_length=2000)
    enabled: bool = True
    phase: Literal["before_send"] = "before_send"


class PromptPreset(BaseModel):
    format: Literal["mrp.prompt_preset"] = "mrp.prompt_preset"
    id: str = Field(default_factory=lambda: new_id("preset"))
    name: str = Field(min_length=1, max_length=160)
    description: str = Field("", max_length=2000)
    source: str = "local"
    revision: int = Field(1, ge=1)
    segments: list[PromptSegment] = Field(default_factory=list, max_length=100)
    transforms: list[PromptTransform] = Field(default_factory=list, max_length=20)
    extensions: dict[str, Any] = Field(default_factory=dict)


class PromptPresetStore:
    def __init__(self, data_root: Path) -> None:
        self.root = data_root / "prompt_presets"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, preset_id: str) -> Path:
        if not is_safe_name(preset_id):
            raise ValueError("无效方案 ID")
        return self.root / f"{preset_id}.json"

    def list(self) -> list[PromptPreset]:
        rows = []
        for path in self.root.glob("*.json"):
            try:
                rows.append(PromptPreset.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(rows, key=lambda row: row.name.casefold())

    def get(self, preset_id: str) -> PromptPreset | None:
        path = self.path(preset_id)
        if not path.exists():
            return None
        return PromptPreset.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, preset: PromptPreset) -> PromptPreset:
        existing = self.get(preset.id)
        if existing is not None:
            preset.revision = existing.revision + 1
        write_json_atomic(self.path(preset.id), preset, indent=2)
        return preset

    def delete(self, preset_id: str) -> None:
        self.path(preset_id).unlink(missing_ok=True)


def preview_import(raw: bytes) -> dict[str, Any]:
    if len(raw) > 2_000_000:
        raise ValueError("预设文件过大")
    data = _scrub(json.loads(raw), [0])
    if not isinstance(data, dict):
        raise ValueError("预设根对象必须为 JSON 对象")
    if data.get("format") == "mrp.prompt_preset":
        preset = PromptPreset.model_validate(data)
        unknown = {key: value for key, value in data.items() if key not in PromptPreset.model_fields}
        if unknown:
            preset.extensions = {**preset.extensions, "unknown_fields": unknown}
        stored = preset.extensions.get("mrp.import_report")
        report = {key: [str(row) for row in stored.get(key, [])] if isinstance(stored, dict) and isinstance(stored.get(key), list) else []
                  for key in ("converted", "partial", "unsupported")}
        report["converted"] = ["本项目方案", *report["converted"]]
        return {"draft": preset.model_dump(mode="json"), **report, "original_sha256": hashlib.sha256(raw).hexdigest()}
    converted = []
    partial = []
    unsupported = []
    segments: list[PromptSegment] = []
    if isinstance(data.get("prompts"), list):
        order_sets = data.get("prompt_order") if isinstance(data.get("prompt_order"), list) else []
        order_sets = [item for item in order_sets if isinstance(item, dict) and isinstance(item.get("order"), list)]
        selected_order = next((item for item in order_sets if str(item.get("character_id")) == "100000"),
                              order_sets[0] if order_sets else None)
        active_order = {str(item.get("identifier")): (index, item.get("enabled", False))
                        for index, item in enumerate(selected_order["order"] if selected_order else [])
                        if isinstance(item, dict)}
        if selected_order:
            converted.append("prompt_order 中的片段启用状态和顺序")
            if len(order_sets) > 1:
                partial.append(f"多套排序仅采用 character_id={selected_order.get('character_id')}；其他排序保留原文")
        for index, item in enumerate(data["prompts"][:100]):
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            marker = bool(item.get("marker"))
            if not marker and (not isinstance(content, str) or not content.strip()):
                continue
            name = str(item.get("name") or item.get("identifier") or "导入片段")[:120]
            identifier = str(item.get("identifier") or "")
            rank, enabled = active_order.get(identifier, (index, False)) if selected_order else (index, item.get("enabled", True))
            enabled = enabled is True or enabled == 1 or str(enabled).lower() == "true"
            role = str(item.get("role") or "system")
            anchor = "system"
            depth = 0
            order = rank * 10
            unsafe = []
            if marker:
                unsafe.append("外部动态 marker；由本项目自己组装历史、人设与世界书")
            if role != "system":
                unsafe.append(f"独立 {role} 消息身份")
            position = item.get("injection_position", 0)
            if position in (1, "1"):
                anchor = "at_depth"
                try:
                    order = int(item.get("injection_order", 100))
                except (TypeError, ValueError):
                    unsafe.append("无效深度插入顺序")
                try:
                    depth = int(item.get("injection_depth", 4))
                except (TypeError, ValueError):
                    depth = 4
                    unsafe.append("无效插入深度")
                if not 0 <= depth <= 100:
                    unsafe.append("超出支持范围的插入深度")
                    depth = min(100, max(0, depth))
                partial.append(f"{name}：深度 {depth} 转为本项目历史文本插入点，不是独立 system 消息")
            elif position not in (0, "0", None):
                unsafe.append(f"插入位置 {position}")
            if item.get("injection_trigger"):
                unsafe.append("生成类型触发条件")
            content = content if isinstance(content, str) else ""
            unsafe.extend(f"宏 {{{{{macro}}}}}" for macro in unknown_macros(content))
            if len(content) > 100_000:
                unsafe.append("超过片段长度限制（草稿截取，完整原文保留）")
            if unsafe:
                enabled = False
                unsupported.append(f"{name}：{'、'.join(unsafe)} 未执行，已停用；完整片段保留原文")
            elif not enabled:
                converted.append(f"{name}：保持关闭")
            segments.append(PromptSegment(name=name, content=content[:100_000], enabled=enabled,
                                          anchor=anchor, depth=depth, order=min(10_000, max(-10_000, order))))
        converted.append(f"文本片段 {len(segments)} 项")
        partial.append("system 文字指令转为本项目系统上下文区；不保留外部独立消息边界或相对动态 marker 的位置")
        if len(data["prompts"]) > 100:
            unsupported.append("超过前 100 个片段的内容仅保留原文，未启用")
    if not segments and isinstance(data.get("system_prompt"), str):
        macros = unknown_macros(data["system_prompt"])
        enabled = not macros and len(data["system_prompt"]) <= 100_000
        segments.append(PromptSegment(name="系统指令", content=data["system_prompt"][:100_000], enabled=enabled))
        if not enabled:
            unsupported.append("系统指令：未知宏或超出长度，已停用；完整原文保留")
        converted.append("系统指令")
    for key in ("script", "scripts", "regex", "regex_scripts", "extensions"):
        if data.get(key):
            unsupported.append(f"{key}：保留原文，不执行")
    report = {"converted": converted, "partial": partial, "unsupported": unsupported}
    preset = PromptPreset(name=str(data.get("name") or "导入方案")[:160],
                          source="foreign_import", segments=segments,
                          extensions={"foreign_fields": data, "mrp.import_report": report})
    return {"draft": preset.model_dump(mode="json"), "converted": converted,
            "partial": partial, "unsupported": unsupported,
            "original_sha256": hashlib.sha256(raw).hexdigest()}
