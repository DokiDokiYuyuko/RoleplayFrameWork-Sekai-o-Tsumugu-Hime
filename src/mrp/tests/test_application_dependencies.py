"""Executable dependency direction for every application use case and port."""
import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULES = sorted(path.stem for path in (ROOT / "application").glob("*.py"))
FORBIDDEN = ("mrp.server", "mrp.storage", "mrp.engines", "mrp.orchestrator", "mrp.llm",
             "fastapi", "starlette", "sqlite3", "httpx", "aiohttp", "requests", "pathlib", "os")


@pytest.mark.parametrize("module", MODULES)
def test_application_depends_only_on_policies_contracts_and_explicit_ports(module):
    tree = ast.parse((ROOT / "application" / (module + ".py")).read_text(encoding="utf-8"))
    imports = []
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = resolve_name("." * node.level + (node.module or ""), "mrp.application") if node.level else node.module or ""
            imports.append(base)
            imports.extend(base + "." + alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute) and node.attr in {"container", "story_db", "_conn", "_connection"}:
            violations.append(f"line {node.lineno}: infrastructure attribute {node.attr}")
        elif isinstance(node, ast.arg) and node.arg == "container":
            violations.append(f"line {node.lineno}: inject a narrow port instead of a container")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"open", "__import__"}:
            violations.append(f"line {node.lineno}: direct IO/dynamic import")
    violations.extend(name for name in imports if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN))
    assert not violations, "\n".join(violations)
