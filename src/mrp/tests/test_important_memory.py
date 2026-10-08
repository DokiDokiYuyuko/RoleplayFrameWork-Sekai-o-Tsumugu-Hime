import json
import pytest
from mrp.orchestrator.memory import MemoryStore
from mrp.orchestrator.important_memory import MEMORY_OUTPUT_TOKENS, ImportantMemoryConsolidator, chunks
from mrp.shared.models import SessionState, SessionMeta, Character, CharacterCard, Message, MemoryRecord, PlayerIdentity


def story():
    state = SessionState(meta=SessionMeta(id="isolated-memory", character_ids=["guide"]),
                         characters=[Character(id="guide", card=CharacterCard(name="向导"))])
    state.messages = [Message(id="old", session_id=state.meta.id, seq=0, turn=1, actor="player",
        content="旅行资料。" * 2000 + "\n下次见面一起去图书馆。")]
    return state


def result(material, *, category="unfinished", content="玩家与向导约定下次去图书馆", status="open"):
    row = material["messages"][-1]
    return json.dumps({"memories": [{"category": category, "content": content,
        "participant_ids": ["player", "guide"], "source_message_ids": [row["id"]],
        "evidence": {row["id"]: row["text"]}, "matter_status": status}]}, ensure_ascii=False)


def test_complete_long_window_and_long_message(tmp_path):
    state = story()
    seen = []
    def fake(messages):
        data = json.loads(messages[-1]["content"])
        seen.extend(r["text"] for r in data["messages"])
        return result(data) if any("图书馆" in r["text"] for r in data["messages"]) else '{"memories":[]}'
    extractor = ImportantMemoryConsolidator(fake)
    records = extractor.consolidate_records(state, "guide", 0, 1, input_limit=2400)
    assert "".join(seen) == state.messages[0].content
    assert len(seen) > 1
    assert len(records) == 1 and "图书馆" in records[0].content
    store = MemoryStore(tmp_path / "memory.db", tmp_path / "mirrors", None)
    hashes = {m.id: m.fingerprint for m in state.messages}
    store.commit_window(state.meta.id, "guide", 1, 1, hashes, records)
    assert store.commit_window(state.meta.id, "guide", 1, 1, hashes, records) == []
    assert len(store.records_for("guide")) == 1
    assert store.records_for("guide")[0].source_fingerprints == hashes
    store.close()


def test_retry_invalid_evidence_no_success_fallback():
    calls = []
    def invalid(messages):
        calls.append(messages)
        return '{"memories":[{"content":"虚构","category":"experience","source_message_ids":["missing"]}]}'
    with pytest.raises(ValueError, match="未完成"):
        ImportantMemoryConsolidator(invalid).consolidate_records(story(), "guide", 0, 1)
    assert len(calls) == 2


def test_empty_coverage_and_dirty_old_window(tmp_path):
    store = MemoryStore(tmp_path / "memory.db", tmp_path / "mirrors", None)
    state = story()
    hashes = {m.id: m.fingerprint for m in state.messages}
    store.commit_window(state.meta.id, "guide", 1, 1, hashes, [])
    store.commit_window(state.meta.id, "guide", 2, 50, {}, [])
    assert store.last_consolidated_turn("guide", state.meta.id) == 50
    store.invalidate_sources(state.meta.id, {"old"})
    state.messages[0].content = "约定取消了。"
    from mrp.shared.models import fingerprint
    state.messages[0].fingerprint = fingerprint("player", 0, state.messages[0].content)
    assert store.next_window("guide", state.meta.id, 50, state.messages) == (1, 1, True)
    store.close()


def test_manual_correction_retained_and_rollback(tmp_path):
    store = MemoryStore(tmp_path / "memory.db", tmp_path / "mirror", None)
    record = MemoryRecord(character_id="guide", session_id="a", content="已纠正", manually_revised=True,
                          source_message_ids=["old"], important=True, participant_ids=["player", "guide"])
    store.add(record)
    affected = store.invalidate_sources("a", {"old"})
    current = store.records_for("guide", session_id="a")[0]
    assert current.source_changed and not current.invalidated and current.content == "已纠正"
    store.restore_invalidated("a", affected)
    assert not store.records_for("guide")[0].source_changed
    store.reload_mirrors()
    assert store.records_for("guide")[0].important
    store.close()


def test_legacy_invalidation_not_skipped_by_newer_coverage(tmp_path):
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    state = story()
    old = MemoryRecord(character_id='guide', session_id=state.meta.id, kind='episodic',
                       content='旧压缩记忆', source_message_ids=['old'], turn_start=1, turn_end=1)
    store.add(old)
    store.commit_window(state.meta.id, 'guide', 2, 50, {}, [])
    affected = store.invalidate_sources(state.meta.id, {'old'})
    assert store.next_window('guide', state.meta.id, 50, state.messages) == (1, 1, True)
    store.restore_invalidated(state.meta.id, affected)
    assert store.next_window('guide', state.meta.id, 50, state.messages) == (51, 50, False)
    store.close()


def test_failed_update_and_batch_roll_back(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    record = MemoryRecord(character_id='guide', session_id='s', content='原始')
    store.add(record)
    def fail(_record):
        raise RuntimeError('模拟存储失败')
    monkeypatch.setattr(store, '_insert_db', fail)
    changed = record.model_copy(update={'content': '修改'})
    with pytest.raises(RuntimeError): store.update_record(changed)
    assert store.records_for('guide')[0].content == '原始'
    with pytest.raises(RuntimeError): store.commit_window('s', 'guide', 1, 1, {}, [changed])
    assert store.window_rows('guide', 's') == []
    store.close()


def test_reunion_recall_without_keywords_branch_privacy_and_recent_dedup(tmp_path):
    from mrp.orchestrator.memory_recall import recall_memories, finish_recall_audit
    from mrp.shared.models import TurnContext
    from mrp.shared.prompt import compose_prompt
    state = story()
    store = MemoryStore(tmp_path / "memory.db", tmp_path / "mirrors", None)
    source = state.messages[0]
    record = MemoryRecord(character_id="guide", session_id=state.meta.id, content="下次见面去图书馆。旧日曾在港口告别。",
        category="unfinished", matter_status="open", important=True, participant_ids=["player", "guide"],
        source_message_ids=[source.id], source_fingerprints={source.id: source.fingerprint}, turn_start=1, turn_end=1)
    store.add(record)
    store.add(MemoryRecord(character_id="guide", session_id="sibling", content="另一路线的秘密", important=True))
    for index in range(2, 25):
        state.messages.append(Message(id=f"m{index}", session_id=state.meta.id, seq=index, turn=index,
                                      actor="player", content="普通旅程。"))
    state.messages.append(Message(id="reunion", session_id=state.meta.id, seq=25, turn=25, actor="player", content="好久不见"))
    visible = state.visible_messages_for("guide")
    audit = []
    injections = recall_memories(store, state, "guide", visible, ["guide"], "好久不见", audit=audit)
    prompt = compose_prompt(TurnContext(session_id=state.meta.id, character_id="guide", turn=25,
                                       visible_messages=visible, injections=injections))
    finish_recall_audit(audit, prompt.included_entry_ids, prompt.omitted_reasons)
    assert record.id in prompt.included_entry_ids and "图书馆" in prompt.text
    assert "另一路线的秘密" not in prompt.text
    assert "历史地点" in prompt.text and audit[0]["disposition"] == "injected"
    assert not recall_memories(store, state, "guide", [source], ["guide"], "")
    store.invalidate_sources(state.meta.id, {source.id})
    assert not recall_memories(store, state, "guide", visible, ["guide"], "好久不见")
    store.close()


def test_completed_matter_supersedes_open_atomic_commit(tmp_path):
    store = MemoryStore(tmp_path / "memory.db", tmp_path / "mirrors", None)
    old = MemoryRecord(id="agreement", character_id="guide", session_id="s", content="待办借书", category="unfinished",
                       matter_status="open", participant_ids=["player", "guide"])
    store.add(old)
    new = MemoryRecord(character_id="guide", session_id="s", content="已经一起借到了书", category="unfinished",
                       matter_status="completed", participant_ids=["player", "guide"], supersedes=[old.id])
    store.commit_window("s", "guide", 2, 2, {}, [new])
    # Keep its original evidence so deleting a completion can restore the preceding valid state.
    assert not next(r for r in store.records_for("guide") if r.id == old.id).invalidated
    assert next(r for r in store.records_for("guide") if r.id == new.id).supersedes == [old.id]
    store.close()


def test_memory_api_confirm_revision_sources_and_private_selection(mrp_client):
    client = mrp_client
    def card(name):
        return client.post('/api/v1/characters/import', files={'file': ('card.json', json.dumps({
            'name': name, 'first_mes': '在图书馆约定明天继续查阅资料。'}, ensure_ascii=False).encode(), 'application/json')}).json()['id']
    a, b = card('向导'), card('守卫')
    state = client.post('/api/v1/sessions', json={'title': '记忆隔离验收', 'character_ids': [a, b]}).json()
    sid = state['meta']['id']; message = state['messages'][0]
    response = client.post(f'/api/v1/sessions/{sid}/memory/records', json={
        'character_ids': [a], 'source_message_ids': [message['id']],
        'source_fingerprints': {message['id']: message['fingerprint']},
        'content': '明天继续查资料', 'category': 'unfinished', 'matter_status': 'open', 'important': True})
    assert response.status_code == 200, response.text
    record = response.json()['records'][0]
    patch_url = f'/api/v1/characters/{a}/memories/{record["id"]}?session_id={sid}'
    corrected = client.patch(patch_url, json={'content': '约定后天继续查阅', 'expected_revision': 1})
    assert corrected.status_code == 200 and corrected.json()['revision'] == 2
    assert corrected.json()['manually_revised']
    assert client.patch(patch_url, json={'content': '旧请求', 'expected_revision': 1}).status_code == 409
    listed = client.get(f'/api/v1/sessions/{sid}/memory/records', params={'character_id': a}).json()['records']
    assert listed[0]['important'] and listed[0]['content'] == '约定后天继续查阅'
    assert client.delete(f'/api/v1/characters/{b}/memories/{record["id"]}').status_code == 404
    sources = client.get(f'/api/v1/sessions/{sid}/memory/records/{record["id"]}/sources', params={'character_id': a})
    assert sources.status_code == 200 and sources.json()['messages'][0]['id'] == message['id']
    runner = client.app.state.container.runners[sid]
    runner.state.messages[0].visible_to = [a]
    private = client.post(f'/api/v1/sessions/{sid}/memory/records', json={
        'character_ids': [a, b], 'source_message_ids': [message['id']], 'content': '不可泄露'})
    assert private.status_code == 422
    assert len(client.app.state.container.memory_store.records_for(a, session_id=sid)) == 1


@pytest.mark.asyncio
async def test_pipeline_stale_background_and_empty_success(tmp_path):
    import asyncio
    from mrp.tests.test_integration import make_runner
    from mrp.shared.models import fingerprint
    runner, _ = make_runner(replies=['安全回复'])
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    runner.memory = store
    runner.state.messages = [Message(id='old', session_id=runner.state.meta.id, seq=0, turn=1, actor='player', content='约定去图书馆')]
    started = __import__('threading').Event(); release = __import__('threading').Event()
    def slow(messages):
        started.set(); release.wait(3)
        return '{"memories":[]}'
    runner.episodic_consolidator = ImportantMemoryConsolidator(slow)
    task = asyncio.create_task(runner.memory_pipeline._consolidate_character('char-a', 1, min_interval=0, retries=1, reason='test'))
    await asyncio.to_thread(started.wait, 2)
    runner.state.messages[0].content = '约定已经取消'
    runner.state.messages[0].fingerprint = fingerprint('player', 0, runner.state.messages[0].content)
    release.set(); assert await task == []
    assert store.window_rows('char-a', runner.state.meta.id)[0]['status'] == 'dirty'
    runner.episodic_consolidator = ImportantMemoryConsolidator(lambda _m: '{"memories":[]}')
    assert await runner.memory_pipeline._consolidate_character('char-a', 1, min_interval=0, retries=1, reason='test') == []
    assert store.last_consolidated_turn('char-a', runner.state.meta.id) == 1
    store.close()


def test_migration_backs_up_database_and_reads_legacy_defaults(tmp_path):
    import sqlite3
    path = tmp_path / 'memory.db'
    store = MemoryStore(path, tmp_path / 'mirror', None)
    store.add(MemoryRecord(id='legacy', character_id='guide', session_id='s', content='旧经历正文'))
    store.close()
    with sqlite3.connect(path) as old:
        # Construct a pre-history schema, rather than dropping a column still
        # referenced by the temporal triggers of the current schema.
        for trigger in ("memory_history_insert", "memory_history_update", "memory_history_delete"):
            old.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        old.execute('ALTER TABLE memory_records DROP COLUMN details')
    reopened = MemoryStore(path, tmp_path / 'mirror', None)
    record = reopened.records_for('guide', session_id='s')[0]
    assert record.id == 'legacy' and record.category == 'experience' and record.revision == 1
    with sqlite3.connect(str(path) + '.before-important-memory.bak') as backup:
        assert backup.execute('SELECT content FROM memory_records').fetchone()[0] == '旧经历正文'
        assert 'details' not in {row[1] for row in backup.execute('PRAGMA table_info(memory_records)')}
    reopened.close()


def test_chunked_agreement_completion_and_multiple_records():
    state = story()
    state.messages = [
        Message(session_id=state.meta.id, id='agreement', actor='player', seq=0, turn=1, content='约定下次去图书馆，也可以叫我旅伴。'),
        Message(session_id=state.meta.id, id='journey', actor='player', seq=1, turn=2, content='普通旅途的风景。' * 1800),
        Message(session_id=state.meta.id, id='completion', actor='player', seq=2, turn=3, content='我们已经查完航海资料，约定完成了。')]
    requests = []
    def model(messages):
        data = json.loads(messages[-1]['content']); requests.append(data)
        completed = next((row for row in data['messages'] if row['id'] == 'completion'), None)
        agreed = next((row for row in data['messages'] if row['id'] == 'agreement'), None)
        row = completed or agreed
        if row is None: return '{"memories":[]}'
        memories = [{'category': 'unfinished', 'content': row['text'],
                     'source_message_ids': [row['id']], 'evidence': {row['id']: row['text']},
                     'participant_ids': ['player', 'guide'], 'matter_status': 'completed' if completed else 'open',
                     'supersedes': [m['id'] for m in data['previous_matters'] if m['status'] == 'open'] if completed else []}]
        if agreed:
            memories.append({**memories[0], 'category': 'relationship', 'content': '称呼旅伴', 'matter_status': 'unknown'})
        return json.dumps({'memories': memories}, ensure_ascii=False)
    records = ImportantMemoryConsolidator(model).consolidate_records(state, 'guide', 0, 3, input_limit=2400)
    assert len(requests) > 2 and len(records) == 3
    opened = next(r for r in records if r.matter_status == 'open')
    finished = next(r for r in records if r.matter_status == 'completed')
    assert finished.supersedes == [opened.id]
    assert ''.join(row['text'] for req in requests for row in req['messages']) == ''.join(m.content for m in state.messages)


@pytest.mark.asyncio
async def test_selected_actor_final_request_reunion_and_edit_invalidation(tmp_path):
    from mrp.tests.test_integration import make_runner
    from mrp.shared.prompt import compose_prompt
    runner, _ = make_runner(replies=['旅伴，欢迎回来。', '你现在想做什么？'])
    runner.state.meta.short_input_padding = False
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    runner.memory = store
    source = Message(session_id=runner.state.meta.id, id='agreement', actor='player', seq=0, turn=1, content='我们下次查航海资料。')
    runner.state.messages = [source] + [Message(session_id=runner.state.meta.id, id=f'other-{i}', actor='player', seq=i, turn=i,
                                               content='其他剧情。') for i in range(2, 30)]
    record = MemoryRecord(character_id='char-a', session_id=runner.state.meta.id, content='双方约好重逢时查航海资料。',
        important=True, category='unfinished', matter_status='open', participant_ids=['player', 'char-a'],
        source_message_ids=[source.id], source_fingerprints={source.id: source.fingerprint}, turn_start=1, turn_end=1)
    store.add(record)
    created = await runner.player_say('好久不见', mentions=['char-a'])
    ctx = runner.engines._engines['char-a'].calls[-1]
    assert '航海资料' in compose_prompt(ctx).text and record.id in created[-1].generation_meta.injected_entry_ids
    assert any(row['memory_id'] == record.id and row['disposition'] == 'injected'
               for row in created[-1].generation_meta.memory_recall)
    await runner.edit_message(source.id, '之前没有这个约定。')
    await runner.player_say('好久不见', mentions=['char-a'])
    assert record.id not in compose_prompt(runner.engines._engines['char-a'].calls[-1]).included_entry_ids
    store.close()


@pytest.mark.asyncio
async def test_group_reunion_recall_and_private_sources(tmp_path):
    from types import SimpleNamespace
    from mrp.settings import AppSettings
    from mrp.orchestrator.group_actors import GroupResponder
    from mrp.shared.models import Scene, GroupActor
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    state = story()
    group = GroupActor(id='readers', label='成年读者们', scene_id='library', joined_seq=0)
    state.groups = [group]; state.scenes = [Scene(id='library', title='图书馆', group_ids=[group.id])]
    state.active_scene_id = 'library'
    state.messages = [Message(session_id=state.meta.id, id='old', actor='player', turn=1, seq=0, scene_id='library', content='下次一起找航海资料。')]
    state.messages += [Message(session_id=state.meta.id, id=f'm{i}', actor='player', seq=i, turn=i, scene_id='library', content='其他经历。') for i in range(2, 25)]
    state.messages.append(Message(session_id=state.meta.id, actor='player', seq=25, turn=25, scene_id='library', content='好久不见。'))
    source = state.messages[0]
    memory = MemoryRecord(character_id=group.id, session_id=state.meta.id, content='我们约好查航海资料。',
        participant_ids=['player', group.id], category='unfinished', matter_status='open', important=True,
        source_message_ids=[source.id], source_fingerprints={source.id: source.fingerprint})
    store.add(memory)
    responder = GroupResponder(SimpleNamespace(settings=AppSettings(context_limit_override=65536),
        fake_mode=True, config=SimpleNamespace(fake_mode=True), memory_store=store))
    _, _, trace = await responder.generate(state, group, participants=[group.id, 'guide'])
    assert memory.id in trace['memory_ids'] and '航海资料' in trace['user']
    source.visible_to = ['guide']
    _, _, trace = await responder.generate(state, group, participants=[group.id, 'guide'])
    assert memory.id not in trace['memory_ids'] and '约好查航海资料' not in trace['user']
    store.close()


@pytest.mark.asyncio
async def test_variant_delete_and_failed_regeneration_restore_valid_memories(tmp_path):
    from mrp.tests.test_integration import make_runner
    from mrp.shared.models import MessageVariant
    from mrp.engines.fake import FakeEngine
    from mrp.orchestrator.memory_recall import recall_memories
    runner, _ = make_runner()
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None); runner.memory = store
    player = Message(session_id=runner.state.meta.id, actor='player', seq=0, turn=1, content='请回答', mentions=['char-a'])
    answer = Message(session_id=runner.state.meta.id, id='answer', actor='char-a', seq=1, turn=1, content='约定明天借书。',
                     variants=[MessageVariant(content='约定明天借书。'), MessageVariant(content='约定取消。')])
    runner.state.messages = [player, answer]
    old = MemoryRecord(character_id='char-a', session_id=runner.state.meta.id, kind='episodic', content='明天借书',
        important=True, participant_ids=['player', 'char-a'], source_message_ids=[answer.id],
        source_fingerprints={answer.id: answer.fingerprint}, turn_start=1, turn_end=1)
    store.commit_window(runner.state.meta.id, 'char-a', 1, 1, {player.id: player.fingerprint, answer.id: answer.fingerprint}, [old])
    class FailingEngine(FakeEngine):
        async def generate(self, *args, **kwargs): raise RuntimeError('模拟失败')
    runner.engines._engines['char-a'] = FailingEngine()
    with pytest.raises(RuntimeError): await runner.regenerate_turn(player.id)
    assert runner.state.messages[-1].id == answer.id and not store.records_for('char-a')[0].invalidated
    await runner.switch_variant(answer.id, 1)
    assert store.records_for('char-a')[0].invalidated
    assert not recall_memories(store, runner.state, 'char-a', [], ['char-a'], '借书')
    assert store.next_window('char-a', runner.state.meta.id, 1, runner.state.messages) == (1, 1, True)
    await runner.delete_message(answer.id)
    assert store.window_rows('char-a', runner.state.meta.id)[0]['status'] == 'dirty'
    store.close()


def test_same_name_identity_and_mirror_failure_preserves_commit(tmp_path, monkeypatch):
    from mrp.orchestrator.memory_recall import recall_memories
    state = story(); state.characters.append(Character(id='other-guide', card=CharacterCard(name='向导')))
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    record = MemoryRecord(character_id='guide', session_id=state.meta.id, content='只与第二位向导相关',
                          participant_ids=['guide', 'other-guide'], important=True)
    store.add(record)
    assert not recall_memories(store, state, 'guide', [], ['guide'], '')
    assert recall_memories(store, state, 'guide', [], ['guide', 'other-guide'], '')
    def fail(_cid): raise OSError('模拟镜像只读')
    monkeypatch.setattr(store, '_rewrite_mirror', fail)
    changed = record.model_copy(update={'content': '数据库修订仍成功', 'revision': 2})
    assert store.update_record(changed)
    assert store.records_for('guide')[0].revision == 2
    store.add_many([MemoryRecord(character_id='guide', session_id=state.meta.id, content='数据库新增仍成功')])
    assert len(store.records_for('guide')) == 2
    store.close()


def test_fork_remaps_matter_progress_and_keeps_sibling_independent(tmp_path):
    from mrp.orchestrator.memory_recall import recall_memories
    state = story(); state.meta.id = 'parent'
    state.messages = [Message(session_id='parent', id='open', actor='player', seq=0, turn=1, content='明天借书。'),
                      Message(session_id='parent', id='done', actor='player', seq=1, turn=2, content='我们已经借到书。')]
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    opened = MemoryRecord(id='matter-open', character_id='guide', session_id='parent', category='unfinished',
        content='明天借书', matter_status='open', participant_ids=['player', 'guide'], source_message_ids=['open'],
        source_fingerprints={'open': state.messages[0].fingerprint}, turn_start=1, turn_end=1)
    finished = MemoryRecord(id='matter-done', character_id='guide', session_id='parent', category='unfinished',
        content='借书已经完成', matter_status='completed', participant_ids=['player', 'guide'], source_message_ids=['done'],
        source_fingerprints={'done': state.messages[1].fingerprint}, supersedes=[opened.id], turn_start=2, turn_end=2)
    store.add_many([opened, finished])
    copied = store.copy_at_fork('parent', 'child', store.current_watermark('parent'), state.messages)
    child_open = next(r for r in copied if r.matter_status == 'open')
    child_done = next(r for r in copied if r.matter_status == 'completed')
    assert child_done.supersedes == [child_open.id]
    child = state.model_copy(deep=True); child.meta.id = 'child'
    recalled = recall_memories(store, child, 'guide', [], ['guide'], '')
    assert [r.entry_id for r in recalled] == [child_done.id]
    store.delete_record(child_done.id)
    assert [r.entry_id for r in recall_memories(store, child, 'guide', [], ['guide'], '')] == [child_open.id]
    assert [r.entry_id for r in recall_memories(store, state, 'guide', [], ['guide'], '')] == [finished.id]
    store.close()


def test_extraction_retry_caps_output_and_preserves_usage(monkeypatch):
    from mrp.orchestrator import important_memory
    calls = []
    def gateway(messages, _config, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            error = RuntimeError('上游截断')
            error.usage = {'input_tokens': 100, 'output_tokens': 200}
            raise error
        return result(json.loads(messages[-1]['content'])), {'input_tokens': 100, 'output_tokens': 300}
    monkeypatch.setattr(important_memory, 'chat_text_with_usage', gateway)
    records = ImportantMemoryConsolidator(network=True).consolidate_records(story(), 'guide', 0, 1)
    assert len(records) == 1 and len(calls) == 2
    assert all(call['max_tokens'] == MEMORY_OUTPUT_TOKENS and call['require_complete'] for call in calls)
    assert records.usage.input_tokens == 200 and records.usage.output_tokens == 500


def test_automatic_organize_does_not_overwrite_player_correction(tmp_path):
    state = story(); source = state.messages[0]
    store = MemoryStore(tmp_path / 'memory.db', tmp_path / 'mirror', None)
    manual = MemoryRecord(character_id='guide', session_id=state.meta.id, kind='episodic',
        content='玩家确认：约定是下周一起查资料。', category='unfinished', matter_status='open',
        manually_revised=True, important=True, revision=3, source_message_ids=[source.id],
        source_fingerprints={source.id: source.fingerprint}, turn_start=1, turn_end=1)
    store.add(manual)
    auto = manual.model_copy(update={'id': 'auto-result', 'manually_revised': False, 'content': '明天查资料', 'revision': 1})
    assert store.commit_window(state.meta.id, 'guide', 1, 1, {source.id: source.fingerprint}, [auto]) == []
    record = store.records_for('guide')[0]
    assert record.content == manual.content and record.revision == 3 and record.important and not record.invalidated
    store.close()


def test_deduplicated_chunk_records_keep_completion_references():
    state = story()
    state.messages = [Message(session_id=state.meta.id, id='long', actor='player', seq=0, turn=1,
                              content='整理图书馆资料的约定。' * 1700),
                      Message(session_id=state.meta.id, id='done', actor='player', seq=1, turn=2,
                              content='约定已完成。')]
    calls = []
    def model(messages):
        data = json.loads(messages[-1]['content']); calls.append(data)
        row = data['messages'][-1]
        completed = row['id'] == 'done'
        return json.dumps({'memories': [{'category': 'unfinished', 'content': '整理资料完成' if completed else '约定整理资料',
            'participant_ids': ['player', 'guide'], 'source_message_ids': [row['id']],
            'evidence': {row['id']: row['text']}, 'matter_status': 'completed' if completed else 'open',
            'supersedes': [data['previous_matters'][-1]['id']] if completed else []}]}, ensure_ascii=False)
    records = ImportantMemoryConsolidator(model).consolidate_records(state, 'guide', 0, 2, input_limit=2400)
    assert len(calls) > 2 and len(records) == 2
    opened = next(r for r in records if r.matter_status == 'open')
    completed = next(r for r in records if r.matter_status == 'completed')
    assert completed.supersedes == [opened.id]


def test_automatic_next_window_skips_failed(tmp_path):
    store = MemoryStore(tmp_path / "skip.db", tmp_path / "mirror", None)
    store.mark_window("s", "guide", 1, 15, "failed", {}, "整理失败，可再次整理；未提交不完整记忆")
    assert store.next_window("guide", "s", 20, [], retry_failed=False) == (16, 20, False)
    assert store.next_window("guide", "s", 20, [], retry_failed=True) == (1, 15, True)
    store.close()


def test_single_identity_stores_person_and_two_identities_keep_player():
    state = story()
    state.messages = [Message(id="src", session_id=state.meta.id, seq=0, turn=1, actor="player", content="下次见面一起去图书馆。")]
    state.player_identities = [PlayerIdentity(person_id="person-wei", name="薇安")]
    state.meta.player_identity_id = state.player_identities[0].id

    def model(messages):
        data = json.loads(messages[-1]["content"])
        row = data["messages"][-1]
        return json.dumps({"memories": [{"category": "experience", "content": "一起看过图书馆的地图",
            "participant_ids": ["player", "guide"], "source_message_ids": [row["id"]],
            "evidence": {row["id"]: "图书馆"}, "matter_status": "unknown"}]}, ensure_ascii=False)

    records = ImportantMemoryConsolidator(model).consolidate_records(state, "guide", 0, 1)
    assert records[0].participant_ids == ["person-wei", "guide"]
    state.player_identities.append(PlayerIdentity(person_id="person-other", name="另一人"))
    again = ImportantMemoryConsolidator(model).consolidate_records(state, "guide", 0, 1)
    assert "player" in again[0].participant_ids and "person-wei" not in again[0].participant_ids


def test_single_identity_recalls_generic_player_and_two_identities_omit(tmp_path):
    from mrp.orchestrator.memory_recall import recall_memories
    from mrp.shared.player_identity import memory_participants
    state = story()
    source = Message(id="src", session_id=state.meta.id, seq=0, turn=1, actor="player", content="下次见面一起去图书馆。")
    state.messages = [source]
    identity = PlayerIdentity(person_id="person-wei", name="薇安")
    state.player_identities = [identity]
    state.meta.player_identity_id = identity.id
    for index in range(1, 18):
        state.messages.append(Message(id=f"pad{index}", session_id=state.meta.id, seq=index, turn=index + 1,
                                      actor="guide", content="赶路。"))
    store = MemoryStore(tmp_path / "recall.db", tmp_path / "mirror", None)
    record = MemoryRecord(character_id="guide", session_id=state.meta.id, content="约好去图书馆。",
        category="experience", participant_ids=["player", "scholar"], source_message_ids=[source.id],
        source_fingerprints={source.id: source.fingerprint}, turn_start=1, turn_end=1)
    store.add(record)
    assert memory_participants(state, record) == {"person-wei", "scholar"}
    visible = state.visible_messages_for("guide")
    audit = []
    injections = recall_memories(store, state, "guide", visible, ["scholar"], audit=audit)
    assert any(item.entry_id == record.id for item in injections)
    assert not any(row["reason"] == "历史玩家身份未确认，待玩家纠正" for row in audit)
    state.player_identities.append(PlayerIdentity(person_id="person-other", name="另一人"))
    audit = []
    injections = recall_memories(store, state, "guide", visible, ["scholar"], audit=audit)
    assert not any(item.entry_id == record.id for item in injections)
    assert any(row["reason"] == "历史玩家身份未确认，待玩家纠正" for row in audit)
    store.close()


@pytest.mark.asyncio
async def test_consolidation_window_stays_inside_interval(tmp_path):
    from mrp.tests.test_integration import make_runner
    runner, _ = make_runner(replies=["嗯。"])
    store = MemoryStore(tmp_path / "clamp.db", tmp_path / "mirror", None)
    runner.memory = store
    runner.state.meta.memory_interval_turns = 1
    runner.state.messages = [
        Message(id="t1", session_id=runner.state.meta.id, seq=0, turn=1, actor="player", content="第一段约定去图书馆。"),
        Message(id="t2", session_id=runner.state.meta.id, seq=1, turn=2, actor="player", content="第二段只是赶路。"),
    ]
    seen = []

    def model(messages):
        data = json.loads(messages[-1]["content"])
        seen.append([row["turn"] for row in data["messages"]])
        return '{"memories":[]}'

    runner.episodic_consolidator = ImportantMemoryConsolidator(model)
    await runner.memory_pipeline._consolidate_character("char-a", 2, min_interval=0, retries=1, reason="test")
    assert seen == [[1]]
    store.close()


@pytest.mark.asyncio
async def test_memory_consolidation_setting_gates_automatic_work(tmp_path):
    import asyncio
    from mrp.settings import AppSettings
    from mrp.tests.test_integration import make_runner
    runner, _ = make_runner(replies=["嗯。"])
    store = MemoryStore(tmp_path / "gate.db", tmp_path / "mirror", None)
    runner.memory = store
    runner.episodic_consolidator = ImportantMemoryConsolidator(lambda _messages: '{"memories":[]}')
    runner.state.meta.memory_interval_turns = 1
    runner.state.messages = [Message(id="t1", session_id=runner.state.meta.id, seq=0, turn=1, actor="player", content="约定。")]
    runner.app_settings = AppSettings()
    runner.memory_pipeline.maybe_spawn_consolidation(1, "interval")
    runner.memory_pipeline.spawn_scene_summary_task(None)
    assert runner._bg_tasks == set()
    runner.app_settings.memory_consolidation_enabled = True
    runner.memory_pipeline.maybe_spawn_consolidation(1, "interval")
    assert len(runner._bg_tasks) == 1
    await asyncio.gather(*list(runner._bg_tasks))
    store.close()
