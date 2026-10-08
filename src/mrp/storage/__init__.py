"""mrp 存储层：文件系统布局 + 原子写 + 索引化仓储（cephfs 友好）。

对外只暴露数据根、三类仓储与原子写工具；不引入任何对 server/app.py 的依赖，
便于波 2 逐点接线（app.py 的 DIR_*/直写逻辑 → 本包）。
"""
from __future__ import annotations

from .atomic import read_json, write_json_atomic, write_text_atomic
from .paths import AppPaths, default_data_root, validate_data_root
from .registry import CharacterRegistry, LorebookRegistry, WorldRegistry
from .save_repo import SaveRepo, SaveSummary
from .session_repo import SessionRepo, SessionSummary

__all__ = [
    "AppPaths",
    "CharacterRegistry",
    "LorebookRegistry",
    "WorldRegistry",
    "SaveRepo",
    "SaveSummary",
    "SessionRepo",
    "SessionSummary",
    "default_data_root",
    "validate_data_root",
    "read_json",
    "write_json_atomic",
    "write_text_atomic",
]
