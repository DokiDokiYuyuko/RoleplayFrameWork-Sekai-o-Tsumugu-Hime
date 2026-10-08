"""唯一数据根：mrp 的磁盘布局（用户数据目录结构）。"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def default_data_root() -> Path:
    """返回私有数据根；缺省位置在代码仓库之外。"""
    env = os.environ.get("MRP_DATA_ROOT")
    if env and env.strip():
        return validate_data_root(Path(env))
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return validate_data_root(base / "Sekai o Tsumugu Hime" / "data")


def validate_data_root(data_root: Path, code_root: Path | None = None) -> Path:
    """拒绝把运行数据写入代码目录；resolve 同时处理存在的链接/junction。"""
    resolved_data = Path(data_root).expanduser().resolve()
    resolved_code = (code_root or Path(__file__).resolve().parents[3]).resolve()
    try:
        resolved_data.relative_to(resolved_code)
    except ValueError:
        return resolved_data
    raise ValueError("MRP_DATA_ROOT must be outside the code directory; refusing to write private data into the repository.")


def is_safe_name(name: str) -> bool:
    """id → 文件名前的安全检查（防路径穿越：URL 里的 id 直接拼路径是旧代码的隐患）。"""
    return bool(name) and "/" not in name and "\\" not in name and name not in {".", ".."}


@dataclass(frozen=True)
class AppPaths:
    """数据根 + 目录/文件布局。`AppPaths.default()` 复刻 app.py 的 env 兜底。"""

    data_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_root", Path(self.data_root))

    @classmethod
    def default(cls) -> "AppPaths":
        return cls(default_data_root())

    # ---------- 目录 ----------
    @property
    def characters_dir(self) -> Path:
        return self.data_root / "characters"

    @property
    def lorebooks_dir(self) -> Path:
        return self.data_root / "lorebooks"

    @property
    def worlds_dir(self) -> Path:
        return self.data_root / "worlds"

    @property
    def scenarios_dir(self) -> Path:
        return self.data_root / "scenarios"

    @property
    def sessions_dir(self) -> Path:
        return self.data_root / "sessions"

    @property
    def saves_dir(self) -> Path:
        return self.data_root / "saves"

    @property
    def avatars_dir(self) -> Path:
        """头像目录 = characters 目录（`save_avatar_png` 落在 `<characters>/<id>/avatar.png`）。"""
        return self.characters_dir

    @property
    def memory_dir(self) -> Path:
        return self.data_root / "memories"

    @property
    def story_media_dir(self) -> Path:
        return self.data_root / "story_media"

    @property
    def tts_dir(self) -> Path:
        return self.data_root / "tts"

    @property
    def tts_voices_dir(self) -> Path:
        return self.tts_dir / "voices"

    @property
    def tts_cache_dir(self) -> Path:
        return self.tts_dir / "cache"

    # ---------- 文件 ----------
    @property
    def settings_file(self) -> Path:
        return self.data_root / "settings.json"

    @property
    def sessions_index_file(self) -> Path:
        return self.sessions_dir / "index.json"

    @property
    def saves_index_file(self) -> Path:
        return self.saves_dir / "index.json"

    def ensure(self) -> "AppPaths":
        """建齐全部目录（幂等；启动/测试用）。"""
        for d in (
            self.characters_dir,
            self.lorebooks_dir,
            self.worlds_dir,
            self.scenarios_dir,
            self.sessions_dir,
            self.saves_dir,
            self.memory_dir,
            self.tts_voices_dir,
            self.tts_cache_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        return self
