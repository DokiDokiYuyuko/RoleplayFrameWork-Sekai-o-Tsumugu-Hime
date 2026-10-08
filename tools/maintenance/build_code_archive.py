"""Create a reviewable code-only archive; never includes local application data."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import zipfile

from code_manifest import source_files, publication_bytes
from workspace_paths import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    files = source_files()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output = args.output or PROJECT_ROOT / "tmp" / "runs" / "code-export" / stamp / "织界之姬代码.zip"
    if output.exists():
        raise SystemExit("Output already exists; choose a new archive path.")
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    try:
        with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
            for name in files:
                content = publication_bytes(name, files)
                archive.writestr(name, content)
                records.append({"path": name, "sha256": hashlib.sha256(content).hexdigest()})
            archive.writestr("publication-manifest.json", json.dumps({"scope": "code-and-user-guides", "files": records}, ensure_ascii=False, indent=2))
    except Exception:
        output.unlink(missing_ok=True)
        raise
    print(f"Code archive: {output}; {len(records)} files. Local stories and credentials are excluded.")


if __name__ == "__main__":
    main()
