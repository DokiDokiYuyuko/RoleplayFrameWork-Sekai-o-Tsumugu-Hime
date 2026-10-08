"""stdio entry point for the task-bound read-only MCP server."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mrp.lorebook_generation.tools import main


if __name__ == "__main__":
    main()
