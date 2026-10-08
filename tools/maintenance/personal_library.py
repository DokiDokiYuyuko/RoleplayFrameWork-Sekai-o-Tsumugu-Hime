"""Offline backup/verify/restore. No default data root: paths must be explicit."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from mrp.storage.personal_library import backup_library, restore_library, verify_library


def require_stopped():
    if os.name == "nt":
        command = "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '(?i)(-m\\s+mrp\\.server\\.main|uvicorn\\s+mrp\\.server)' } | Select-Object -ExpandProperty ProcessId"
        result = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=20)
        if result.returncode or result.stdout.strip():
            raise ValueError("无法确认服务已停止，或检测到本项目服务进程；请先停止服务")
    elif Path("/proc").exists():
        for path in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                command = path.read_bytes().replace(b"\x00", b" ")
            except (OSError, PermissionError):
                continue
            if b"mrp.server.main" in command or b"uvicorn mrp.server" in command:
                raise ValueError("检测到本项目服务进程；请先停止服务")
    else:
        raise ValueError("当前平台无法自动确认离线状态")


def main():
    parser = argparse.ArgumentParser(description="私密库离线备份/校验/恢复；不覆盖已有文件或故事")
    parser.add_argument("action", choices=["backup", "verify", "restore"])
    parser.add_argument("--source", type=Path, help="backup: 显式源数据根；verify/restore: ZIP 文件")
    parser.add_argument("--target", type=Path, help="backup: 新 ZIP 文件；restore: 新空数据根")
    args = parser.parse_args()
    try:
        if args.source is None or (args.action != "verify" and args.target is None):
            raise ValueError("请提供显式 --source，以及备份/恢复的 --target")
        if args.action != "verify":
            require_stopped()
        if args.action == "backup":
            report = backup_library(args.source, args.target, offline_confirmed=True)
        elif args.action == "verify":
            report = verify_library(args.source.read_bytes())
        else:
            report = restore_library(args.source.read_bytes(), args.target, offline_confirmed=True)
        print(json.dumps(report, ensure_ascii=True))
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
