"""One local verification entry point. Only synthetic data; no production service."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def run(command, *, cwd=ROOT, env=None):
    print("Running:", " ".join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="Requires PLAYWRIGHT_MODULE and UI_ACCEPTANCE_OUT outside the code tree")
    args = parser.parse_args()
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise SystemExit("npm is required; use the project's installed dependencies")
    with tempfile.TemporaryDirectory(prefix="mrp-architecture-") as temporary:
        env = {**os.environ, "MRP_DATA_ROOT": temporary, "MRP_FAKE_ENGINE": "1", "PYTHONIOENCODING": "utf-8"}
        run([sys.executable, "tools/maintenance/generate_story_contracts.py", "--check"], env=env)
        run([sys.executable, "-m", "pytest", "-q", "-rs"], env=env)
        run([npm, "run", "test:unit"], cwd=ROOT / "src/web", env=env)
        run([npm, "run", "build"], cwd=ROOT / "src/web", env=env)
        if args.browser:
            run([shutil.which("node") or "node", "tests/uiAcceptance.mjs"], cwd=ROOT / "src/web", env=env)
    print("Architecture verification passed.")


if __name__ == "__main__":
    main()
