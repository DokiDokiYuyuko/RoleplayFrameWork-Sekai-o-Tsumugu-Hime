"""Production context and branch compatibility checks using disposable synthetic assets."""
import json

import pytest

from mrp.orchestrator.context import ContextBuilder
from mrp.orchestrator.model_capacity import ModelCapacity
from mrp.orchestrator.worldline_state import apply_projection, current_state_projection
from mrp.shared.models import Injection, SessionMeta, SessionState, TurnContext
from mrp.shared.prompt import compose_prompt


def character(client):
    response = client.post('/api/v1/characters/import', files={'file': ('fiction.json', json.dumps({
        'name': '合成守卫', 'description': '合成守卫资料', 'first_mes': '默认迎接',
        'alternate_greetings': ['雨夜迎接', '黎明迎接'],
    }).encode(), 'application/json')})
    assert response.status_code == 200, response.text
    return response.json()['id']


@pytest.fixture
def capacity(monkeypatch):
    async def fixed(self, character, **kwargs):
        return ModelCapacity(64000, 50000, 8000, 'synthetic')
    monkeypatch.setattr(ContextBuilder, 'model_capacity', fixed)


def test_create_exact_manuscript_preview_and_frozen_story(mrp_client, capacity):
    cid = character(mrp_client)
    body = '  原稿标记：合成北岸的桥在雨夜关闭。\n\n保留空行与尾部空格。  '
    response = mrp_client.post('/api/v1/worlds', json={
        'title': '合成自由世界', 'manuscript_body': body, 'core_brief': '核心标记：请勿重复原稿模式。'})
    assert response.status_code == 200, response.text
    world = response.json()
    assert world['archive_records'][0]['body'] == body
    assert world['runtime_policy'] == 'raw'
    container = mrp_client.app.state.container
    sessions_before = set(container.runners)
    files_before = {path.name: path.read_bytes() for path in container.paths.sessions_dir.glob('*.json')}
    preview = mrp_client.post(f"/api/v1/worlds/{world['id']}/runtime-preview", json={'message': '检查北岸', 'character_id': cid})
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result['valid'] and '原稿标记' in result['prompt'] and '核心标记' not in result['prompt']
    assert set(container.runners) == sessions_before
    assert {path.name: path.read_bytes() for path in container.paths.sessions_dir.glob('*.json')} == files_before
    opened = mrp_client.post('/api/v1/sessions', json={'title': '合成故事', 'character_ids': [cid], 'world_id': world['id']})
    assert opened.status_code == 200, opened.text
    state = opened.json()
    assert state['meta']['world_runtime_policy'] == 'raw'
    patched = mrp_client.patch(f"/api/v1/worlds/{world['id']}", json={'expected_revision': world['revision'], 'runtime_policy': 'compiled'})
    assert patched.status_code == 200, patched.text
    assert mrp_client.get(f"/api/v1/sessions/{state['meta']['id']}").json()['meta']['world_runtime_policy'] == 'raw'


def test_compiled_excludes_original_and_author_entries(mrp_client, capacity):
    cid = character(mrp_client)
    book = mrp_client.post('/api/v1/lorebooks', json={'book': {'name': '合成词条', 'entries': [
        {'uid': 1, 'constant': True, 'content': '公开词条标记'},
        {'uid': 2, 'constant': True, 'content': '私密词条标记', 'extensions': {'mrp.runtime_scope': 'author'}},
    ]}})
    assert book.status_code == 200, book.text
    bid = book.json()['id']
    world = mrp_client.post('/api/v1/worlds', json={'title': '合成整理世界', 'manuscript_body': '完整原稿标记', 'runtime_policy': 'compiled', 'core_brief': '核心保留标记'}).json()
    linked = mrp_client.post(f"/api/v1/worlds/{world['id']}/lorebooks", json={'expected_revision': world['revision'], 'book_id': bid})
    assert linked.status_code == 200, linked.text
    result = mrp_client.post(f"/api/v1/worlds/{world['id']}/runtime-preview", json={'character_id': cid}).json()
    assert result['valid'], result
    assert '核心保留标记' in result['prompt'] and '公开词条标记' in result['prompt']
    assert '完整原稿标记' not in result['prompt'] and '私密词条标记' not in result['prompt']


def test_raw_rejects_overflow_and_keeps_unknown_capacity():
    ctx = TurnContext(session_id='synthetic', character_id='character', turn=1, world_runtime_policy='raw',
        budget_tokens=1024, injections=[Injection(source='archive', entry_id='long', content='长原稿' * 2000)])
    with pytest.raises(ValueError, match='原稿'):
        compose_prompt(ctx)
    ctx.budget_tokens = None
    assert '长原稿' * 2000 in compose_prompt(ctx).text


def test_raw_does_not_repeat_derived_entries_or_recursion(mrp_client, capacity):
    world = mrp_client.post('/api/v1/worlds', json={'title': '合成直接使用世界', 'manuscript_body': '原文唯一标记'}).json()
    book = mrp_client.post('/api/v1/lorebooks', json={'book': {'name': '合成派生书', 'entries': [
        {'uid': 1, 'constant': True, 'content': '派生重复标记 递归触发词', 'extensions': {'mrp.archive_sources': [{'source_id': world['manuscript_archive_id']}] }},
        {'uid': 2, 'keys': ['递归触发词'], 'content': '错误递归标记'},
        {'uid': 3, 'constant': True, 'content': '独立手写规则'},
    ]}}).json()
    linked = mrp_client.post(f"/api/v1/worlds/{world['id']}/lorebooks", json={'expected_revision': world['revision'], 'book_id': book['id']})
    assert linked.status_code == 200, linked.text
    preview = mrp_client.post(f"/api/v1/worlds/{world['id']}/runtime-preview", json={}).json()
    assert preview['valid'], preview
    assert '原文唯一标记' in preview['prompt'] and '独立手写规则' in preview['prompt']
    assert '派生重复标记' not in preview['prompt'] and '错误递归标记' not in preview['prompt']


def test_old_anchor_and_roundtrip_preserve_policy():
    state = SessionState(meta=SessionMeta(world_runtime_policy='compiled'))
    projection = current_state_projection(state)
    state.meta.world_runtime_policy = 'raw'
    apply_projection(state, projection)
    assert state.meta.world_runtime_policy == 'compiled'
    del projection['meta']['world_runtime_policy']
    apply_projection(state, projection)
    assert state.meta.world_runtime_policy == 'legacy_full'


def test_alternate_greeting_is_local_to_story_and_validated(mrp_client):
    cid = character(mrp_client)
    response = mrp_client.post('/api/v1/sessions', json={'character_ids': [cid], 'greeting_choices': {cid: 2}})
    assert response.status_code == 200, response.text
    state = response.json()
    assert state['characters'][0]['card']['first_mes'] == '黎明迎接'
    assert any(row['content'] == '黎明迎接' for row in state['messages'])
    assert mrp_client.get(f'/api/v1/characters/{cid}').json()['card']['first_mes'] == '默认迎接'
    before = set(mrp_client.app.state.container.runners)
    bad = mrp_client.post('/api/v1/sessions', json={'character_ids': [cid], 'greeting_choices': {cid: 9}})
    assert bad.status_code == 400 and set(mrp_client.app.state.container.runners) == before
