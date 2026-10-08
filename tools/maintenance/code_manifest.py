"""Explicit code publication boundary. No Git metadata or live data required."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from urllib.parse import unquote
from pathlib import Path

from workspace_paths import PROJECT_ROOT

ROOT_FILES = {
    "README.md", "LICENSE", "pyproject.toml", "uv.lock", "requirements-lock.txt", ".gitignore", "run.sh",
    "启动织界之姬.bat", "启动酒馆.bat", "手机访问.bat", "停止服务.bat",
    "start_lan.bat", "stop_server.bat", "disable_lan_firewall.ps1",
    ".agents/skills/character-card-art/SKILL.md",
}
SOURCE_TREES = ("src/mrp", "src/web", "tools", "docs", "third_party")
EXCLUDED_PARTS = {
    "node_modules", "dist", "__pycache__", ".pytest_cache", ".playwright-cli", ".git",
    ".player-switch-acceptance-dist",
}
EXCLUDED_FILES = {
    'src/web/src/appearance/README.md',
    'src/mrp/asset_import/skills/README.md',
    'tools/maintenance/upload_modelscope.py',
    'tools/maintenance/download_modelscope.py',
    'tools/maintenance/verify_modelscope.py',
}
# Stored test data requires an independently reviewed synthetic origin and pinned bytes.
# Only CRLF -> LF is normalized so Git checkout policy cannot alter approval.
SYNTHETIC_FIXTURE_HASHES = {
    'src/mrp/tests/fixtures/synthetic-session-v1.json': '91a031616b35382471c28a0307bddb574eb04690f345ddc39a9e355cd9df380b',
}
TEXT_SUFFIXES = {".py", ".ps1", ".bat", ".sh", ".md", ".json", ".ts", ".mts", ".tsx", ".js", ".mjs", ".css", ".toml", ".lock", ".html", ".yml", ".yaml", ".svg", ".txt"}
SECRET = re.compile(r"(?:sk-(?:or-v1-)?[A-Za-z0-9_-]{24,}|ms-[0-9a-f]{8}-[0-9a-f-]{27,})")
PRIVATE_PATH = re.compile(
    r"(?i)(?:[A-Z]:\\Users\\[^\\\s]+|/" + "home/" + r"[^/\s]+|/" + "Users/" + r"[^/\s]+)"
)
SENSITIVE_TOKENS = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[A-Za-z0-9_-]{35}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|Bearer\s+[A-Za-z0-9._~+/-]{24,})"
)
ALLOWED_BINARY = {
    "src/web/public/fonts/huangyou.ttf",
    "src/web/public/fonts/mashan.ttf",
    "src/web/public/fonts/wenkai.ttf",
    "src/web/public/fonts/xiaowei.ttf",
    "src/web/public/brand/sekai-o-tsumugu-hime-emblem.png",
    "src/web/public/brand/sekai-o-tsumugu-hime-wordmark.png",
    "src/web/public/brand/themes/astral/story-forest.jpg",
    "src/web/public/brand/themes/astral/story-moon.jpg",
    "src/web/public/brand/themes/astral/story-ocean.jpg",
    "src/web/public/brand/themes/astral/sidebar-palace.jpg",
}


def source_files(root: Path = PROJECT_ROOT) -> list[str]:
    root = root.resolve()
    import hashlib
    reviewed = root / 'third_party' / 'public-assets.json'
    allowed_binary = set(ALLOWED_BINARY)
    if reviewed.is_file():
        for asset in json.loads(reviewed.read_text(encoding='utf-8')):
            relative = asset['file']
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f'Reviewed asset is missing or escapes the project: {relative}')
            if hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
                raise ValueError(f'Reviewed asset has changed: {relative}')
            allowed_binary.add(relative)
    paths = {root / name for name in ROOT_FILES if (root / name).is_file()}
    paths.add(root / "third_party" / "README.md")
    paths.add(root / "third_party" / "fonts.json")
    import os
    for tree in SOURCE_TREES:
        for current, dirs, files in os.walk(root / tree, followlinks=False):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_PARTS and not d.startswith(".lan-dist-")
                       and not (Path(current) / d).is_symlink()
                       and not getattr(Path(current) / d, "is_junction", lambda: False)()]
            for name in files:
                path = Path(current) / name
                if name == ".gitkeep" or name.startswith(".env") or name.endswith((".log", ".local", ".pyc")) or path.is_symlink():
                    continue
                paths.add(path)
    result = []
    for path in sorted(paths):
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError("Publication path escapes the project.")
        rel = path.relative_to(root).as_posix()
        if rel in EXCLUDED_FILES:
            continue
        if '/tests/' in rel and path.suffix.lower() in {'.json', '.yaml', '.yml'}:
            expected = SYNTHETIC_FIXTURE_HASHES.get(rel)
            fixture_bytes = path.read_bytes().replace(b'\r\n', b'\n')
            if not expected or hashlib.sha256(fixture_bytes).hexdigest() != expected:
                raise ValueError(f'Unreviewed or changed test-data fixture; publication stopped: {rel}')
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {'.gitignore', 'LICENSE'}:
            content = path.read_text(encoding="utf-8-sig", errors="replace")
            if SECRET.search(content) or SENSITIVE_TOKENS.search(content):
                raise ValueError(f"Possible embedded credential; publication stopped: {rel}")
            if PRIVATE_PATH.search(content):
                raise ValueError(f"Possible personal absolute path; publication stopped: {rel}")
        elif rel not in allowed_binary:
            raise ValueError(f"Unreviewed binary asset; publication stopped: {rel}")
        result.append(rel)
    return result


def audit_git_state(root: Path, allowed: set[str]) -> list[str]:
    git_dir = root / ".git"
    if not git_dir.exists():
        return []
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=False,
    )
    if tracked.returncode != 0:
        raise RuntimeError("Unable to inspect Git file list; publication audit stopped.")
    findings: list[str] = []
    for raw_name in tracked.stdout.split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode("utf-8", errors="replace").replace("\\", "/")
        if name not in allowed:
            findings.append(f"Git contains a file outside the publication allowlist: {name}")

    revisions = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--objects", "--all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if revisions.returncode != 0:
        raise RuntimeError("Unable to inspect Git history; publication audit stopped.")
    objects: dict[str, str] = {}
    for line in revisions.stdout.splitlines():
        parts = line.split(" ", 1)
        if parts:
            objects[parts[0]] = parts[1] if len(parts) > 1 else "<unknown path>"
    if objects:
        process = subprocess.Popen(
            ["git", "-C", str(root), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None and process.stdout is not None
        for object_id, object_path in objects.items():
            process.stdin.write(object_id.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().decode("ascii", errors="replace").strip().split()
            if len(header) != 3:
                continue
            size = int(header[2])
            blob = process.stdout.read(size)
            process.stdout.read(1)
            if header[1] != "blob":
                continue
            content = blob.decode("utf-8", errors="replace")
            if SECRET.search(content) or SENSITIVE_TOKENS.search(content):
                findings.append(f"Git history contains a possible credential in {object_path}.")
            if PRIVATE_PATH.search(content):
                findings.append(f"Git history contains a possible personal path in {object_path}.")
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError("Unable to scan Git history; publication audit stopped.")
    return findings


def publication_bytes(name: str, files: list[str]) -> bytes:
    """Omit clickable links to private local documents from exported Markdown."""
    path = PROJECT_ROOT / name
    data = path.read_bytes()
    if path.suffix != ".md":
        return data
    included = set(files)
    text = data.decode("utf-8-sig")
    def local_link(match):
        label, target = match.group(1), match.group(2).strip("<>")
        target = unquote(target.split("#", 1)[0])
        if not target or "://" in target or target.startswith("mailto:"):
            return match.group(0)
        resolved = (path.parent / target).resolve()
        if not resolved.is_relative_to(PROJECT_ROOT):
            return label + "（本地工作区资料）"
        rel = resolved.relative_to(PROJECT_ROOT).as_posix()
        if rel not in included and not any(item.startswith(rel.rstrip("/") + "/") for item in included):
            return label + "（本地工作区资料，代码包不包含）"
        return match.group(0)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", local_link, text).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="List code files eligible for publication; no network access.")
    parser.add_argument("--output", type=Path, help="Write the reviewable JSON manifest to a local file.")
    parser.add_argument("--audit", action="store_true", help="Run credential, personal-path, binary, and Git-state checks.")
    args = parser.parse_args()
    try:
        files = source_files()
        findings = audit_git_state(PROJECT_ROOT.resolve(), set(files)) if args.audit else []
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    if findings:
        for finding in findings:
            print(f"BLOCKED: {finding}")
        raise SystemExit(2)
    if args.audit:
        if not (PROJECT_ROOT / ".git").exists():
            print(f"Static privacy scan passed for {len(files)} allowlisted files.")
            print("Publication check incomplete: Git working files and history cannot be checked until this code folder is initialized.")
        else:
            print(f"Privacy and Git-history audit passed for {len(files)} allowlisted files.")
        return
    payload = {"scope": "source-and-public-guides", "files": files,
               "excluded": ["data", "backups", "tmp", "external_data", "research", "document", ".agents (except .agents/skills/character-card-art/SKILL.md)", ".claude", "AGENTS.md", "CLAUDE.md", ".git", ".venv", "node_modules", "dist"]}
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Manifest written: {len(files)} files.")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
