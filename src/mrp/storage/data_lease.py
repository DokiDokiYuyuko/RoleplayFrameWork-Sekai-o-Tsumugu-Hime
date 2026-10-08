"""OS advisory lease shared by the server lifespan and offline maintenance."""
from __future__ import annotations

import os
from pathlib import Path


class DataRootBusy(RuntimeError):
    pass


class DataRootLease:
    def __init__(self, data_root: Path, purpose='maintenance'):
        self.path = Path(data_root) / '.story-storage.lease'
        self.purpose = purpose
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        if stream.seek(0, 2) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            stream.close()
            raise DataRootBusy('数据目录正在由服务或维护任务使用；拒绝在线迁移或并行启动。') from exc
        self.stream = stream
        if self.purpose == 'server':
            try:
                self.require_completed_cutovers(self.path.parent)
            except BaseException:
                self.__exit__()
                raise
        return self

    @staticmethod
    def require_completed_cutovers(data_root):
        import sqlite3
        database = Path(data_root) / 'memories' / 'memory.db'
        if not database.exists():
            return
        db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        try:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='migrations'").fetchone():
                pending = db.execute("SELECT id FROM migrations WHERE status IN ('activating','rolling_back') LIMIT 1").fetchone()
                if pending:
                    raise RuntimeError('存储切换未完成，请先运行 story_migration recover；服务不会打开双写路径。')
        finally:
            db.close()

    def __exit__(self, *args):
        if self.stream:
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None
