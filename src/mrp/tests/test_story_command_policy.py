"""New story mutation endpoints must declare their owner and commit protocol."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_story_mutation_route_has_an_explicit_protocol():
    policy = json.loads((ROOT / "contracts/story-command-policy.json").read_text(encoding="utf-8"))
    assert policy["version"] == 2
    protocols = {"branch-command", "entity-publication", "generation-operation", "checkpoint-command",
                 "player-switch", "temporary-generation", "preview", "memory-command", "memory-job", "story-lifecycle"}
    registered = {}
    for row in policy["routes"]:
        identity = (row["method"], row["path"])
        assert identity not in registered, f"duplicate command policy: {identity}"
        assert row["protocol"] in protocols and row["rationale"].strip()
        registered[identity] = row["handler"]
    actual = {}
    for file in (ROOT / "server/routers").glob("*.py"):
        for node in ast.parse(file.read_text(encoding="utf-8-sig")).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr in {"post", "patch", "delete"}
                        and decorator.args and isinstance(decorator.args[0], ast.Constant)):
                    continue
                path = decorator.args[0].value
                if not (file.stem == "memory" or (file.stem == "scenarios" and node.name == "start_scenario") or any(path.startswith("/api/v1/" + prefix)
                        for prefix in ("sessions", "branches", "stories", "saves", "chat-import"))):
                    continue
                identity = (decorator.func.attr.upper(), path)
                assert identity not in actual, f"duplicate route: {identity}"
                actual[identity] = f"{file.stem}.{node.name}"
    assert actual == registered, "Story mutation route changed: update the reviewed protocol policy and acceptance tests."


def test_branch_commands_use_the_reviewed_commit_adapter():
    policy = json.loads((ROOT / "contracts/story-command-policy.json").read_text(encoding="utf-8"))
    for row in policy["routes"]:
        if row["protocol"] != "branch-command":
            continue
        module, handler = row["handler"].split(".")
        tree = ast.parse((ROOT / f"server/routers/{module}.py").read_text(encoding="utf-8-sig"))
        function = next(node for node in tree.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == handler)
        calls = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                 for node in ast.walk(function) if isinstance(node, ast.Call)
                 and isinstance(node.func, (ast.Name, ast.Attribute))}
        assert calls & {"execute_command", "_branch_command"}, row["handler"]
        assert not calls & {"persist", "persist_session", "save_session", "save_session_state"}, row["handler"]
    worldlines = ast.parse((ROOT / "server/routers/worldlines.py").read_text(encoding="utf-8-sig"))
    adapter = next(node for node in worldlines.body if isinstance(node, ast.AsyncFunctionDef)
                   and node.name == "_branch_command")
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "execute_command" for node in ast.walk(adapter))


def test_memory_and_lifecycle_routes_keep_transaction_ownership():
    policy = json.loads((ROOT / "contracts/story-command-policy.json").read_text(encoding="utf-8"))
    owners = {"memory-command": "execute_command", "memory-job": "consolidate", "story-lifecycle": "execute_lifecycle"}
    exceptions = set()
    for row in policy["routes"]:
        if row["protocol"] not in owners:
            continue
        module, handler = row["handler"].split(".")
        tree = ast.parse((ROOT / f"server/routers/{module}.py").read_text(encoding="utf-8-sig"))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == handler)
        calls = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                 for node in ast.walk(function) if isinstance(node, ast.Call)
                 and isinstance(node.func, (ast.Name, ast.Attribute))}
        assert owners[row["protocol"]] in calls, row["handler"]
        assert not calls & {"commit", "save_state", "delete_record", "update_record", "persist_session"}, row["handler"]
        if row.get("compatibility_exception"):
            exceptions.add(row["handler"])
    assert exceptions == {"memory.update_memory", "memory.delete_memory"}
