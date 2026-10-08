"""Offline private-library archive with verified manifests and empty-root restoration."""
from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from mrp.storage.paths import validate_data_root

FORMAT = "mrp.personal_library"
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_FILE = 512 * 1024 * 1024
CATEGORIES = {
    "characters": "角色与媒体", "lorebooks": "运行世界书", "worlds": "世界及完整档案原稿",
    "scenarios": "场景预设", "sessions": "故事和路线", "saves": "存档", "memories": "记忆及检索数据库",
    "model_requests": "历史模型请求", "usage-ledger": "逐调用用量账本", "story_media": "故事媒体", "simple_chats": "简单聊天",
    "prompt_presets": "提示词方案", "asset_import_jobs": "设定导入原稿和草稿",
    "lorebook_generation_jobs": "世界书创作原稿和草稿", "card_inspiration_jobs": "角色创作原稿和草稿",
    "tts": "语音配置和媒体", "settings.json": "设置（排除保存密钥）",
}
EXCLUDED = ["保存的 API 密钥和 local_credentials.json", "局域网证书私钥与配对令牌", "旧备份、临时事务和可重建索引"]


def _is_link(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _inventory(root):
    result = []
    for name in CATEGORIES:
        path = root / name
        if not path.exists():
            continue
        if _is_link(path):
            raise ValueError("数据中存在链接，不能可靠备份")
        if path.is_file():
            result.append(path)
            continue
        for current, dirs, files in os.walk(path, followlinks=False):
            base = Path(current)
            if any(_is_link(base / x) for x in dirs + files):
                raise ValueError("数据中存在链接，不能可靠备份")
            result.extend(base / x for x in files if not x.endswith(("-wal", "-shm", ".tmp", ".bak")))
    return sorted(result, key=lambda p: p.relative_to(root).as_posix())


def library_checklist(root):
    root = Path(root)
    files = _inventory(root)
    rows = [{"key": name, "label": label,
             "file_count": sum(p.relative_to(root).parts[0] == name for p in files),
             "bytes": sum(p.stat().st_size for p in files if p.relative_to(root).parts[0] == name)}
            for name, label in CATEGORIES.items()]
    return {"format": FORMAT, "categories": rows, "excludes": EXCLUDED, "secrets_included": False,
            "offline_required": True, "restore_policy": "new_empty_data_root",
            "limits": {"total_bytes": MAX_TOTAL, "file_bytes": MAX_FILE, "web_verify_bytes": 200 * 1024 * 1024},
            "steps": ["先停止本项目服务；不要删除 SQLite WAL/SHM 文件。",
                      "用 personal_library.py backup 生成离线私密 ZIP，并用 verify 检查清单和哈希。",
                      "用 restore 恢复到代码目录之外的新空数据根；已有故事目录不会被覆盖。",
                      "核对角色、原稿、故事、记忆、媒体和请求记录后，重新配置密钥并指向新数据根启动。"]}


def _stamp(files):
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in files}


def _consistency_stamp(root):
    files = _inventory(root)
    # WAL content is materialized by SQLite backup, never archived or removed.
    # Its stamp detects another writer even while the main database is unchanged.
    wal_files = [p.with_name(p.name + "-wal") for p in files
                 if p.suffix.lower() in {".db", ".sqlite", ".sqlite3"}]
    return _stamp(files + [p for p in wal_files if p.exists()])


def _settings(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("设置文件无法可靠解析") from exc
    if not isinstance(value, dict):
        raise ValueError("设置文件不是 JSON 对象")
    # Settings secrets are the only altered payload. Story/evidence fingerprints stay intact.
    def clean(item):
        if isinstance(item, dict):
            return {k: clean(v) for k, v in item.items() if k.lower() not in {
                "api_key", "provider_api_keys", "apikey", "password", "authorization", "access_token",
                "refresh_token", "secret", "token", "modelscope_token"}}
        if isinstance(item, list):
            return [clean(x) for x in item]
        return item
    try:
        return json.dumps(clean(value), ensure_ascii=False, separators=(",", ":")).encode()
    except RecursionError as exc:
        raise ValueError("设置文件嵌套过深") from exc


def _read_member(archive, name):
    try:
        return archive.read(name)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, ValueError) as exc:
        raise ValueError("备份成员损坏、加密或使用不支持的压缩方式") from exc


def backup_library(root: Path, output: Path, *, offline_confirmed: bool):
    if not offline_confirmed:
        raise ValueError("完整私密库备份需要先停止服务并确认离线")
    root, output = validate_data_root(root), validate_data_root(output)
    if not root.is_dir() or output == root or root in output.parents:
        raise ValueError("备份源必须存在，备份文件必须位于源数据根之外")
    if output.exists():
        raise FileExistsError("备份文件已存在，不覆盖")
    files = _inventory(root)
    before = _consistency_stamp(root)
    manifest = {"format": FORMAT, "format_version": 1, "secrets_included": False,
                "excludes": EXCLUDED, "files": [], "restore_policy": "new_empty_data_root"}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mrp-library-", dir=output.parent) as tmp:
        archive_path = Path(tmp) / "library.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            total = 0
            for index, path in enumerate(files):
                relative = path.relative_to(root).as_posix()
                if path.stat().st_size > MAX_FILE:
                    raise ValueError("私密库超过单文件 512 MB 的备份限制")
                if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                    target = Path(tmp) / f"database-{index}.db"
                    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(target)) as destination:
                        source.backup(destination)
                        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError("数据库完整性检查失败")
                    if target.stat().st_size > MAX_FILE:
                        raise ValueError("一致性数据库快照超过单文件 512 MB 的备份限制")
                    payload = target.read_bytes()
                else:
                    payload = path.read_bytes()
                if relative == "settings.json":
                    payload = _settings(payload)
                total += len(payload)
                if len(payload) > MAX_FILE or total > MAX_TOTAL:
                    raise ValueError("私密库超过备份限制（单文件 512 MB、总量 2 GB）")
                archive.writestr(relative, payload)
                manifest["files"].append({"path": relative, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        if _consistency_stamp(root) != before:
            raise RuntimeError("备份期间源数据有变化，未发布备份；请确认服务已停止")
        verify_library(archive_path.read_bytes())
        # Hard link is exclusive; concurrent output creation cannot be overwritten.
        os.link(archive_path, output)
    return {"ok": True, "file_count": len(manifest["files"]), "bytes": total,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "secrets_included": False}


def verify_library(raw: bytes):
    if len(raw) > MAX_TOTAL:
        raise ValueError("备份压缩文件超过大小限制")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("不是有效的私密库 ZIP") from exc
    with archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(infos) > 100_000 or len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("清单缺失、文件重复或条目过多")
        if sum(i.file_size for i in infos) > MAX_TOTAL or any(i.file_size > MAX_FILE for i in infos):
            raise ValueError("备份解压大小超过限制")
        if archive.getinfo("manifest.json").file_size > 20 * 1024 * 1024:
            raise ValueError("备份清单过大")
        try:
            manifest = json.loads(_read_member(archive, "manifest.json"))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("备份清单无效") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or manifest.get("format_version") != 1 or manifest.get("secrets_included") is not False:
            raise ValueError("私密库格式或密钥策略不支持")
        entries = manifest.get("files")
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise ValueError("备份文件清单无效")
        if any(type(e.get("bytes")) is not int or not 0 <= e["bytes"] <= MAX_FILE
               or not isinstance(e.get("sha256"), str) or len(e["sha256"]) != 64 for e in entries):
            raise ValueError("备份清单缺少可靠的大小或哈希")
        paths = [e.get("path") for e in entries]
        if any(not isinstance(p, str) for p in paths) or len(paths) != len(set(paths)) or set(names) != set(paths) | {"manifest.json"}:
            raise ValueError("清单和归档内容不一致")
        path_keys = {p.casefold() for p in paths}
        canonical_paths = set()
        for item in entries:
            name = item["path"]
            parts = PurePosixPath(name).parts
            if not parts or name.startswith("/") or "\\" in name or ":" in name or any(x in {"..", "."} for x in parts) or parts[0] not in CATEGORIES:
                raise ValueError("备份路径不在允许的数据目录内")
            if parts[0] == "settings.json" and len(parts) != 1:
                raise ValueError("设置只能是根目录 JSON 文件")
            if name != PurePosixPath(name).as_posix() or archive.getinfo(name).is_dir():
                raise ValueError("备份路径不是规范文件路径")
            if any(part.rstrip(" .") != part or any(ord(c) < 32 or c in '<>\"|?*' for c in part)
                   or part.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}
                   for part in parts):
                raise ValueError("备份含跨平台不安全的文件名")
            canonical = name.casefold()
            if canonical in canonical_paths:
                raise ValueError("备份包含大小写冲突的路径")
            canonical_paths.add(canonical)
            if any(parent.as_posix().casefold() in path_keys
                   for parent in PurePosixPath(name).parents if parent.as_posix() != "."):
                raise ValueError("备份文件路径与父目录冲突")
            payload = _read_member(archive, name)
            if len(payload) != item.get("bytes") or hashlib.sha256(payload).hexdigest() != item.get("sha256"):
                raise ValueError("备份文件哈希或长度不一致")
            if name == "settings.json" and _settings(payload) != payload:
                # Compare structure, not formatting, for archives made by compatible tools.
                if json.loads(_settings(payload)) != json.loads(payload):
                    raise ValueError("备份包含被排除的设置密钥")
        return {"ok": True, "format": FORMAT, "file_count": len(entries),
                "bytes": sum(e["bytes"] for e in entries), "sha256": hashlib.sha256(raw).hexdigest(),
                "secrets_included": False, "restore_policy": "new_empty_data_root",
                "categories": sorted({PurePosixPath(x).parts[0] for x in paths})}


def restore_library(raw: bytes, target: Path, *, offline_confirmed: bool):
    if not offline_confirmed:
        raise ValueError("恢复需要先停止服务并确认离线")
    report = verify_library(raw)  # Every hash is verified before any target mutation.
    original_target = Path(target).expanduser().absolute()
    if _is_link(original_target) or any(_is_link(p) for p in original_target.parents):
        raise ValueError("恢复目标不能是链接或包含链接父目录")
    target = validate_data_root(original_target)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise FileExistsError("恢复仅允许新建或空数据根；不会覆盖已有故事")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mrp-restore-", dir=target.parent) as tmp:
        staged = Path(tmp) / "data"
        staged.mkdir()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for item in manifest["files"]:
                destination = staged / item["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(item["path"]))
        if target.exists():
            # rmdir fails if a concurrent story was written: never replace that directory.
            target.rmdir()
        if target.exists():
            raise FileExistsError("恢复目标在操作期间已改变")
        # Windows rename is exclusive. On POSIX explicitly reserve the target first,
        # then move each member with exclusive creation to avoid replacing a new story.
        target.mkdir(exist_ok=False)
        try:
            for path in sorted(staged.rglob("*")):
                if path.is_file():
                    destination = target / path.relative_to(staged)
                    if _is_link(target) or any(_is_link(parent) for parent in destination.parents):
                        raise RuntimeError("恢复目标出现链接，已停止写入")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("xb") as stream:
                        stream.write(path.read_bytes())
        except Exception:
            # Preserve partial restoration for inspection; never recursively delete a target
            # which another process could have started using.
            raise RuntimeError("恢复未完成，已保留目标供核查；请使用另一个新空目标重试")
    return {**report, "restored": True, "keys_need_configuration": True}
