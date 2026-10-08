"""Executable boundaries: fail before a local change silently broadens coupling."""
import ast
from pathlib import Path

from mrp.shared.models import SessionMeta, SessionState
from mrp.shared.session_policy import SESSION_FIELD_POLICIES, SESSION_STATE_FIELD_POLICIES, GENERATION_MATERIAL_META_FIELDS, LEGACY_SNAPSHOT_META_FIELDS

ROOT = Path(__file__).resolve().parents[3]


def test_every_session_field_has_explicit_ownership_and_history_policy():
    assert set(SessionMeta.model_fields) == set(SESSION_FIELD_POLICIES)
    assert set(SessionState.model_fields) == set(SESSION_STATE_FIELD_POLICIES)
    for model, policies in ((SessionMeta, SESSION_FIELD_POLICIES), (SessionState, SESSION_STATE_FIELD_POLICIES)):
        for name, field in model.model_fields.items():
            policy = policies[name]
            assert policy.scope and policy.historical and policy.api and policy.default
            if field.is_required():
                assert policy.default == "required", name
    assert set(LEGACY_SNAPSHOT_META_FIELDS) < set(GENERATION_MATERIAL_META_FIELDS)


def test_domain_and_contracts_do_not_depend_on_runtime_adapters():
    violations = []
    for directory in (ROOT / "src/mrp/shared", ROOT / "src/mrp/contracts"):
        for file in directory.rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                modules = [node.module or ""] if isinstance(node, ast.ImportFrom) else [item.name for item in node.names] if isinstance(node, ast.Import) else []
                for module in modules:
                    if module.startswith(("mrp.server", "mrp.storage", "mrp.engines", "mrp.orchestrator", "fastapi")):
                        violations.append(f"{file.relative_to(ROOT)}:{node.lineno}: {module}")
    assert not violations, "\n".join(violations)


def test_transport_projection_does_not_expand_when_storage_metadata_expands():
    from mrp.contracts.story import MessageView, GenerationView, ProvenanceView, TurnRunView, ConversationRunView
    assert "baseline_state" not in ProvenanceView.model_fields
    assert "memory_recall" not in GenerationView.model_fields
    assert "parallel_context" not in TurnRunView.model_fields
    assert not {"steps", "scheduling_trace"} & ConversationRunView.model_fields.keys()
    assert "generation_meta" in MessageView.model_fields
