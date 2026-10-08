from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.maintenance.migrate_data_root import migrate


def test_migration_copies_files_and_is_repeatable(tmp_path: Path) -> None:
    source = tmp_path / "old" / "data"
    target = tmp_path / "new" / "data"
    (source / "characters" / "char-a").mkdir(parents=True)
    (source / "characters" / "char-a" / "card.json").write_text('{"name":"synthetic"}', encoding="utf-8")
    (source / "settings.json").write_text(json.dumps({"api_key": "test-only"}), encoding="utf-8")

    plan = migrate(source, target, plan_only=True)
    assert plan["to_copy"] == 2
    assert not target.exists()

    result = migrate(source, target)
    assert result["copied"] == 2
    assert result["verified"] is True
    assert migrate(source, target)["already_identical"] == 2
    assert (source / "settings.json").exists()


def test_migration_refuses_to_overwrite_conflicting_target(tmp_path: Path) -> None:
    source = tmp_path / "old"
    target = tmp_path / "new"
    source.mkdir()
    target.mkdir()
    (source / "settings.json").write_text('{"value":"source"}', encoding="utf-8")
    (target / "settings.json").write_text('{"value":"target"}', encoding="utf-8")

    with pytest.raises(FileExistsError, match="before copying"):
        migrate(source, target)
    assert (target / "settings.json").read_text(encoding="utf-8") == '{"value":"target"}'


def test_migration_rejects_nested_source_and_target(tmp_path: Path) -> None:
    source = tmp_path / "data"
    source.mkdir()
    with pytest.raises(ValueError, match="separate directories"):
        migrate(source, source / "child")
