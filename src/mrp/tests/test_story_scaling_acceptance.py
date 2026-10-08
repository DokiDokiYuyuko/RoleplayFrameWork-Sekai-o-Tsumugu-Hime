"""Synthetic scale gates measure persisted writes and paged read size, not wall time."""
from contextlib import contextmanager
import json

import pytest

from mrp.shared.models import Message, SessionMeta, SessionState, TurnRun, ConversationRun
from mrp.storage.paths import AppPaths
from mrp.storage.session_repo import SessionRepo


def synthetic_state(branch, count):
    return SessionState(schema_version=3,
        meta=SessionMeta(id=branch, story_id="sess-scale-root", title="Synthetic scale acceptance"),
        messages=[Message(id=f"msg-{branch}-{index:06d}", session_id=branch,
            seq=index, turn=index, actor="player", status="final", content="fixed synthetic prose " * 20)
            for index in range(count)])


@pytest.mark.asyncio
@pytest.mark.parametrize("run_kind", [None, "ordinary", "conversation"])
async def test_page_size_and_append_writes_are_bounded_by_requested_work(tmp_path, monkeypatch, run_kind):
    measurements = []
    for count, siblings in ((100, 1), (1000, 16)):
        repo = SessionRepo(AppPaths(tmp_path / str(count)), sqlite_new_stories=True)
        value = synthetic_state("sess-scale-root", count)
        if run_kind:
            for message in value.messages:
                operation = f"scale-op-{message.seq:06d}"
                message.operation_id = operation
                common = dict(id=f"scale-run-{message.seq:06d}", session_id=value.meta.id,
                    operation_id=operation, turn=message.turn, status="completed")
                if run_kind == "ordinary":
                    value.turn_runs.append(TurnRun(**common, request_fingerprint="synthetic",
                        input_message_ids=[message.id]))
                else:
                    value.conversation_runs.append(ConversationRun(**common, scene_id="scene-synthetic",
                        seed_message_ids=[message.id], last_committed_message_id=message.id))
        await repo.save_state(value, 0)
        for index in range(siblings):
            await repo.save_state(synthetic_state(f"sess-other-{index:03d}", 3), 0)
        # A history window must never hydrate the complete stored branch.
        monkeypatch.setattr(repo.story_db, "load", lambda *args, **kwargs:
            pytest.fail("Paged reading hydrated the entire story"))
        window, turn, latest, before = repo.story_db.read_window(value.meta.id, limit=20)
        assert len(window.messages) == 20 and turn == latest == count - 1
        assert before == count - 20
        assert len(window.turn_runs) <= 21
        assert len(window.conversation_runs) <= 21
        payload_bytes = len(window.model_dump_json().encode("utf-8"))
        transaction = repo.story_db.transaction
        writes = []

        @contextmanager
        def measured_transaction():
            with transaction() as connection:
                baseline = connection.total_changes
                yield connection
                writes.append(connection.total_changes - baseline)

        monkeypatch.setattr(repo.story_db, "transaction", measured_transaction)
        value.messages.append(Message(id="msg-new-fixed", session_id=value.meta.id,
            seq=count, turn=count, actor="player", status="final", content="one fixed appended reply"))
        await repo.save_state(value, 0)
        measurements.append({"run_kind": run_kind, "history": count, "other_branches": siblings, "page_messages": 20,
            "page_bytes": payload_bytes, "sqlite_row_changes": sum(writes)})
    assert measurements[1]["page_bytes"] <= measurements[0]["page_bytes"] + 1024
    assert measurements[1]["sqlite_row_changes"] <= measurements[0]["sqlite_row_changes"] + 4
    print("STORY_SCALE_ACCEPTANCE=" + json.dumps(measurements))
