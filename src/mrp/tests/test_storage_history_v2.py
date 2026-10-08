from types import SimpleNamespace

import pytest

from mrp.orchestrator.memory import MemoryHistoryUnavailable, MemoryStore
from mrp.shared.models import MemoryRecord, Message, SessionMeta, SessionState
from mrp.storage.story_search import StorySearchIndex


def test_memory_checkpoint_survives_edit_invalidation_and_delete(tmp_path):
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    record = MemoryRecord(id='mem-a', session_id='branch', character_id='guide',
                          content='original', source_message_ids=['message'])
    store.add(record)
    first = store.current_watermark('branch')
    record.content = 'edited'
    store.update_record(record)
    edited = store.current_watermark('branch')
    store.invalidate_sources('branch', {'message'})
    invalidated = store.current_watermark('branch')
    store.delete_record(record.id)
    assert store.history_at('branch', first)[0].content == 'original'
    assert store.history_at('branch', edited)[0].content == 'edited'
    assert store.history_at('branch', invalidated) == []
    prefix = [SimpleNamespace(id='message', can_see=lambda actor: True)]
    assert store.copy_at_fork('branch', 'child', first, prefix)[0].content == 'original'
    store.close()
    reopened = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    assert reopened.history_at('branch', first)[0].content == 'original'
    reopened.close()


def test_legacy_history_declares_coverage_floor(tmp_path):
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    store.add(MemoryRecord(session_id='branch', character_id='guide', content='legacy'))
    for trigger in ('memory_history_insert', 'memory_history_update', 'memory_history_delete'):
        store.conn.execute(f'DROP TRIGGER {trigger}')
    store.conn.execute('DROP TABLE memory_history')
    store.conn.execute('DROP TABLE memory_history_floor')
    store.conn.commit()
    store.close()
    upgraded = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    with pytest.raises(MemoryHistoryUnavailable):
        upgraded.history_at('branch', 0)
    assert upgraded.history_at('branch', 1)[0].content == 'legacy'
    upgraded.close()


def test_search_rejects_stale_worker_and_deleted_branch(tmp_path):
    index = StorySearchIndex(tmp_path)
    state = SessionState(meta=SessionMeta(id='branch', title='synthetic', branch_revision=2))
    state.messages = [Message(id='message', session_id='branch', seq=0, turn=0, actor='player', content='new', status='final')]
    index.index_state(state)
    old = state.model_copy(deep=True)
    old.meta.branch_revision = 1
    old.messages[0].content = 'old'
    index.index_state(old)
    assert index.search(state.meta.story_id, '')['results'][0]['excerpt'] == 'new'
    index.delete_branch('branch')
    index.index_state(state)
    assert index.search(state.meta.story_id, '')['results'] == []


def test_upgrade_declares_floor_for_clock_with_no_current_records(tmp_path):
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    store.add(MemoryRecord(id='old', session_id='branch', character_id='guide', content='deleted before upgrade'))
    store.delete_record('old')
    clock = store.current_watermark('branch')
    for trigger in ('memory_history_insert', 'memory_history_update', 'memory_history_delete'):
        store.conn.execute(f'DROP TRIGGER {trigger}')
    store.conn.execute('DROP TABLE memory_history')
    store.conn.execute('DROP TABLE memory_history_floor')
    store.conn.commit()
    store.close()
    upgraded = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    assert not upgraded.history_status('branch', clock - 1)['history_complete']
    with pytest.raises(MemoryHistoryUnavailable):
        upgraded.history_at('branch', clock - 1)
    assert upgraded.history_at('branch', clock) == []
    upgraded.close()
