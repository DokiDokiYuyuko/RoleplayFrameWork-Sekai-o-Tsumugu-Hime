from __future__ import annotations

from pathlib import Path
import subprocess
import hashlib
import json

import pytest

from code_manifest import audit_git_state, source_files


def _minimal_code_tree(root: Path) -> None:
    (root / "src" / "mrp").mkdir(parents=True)
    (root / "src" / "mrp" / "app.py").write_text("print('synthetic')\n", encoding="utf-8")
    (root / "tools").mkdir()
    (root / "document" / "guide").mkdir(parents=True)
    (root / "document" / "guide" / "guide.md").write_text("Public help.\n", encoding="utf-8")
    (root / "third_party").mkdir()
    (root / "third_party" / "README.md").write_text("Dependency notes.\n", encoding="utf-8")


def test_manifest_accepts_synthetic_public_source(tmp_path: Path) -> None:
    _minimal_code_tree(tmp_path)
    assert "src/mrp/app.py" in source_files(tmp_path)


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("personal.md", "Local file: C:\\Users\\someone\\story.json\n"),
    ],
)
def test_manifest_rejects_secret_or_user_path(tmp_path: Path, filename: str, content: str) -> None:
    _minimal_code_tree(tmp_path)
    (tmp_path / "src" / "mrp" / filename).write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="publication stopped"):
        source_files(tmp_path)


def test_manifest_rejects_synthetic_credential(tmp_path: Path) -> None:
    _minimal_code_tree(tmp_path)
    fake_key = "sk-" + "or-v1-" + "a" * 40
    (tmp_path / "src" / "mrp" / "credentials.py").write_text(f"TOKEN = {fake_key!r}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="publication stopped"):
        source_files(tmp_path)


def test_manifest_rejects_unreviewed_binary(tmp_path: Path) -> None:
    _minimal_code_tree(tmp_path)
    (tmp_path / "src" / "mrp" / "private.png").write_bytes(b"synthetic binary")
    with pytest.raises(ValueError, match="Unreviewed binary"):
        source_files(tmp_path)


def test_reviewed_public_assets_are_hash_pinned(tmp_path: Path) -> None:
    _minimal_code_tree(tmp_path)
    public = tmp_path / 'src/web/public'
    public.mkdir(parents=True)
    image = public / 'approved.png'
    image.write_bytes(b'synthetic public asset')
    registry = tmp_path / 'third_party/public-assets.json'
    registry.write_text(json.dumps([{'file':'src/web/public/approved.png','sha256':hashlib.sha256(image.read_bytes()).hexdigest()}]), encoding='utf-8')
    assert 'src/web/public/approved.png' in source_files(tmp_path)
    image.write_bytes(b'changed synthetic asset')
    with pytest.raises(ValueError, match='Reviewed asset has changed'):
        source_files(tmp_path)


def test_development_documents_and_private_runtime_are_excluded(tmp_path: Path) -> None:
    _minimal_code_tree(tmp_path)
    for name in ['CLAUDE.md', 'AGENTS.md', 'document/verification/report.md', '.agents/skills/internal/SKILL.md', 'data/story.json']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Synthetic local-only content', encoding='utf-8')
    files = set(source_files(tmp_path))
    assert not any(name.startswith(('document/', '.agents/', 'data/')) for name in files)
    assert 'CLAUDE.md' not in files and 'AGENTS.md' not in files


def test_only_the_user_character_art_skill_is_publishable(tmp_path: Path) -> None:
    _minimal_code_tree(tmp_path)
    approved = '.agents/skills/character-card-art/SKILL.md'
    extras = ['.agents/skills/character-card-art/private-notes.md',
              '.agents/skills/character-card-art/agents/openai.yaml',
              '.agents/skills/developer-quality/SKILL.md',
              '.claude/skills/character-card-art/SKILL.md']
    for name in [approved, *extras]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('Synthetic skill content', encoding='utf-8')
    files = set(source_files(tmp_path))
    assert {name for name in files if name.startswith(('.agents/', '.claude/'))} == {approved}


def test_user_skill_still_obeys_privacy_scan(tmp_path: Path) -> None:
    _minimal_code_tree(tmp_path)
    skill = tmp_path / '.agents/skills/character-card-art/SKILL.md'
    skill.parent.mkdir(parents=True)
    fake_key = 'sk-' + 'or-v1-' + 'a' * 40
    skill.write_text('Synthetic token ' + fake_key, encoding='utf-8')
    with pytest.raises(ValueError, match='publication stopped'):
        source_files(tmp_path)


def test_stored_test_data_requires_reviewed_synthetic_origin(tmp_path: Path) -> None:
    _minimal_code_tree(tmp_path)
    fixture = tmp_path / 'src/mrp/tests/fixtures/unreviewed.json'
    fixture.parent.mkdir(parents=True)
    fixture.write_text('{"messages":[]}', encoding='utf-8')
    with pytest.raises(ValueError, match='test-data fixture'):
        source_files(tmp_path)


def _reviewed_fixture(tmp_path: Path, payload: bytes) -> str:
    _minimal_code_tree(tmp_path)
    name = 'src/mrp/tests/fixtures/synthetic-session-v1.json'
    fixture = tmp_path / name
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(payload)
    return name


def _reviewed_fixture_lf() -> bytes:
    path = Path(__file__).resolve().parents[2] / 'src/mrp/tests/fixtures/synthetic-session-v1.json'
    return path.read_bytes().replace(b'\r\n', b'\n')


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'], ids=['git-lf', 'windows-crlf'])
def test_reviewed_fixture_accepts_only_checkout_line_ending_equivalence(tmp_path: Path, newline: bytes) -> None:
    name = _reviewed_fixture(tmp_path, _reviewed_fixture_lf().replace(b'\n', newline))
    assert name in source_files(tmp_path)


@pytest.mark.parametrize('mutation', ['body', 'indent', 'extra-line', 'bare-cr', 'bom'])
def test_reviewed_fixture_still_rejects_other_byte_changes(tmp_path: Path, mutation: str) -> None:
    payload = _reviewed_fixture_lf()
    changes = {
        'body': payload.replace(b'Synthetic migration message 0.', b'Different synthetic message 0.'),
        'indent': payload.replace(b'  ', b'   ', 1),
        'extra-line': payload + b'\n',
        'bare-cr': payload.replace(b'\n', b'\r'),
        'bom': b'\xef\xbb\xbf' + payload,
    }
    assert changes[mutation] != payload
    _reviewed_fixture(tmp_path, changes[mutation])
    with pytest.raises(ValueError, match='test-data fixture'):
        source_files(tmp_path)


def test_git_audit_checks_committed_history_without_echoing_secret(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Synthetic Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    readme = root / "README.md"
    readme.write_text("Synthetic public text.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "clean fixture"], check=True)
    readme.write_text("TOKEN=" + "sk-" + "or-v1-" + "A" * 40 + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "commit", "-qam", "synthetic secret fixture"], check=True)

    findings = audit_git_state(root, {"README.md"})
    assert any("possible credential" in finding for finding in findings)
    assert all("sk-" not in finding for finding in findings)
