"""原子 JSON 读写（cephfs 安全：同目录 tmp + os.replace）。

- 写：先落同目录 `.<name>.<rand>.tmp`（dot 前缀 → 不会被 `*.json` glob 扫到），
  再 `os.replace` 覆盖目标。任何异常都不留半文件、不破坏旧内容。
- 读：坏文件/编码错/权限错一律返回 `default`，绝不抛（调用方决定语义）。
- 序列化与旧代码逐字节对齐：pydantic 模型走 `model_dump_json(indent)`，
  dict 走 `json.dumps(..., ensure_ascii=False)`（与 app.py 迁移回写一致）。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger("mrp.storage.atomic")


def _json_default(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"不可序列化类型: {type(obj).__name__}")


def json_text(obj: Any, *, indent: int | None = 2, separators: tuple[str, str] | None = None) -> str:
    """统一序列化入口（模型/数据结构）。`separators` 仅对 dict 生效（索引紧凑化）。"""
    if isinstance(obj, BaseModel):
        return obj.model_dump_json(indent=indent)
    return json.dumps(
        obj, ensure_ascii=False, indent=indent, separators=separators, default=_json_default
    )


def write_text_atomic(path: Path, text: str, *, fsync: bool = False) -> None:
    """原子写文本。失败时清理 tmp，目标文件保持原样。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def write_json_atomic(
    path: Path,
    obj: Any,
    *,
    indent: int | None = 2,
    separators: tuple[str, str] | None = None,
    fsync: bool = False,
) -> None:
    """原子写 JSON。`indent=None` → 与现有 data/*.json 相同的紧凑格式。"""
    write_text_atomic(path, json_text(obj, indent=indent, separators=separators), fsync=fsync)


def read_json(path: Path, default: Any = None) -> Any:
    """读 JSON；缺失/损坏/编码错 → default（不抛）。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return default


def read_text(path: Path) -> str | None:
    """读文本；失败 → None。"""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def unlink_missing_ok(path: Path) -> bool:
    """删除文件；不存在/不可删 → False（不抛）。"""
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False
