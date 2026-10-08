"""Copy a stopped MRP data directory without overwriting different target files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            candidate = current_path / name
            if _is_link(candidate):
                raise ValueError(f"Directory link is not allowed in migration source: {candidate.relative_to(root)}")
        for name in names:
            candidate = current_path / name
            if _is_link(candidate):
                raise ValueError(f"File link is not allowed in migration source: {candidate.relative_to(root)}")
            if not candidate.is_file():
                raise ValueError(f"Unsupported filesystem entry: {candidate.relative_to(root)}")
            files.append(candidate)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix().casefold())


def migrate(source: Path, target: Path, *, plan_only: bool = False) -> dict[str, object]:
    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    if not source.is_dir():
        raise ValueError("Source data directory does not exist.")
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Source and target must be separate directories with no nesting.")

    files = _inventory(source)
    conflicts: list[str] = []
    unchanged: list[Path] = []
    pending: list[Path] = []
    total_bytes = 0
    for file in files:
        relative = file.relative_to(source)
        destination = target / relative
        total_bytes += file.stat().st_size
        if not destination.exists():
            pending.append(file)
        elif destination.is_file() and _sha256(file) == _sha256(destination):
            unchanged.append(file)
        else:
            conflicts.append(relative.as_posix())

    report: dict[str, object] = {
        "source": str(source),
        "target": str(target),
        "files": len(files),
        "bytes": total_bytes,
        "already_identical": len(unchanged),
        "to_copy": len(pending),
        "conflicts": conflicts,
    }
    if conflicts:
        raise FileExistsError(
            f"Migration stopped before copying: {len(conflicts)} target files differ. "
            "Preserve both versions and resolve the conflict explicitly."
        )
    if plan_only:
        return report

    target.mkdir(parents=True, exist_ok=True)
    for source_file in pending:
        relative = source_file.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.migrate-{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source_file, temporary)
            if _sha256(source_file) != _sha256(temporary):
                raise OSError(f"Copy verification failed for {relative.as_posix()}")
            # Recheck immediately before replace in case another process created it.
            if destination.exists():
                if destination.is_file() and _sha256(source_file) == _sha256(destination):
                    temporary.unlink()
                    continue
                raise FileExistsError(f"Target changed during migration: {relative.as_posix()}")
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    # Verify the whole destination subset after the copy. Existing unrelated files are preserved.
    for source_file in files:
        destination = target / source_file.relative_to(source)
        if not destination.is_file() or _sha256(source_file) != _sha256(destination):
            raise OSError(f"Final verification failed for {source_file.relative_to(source).as_posix()}")
    report["copied"] = len(pending)
    report["verified"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely copy private MRP data into a separate data directory.")
    parser.add_argument("source", type=Path, help="Existing private data root")
    parser.add_argument("target", type=Path, help="New private data root, outside the code repository")
    parser.add_argument("--plan", action="store_true", help="Inspect file counts and conflicts without copying")
    args = parser.parse_args()
    try:
        report = migrate(args.source, args.target, plan_only=args.plan)
    except (OSError, ValueError) as exc:
        print(f"Migration stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.plan:
        print("Plan only; no files were copied.")
    else:
        print("Data copied and verified. The source directory was not changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
