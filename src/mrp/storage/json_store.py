"""目录扫描 / 批量读 / 文件戳工具（index 惰性失效的数据源）。

ceph-fuse3 实测（200 个小 JSON 文件，best-of-3）：
- glob+stat  串行 81ms → 8 线程 30ms（2.7x），16/32 线程不再明显更好
- glob+read  串行 153ms → 8 线程 66ms（2.3x），32 线程 53ms（收益递减）
结论：用 8 线程小池（`IO_CONCURRENCY`），放在**一个** `asyncio.to_thread` 里执行，
既不堵事件循环，也不占满 asyncio 默认执行器；并发度可按需调。

`FileStamp` 同时保 mtime 浮点（API 输出 parity）与 mtime_ns（精确比较，绕开浮点/秒级粒度）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .atomic import read_text

IO_CONCURRENCY = 8


@dataclass(frozen=True)
class FileStamp:
    mtime: float
    mtime_ns: int
    size: int

    @classmethod
    def of(cls, path: Path) -> "FileStamp | None":
        try:
            st = Path(path).stat()
        except OSError:
            return None
        return cls(mtime=st.st_mtime, mtime_ns=st.st_mtime_ns, size=st.st_size)

    def matches(self, mtime_ns: int, size: int) -> bool:
        return self.mtime_ns == mtime_ns and self.size == size


def _pool(n: int, concurrency: int) -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=max(1, min(concurrency, n)))


def scan_json_files_sync(
    directory: Path,
    *,
    skip: Iterable[str] = ("index.json",),
    concurrency: int = IO_CONCURRENCY,
) -> list[tuple[Path, FileStamp]]:
    """`*.json` 文件 + 文件戳（一次 stat/文件，并发）；stat 失败的文件直接跳过。"""
    skip_set = set(skip)
    try:
        paths = sorted(p for p in Path(directory).glob("*.json") if p.name not in skip_set)
    except OSError:
        return []
    if not paths:
        return []
    with _pool(len(paths), concurrency) as ex:
        stamps = list(ex.map(FileStamp.of, paths))
    return [(p, s) for p, s in zip(paths, stamps) if s is not None]


def read_text_many_sync(
    paths: Iterable[Path], *, concurrency: int = IO_CONCURRENCY
) -> list[str | None]:
    """并发读文本；单个失败 → None（与入参等长、同序）。"""
    plist = list(paths)
    if not plist:
        return []
    with _pool(len(plist), concurrency) as ex:
        return list(ex.map(read_text, plist))


def raw_schema_version(raw: Any) -> int:
    """从磁盘 dict 推断 schema 版本（session 顶层；save 顶层或 state 内）。"""
    if not isinstance(raw, dict):
        return 1
    v = raw.get("schema_version")
    if isinstance(v, int):
        return v
    state = raw.get("state")
    if isinstance(state, dict) and isinstance(state.get("schema_version"), int):
        return state["schema_version"]
    return 1
