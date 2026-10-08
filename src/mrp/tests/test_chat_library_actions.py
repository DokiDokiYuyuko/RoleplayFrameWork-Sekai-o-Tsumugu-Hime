"""Synthetic edit-scope and selected migration coverage. No model calls or user files."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from mrp.importers.bundle import build_bundle
from mrp.importers.exporters import lorebook_to_st_dict
from mrp.importers.lorebook import import_lorebook
from mrp.shared.models import Character, CharacterAuthoringSource, CharacterCard, Lorebook, LorebookEntry
from mrp.simple_chat import PlainChat, PlainMessage
from mrp.scenarios.repository import ScenarioRepository


def assets(client):
    container = client.app.state.container
    book = Lorebook(name="Synthetic book", tags=["test"])
    character = Character(card=CharacterCard(name="Synthetic guide"), bound_lorebook_ids=[book.id],
                          source_world_id="synthetic-world", authoring_source=CharacterAuthoringSource(text="Synthetic private manuscript"))
    container.characters[character.id] = character
    container.lorebooks[book.id] = book
    return container, character, book


def sidecar(data, character_id):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return json.loads(archive.read(f"characters/{character_id}.mrp.json"))


def test_lorebook_tags_survive_single_st_export_and_legacy_files_default_empty():
    book = Lorebook(name='Synthetic tags',tags=['one','two'])
    assert import_lorebook(lorebook_to_st_dict(book)).tags == ['one','two']
    assert import_lorebook({'entries':{}}).tags == []


def test_scenario_summary_reports_actual_update_and_legacy_fallback_is_stable(mrp_client):
    _, character, _ = assets(mrp_client)
    response = mrp_client.post('/api/v1/scenarios', json={'title':'Synthetic scenario','character_ids':[character.id]})
    assert response.status_code == 200, response.text
    scenario = response.json()
    changed = mrp_client.patch(f"/api/v1/scenarios/{scenario['id']}",json={'description':'Revised','expected_revision':scenario['revision']}).json()
    summary = mrp_client.get('/api/v1/scenarios').json()[0]
    assert summary['updated_at'] == changed['updated_at']
    assert summary['updated_at'] != scenario['updated_at']
    legacy = dict(changed)
    legacy.pop('updated_at')
    restored = ScenarioRepository._parse_package(json.dumps(legacy))
    assert restored.updated_at == restored.created_at


def test_selected_preview_refreshes_and_export_checks_confirmed_revisions(mrp_client):
    container, character, book = assets(mrp_client)
    input = {"characters": [{"id": character.id, "expected_revision": 1}]}
    preview = mrp_client.post('/api/v1/bundle/export-selected/preview', json=input)
    assert preview.status_code == 200, preview.text
    rows = preview.json()['items']
    assert [row['id'] for row in rows] == [character.id, book.id]
    assert rows[1]['dependency'] is True
    assert container.characters[character.id].source_world_id == 'synthetic-world'
    character.revision = 2
    stale = mrp_client.post('/api/v1/bundle/export-selected', json=input)
    assert stale.status_code == 409
    refreshed = mrp_client.post('/api/v1/bundle/export-selected/preview', json=input)
    assert refreshed.json()['items'][0]['revision'] == 2
    input['characters'][0]['expected_revision'] = 2
    response = mrp_client.post('/api/v1/bundle/export-selected', json=input)
    assert response.status_code == 200, response.text
    saved = sidecar(response.content, character.id)
    assert saved['source_world_id'] is None and 'authoring_source' not in saved
    assert saved['bound_lorebook_ids'] == [book.id]
    assert 'Synthetic private manuscript' not in str(response.content)


def test_selected_export_prunes_omitted_bindings_and_rejects_missing_dependencies(mrp_client):
    container, character, book = assets(mrp_client)
    input = {"characters": [{"id": character.id, "expected_revision": 1}], "include_bound_lorebooks": False}
    response = mrp_client.post('/api/v1/bundle/export-selected', json=input)
    assert sidecar(response.content, character.id)['bound_lorebook_ids'] == []
    assert character.bound_lorebook_ids == [book.id]
    del container.lorebooks[book.id]
    input['include_bound_lorebooks'] = True
    assert mrp_client.post('/api/v1/bundle/export-selected/preview', json=input).status_code == 409


@pytest.mark.parametrize('embedded_format', ['list', 'dict'])
def test_selected_share_removes_author_entries_and_provenance_preserves_public_rules_and_private_backup(mrp_client, embedded_format):
    container, character, book = assets(mrp_client)
    private_marker = 'SYNTHETIC-PRIVATE-AUTHOR-QUOTE'
    book.entries = [
        LorebookEntry(uid=7, content=private_marker + '-body', constant=True,
                      extensions={'mrp.runtime_scope': 'author'}),
        LorebookEntry(uid=23, keys=['Public bridge'], content='SAFE-PUBLIC-BRIDGE', enabled=True,
                      extensions={'mrp.runtime_scope': 'shared',
                                  'mrp.archive_sources': [{'source_id': 'private-source', 'quote': private_marker}],
                                  'mrp.archive_source': {'archive_id': 'private-source', 'quote': private_marker},
                                  'mrp.generated': {'payload_hash': 'baseline', 'before_payload': private_marker},
                                  'sticky': 2, 'vendor.custom_rule': {'retain': True},
                                  'mrp.import_compatibility': {'unexecuted_rules': ['vendor.custom_rule'],
                                      'original_entry': {'content': 'SAFE-PUBLIC-BRIDGE', 'vendor.custom_rule': {'retain': True},
                                                         'mrp.archive_sources': [{'quote': private_marker}]}}}),
        LorebookEntry(uid=41, content='SAFE-LEGACY-ENTRY', extensions={'foreign-rule': 'keep'}),
    ]
    before = book.model_dump(mode='json')
    character.card.extensions = {'foreign-card-rule': 'keep', 'character_book': {'name': 'Embedded', 'entries': [
        {'id': 78, 'content': private_marker + '-embedded', 'extensions': {'mrp.runtime_scope': 'author'}},
        {'id': 81, 'content': 'SAFE-PUBLIC-EMBEDDED', 'extensions': {
            'mrp.archive_source': {'quote': private_marker}, 'vendor.custom_rule': {'retain': True}}},
    ]}}
    if embedded_format == 'dict':
        character.card.extensions['character_book']['entries'] = dict(zip(['5', '9'], character.card.extensions['character_book']['entries']))
    character_before = character.model_dump(mode='json')
    request = {'characters': [{'id': character.id, 'expected_revision': character.revision}],
               'lorebooks': [{'id': book.id, 'expected_revision': book.revision}], 'include_bound_lorebooks': False}
    response = mrp_client.post('/api/v1/bundle/export-selected', json=request)
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        decoded = {name: archive.read(name).decode('utf-8') for name in archive.namelist()}
        assert private_marker not in '\n'.join(decoded.values())
        exported = json.loads(decoded[f'lorebooks/{book.id}.json'])
        rows = list(exported['entries'].values())
        assert [row['uid'] for row in rows] == [23, 41]
        assert rows[0]['content'] == 'SAFE-PUBLIC-BRIDGE'
        assert rows[0]['sticky'] == 2 and rows[0]['vendor.custom_rule'] == {'retain': True}
        assert rows[0]['mrp.import_compatibility']['unexecuted_rules'] == ['vendor.custom_rule']
        assert rows[0]['mrp.import_compatibility']['original_entry']['vendor.custom_rule'] == {'retain': True}
        assert rows[1]['foreign-rule'] == 'keep'
        card = json.loads(decoded[f'characters/{character.id}.json'])['data']
        assert card['extensions']['foreign-card-rule'] == 'keep'
        embedded = card['extensions']['character_book']['entries']
        if embedded_format == 'dict':
            assert list(embedded) == ['9']
            embedded = list(embedded.values())
        assert len(embedded) == 1 and embedded[0]['id'] == 81
        assert embedded[0]['content'] == 'SAFE-PUBLIC-EMBEDDED'
        assert embedded[0]['extensions']['vendor.custom_rule'] == {'retain': True}
    preview = mrp_client.post('/api/v1/bundle/export-selected/preview', json=request).json()
    assert preview['exclusions']['author_entries'] == 2
    assert preview['exclusions']['provenance_entries'] == 2
    assert any('2 条作者专用词条' in notice for notice in preview['notices'])
    assert any('2 条词条的原稿引用与整理记录' in notice for notice in preview['notices'])
    assert container.lorebooks[book.id].model_dump(mode='json') == before
    assert container.characters[character.id].model_dump(mode='json') == character_before
    private = mrp_client.get('/api/v1/bundle/export')
    assert private.status_code == 200, private.text
    with zipfile.ZipFile(io.BytesIO(private.content)) as archive:
        private_book = json.loads(archive.read(f'lorebooks/{book.id}.json'))
        assert len(private_book['entries']) == 3
        assert private_marker in json.dumps(private_book)
        assert private_book['entries']['1']['mrp.generated']['payload_hash'] == 'baseline'
        assert json.loads(archive.read(f'characters/{character.id}.mrp.json'))['authoring_source']['text']
        private_card = json.loads(archive.read(f'characters/{character.id}.json'))['data']
        assert len(private_card['extensions']['character_book']['entries']) == 2
        assert private_marker in json.dumps(private_card)


def test_selected_preview_explains_book_that_becomes_empty_without_changing_local_entries(mrp_client):
    _, _, book = assets(mrp_client)
    book.entries = [LorebookEntry(uid=12, content='SYNTHETIC-AUTHOR-ONLY',
                                  extensions={'mrp.runtime_scope': 'author'})]
    request = {'lorebooks': [{'id': book.id, 'expected_revision': book.revision}]}
    preview = mrp_client.post('/api/v1/bundle/export-selected/preview', json=request).json()
    assert preview['exclusions'] == {'author_entries': 1, 'provenance_entries': 0}
    assert any('分享后没有词条' in notice for notice in preview['notices'])
    with zipfile.ZipFile(io.BytesIO(build_bundle([], [book], share=True))) as archive:
        assert json.loads(archive.read(f'lorebooks/{book.id}.json'))['entries'] == {}
    assert len(book.entries) == 1 and book.entries[0].uid == 12


def test_private_bundle_preserves_source_and_import_restores_book_binding_with_warning(mrp_client):
    container, character, book = assets(mrp_client)
    data = build_bundle([character], [book])
    saved = sidecar(data, character.id)
    assert saved['authoring_source']['text'] == character.authoring_source.text
    assert saved['source_world_id'] == character.source_world_id
    response = mrp_client.post('/api/v1/bundle/import', files={'file': ('synthetic.zip', data, 'application/zip')})
    assert response.status_code == 200, response.text
    report = response.json()
    imported = container.characters[report['characters'][0]['id']]
    assert imported.authoring_source.text == character.authoring_source.text
    assert imported.source_world_id is None
    assert imported.bound_lorebook_ids == [report['lorebooks'][0]['id']]
    assert container.lorebooks[report['lorebooks'][0]['id']].tags == ['test']
    assert report['characters'][0]['warnings']


def install_chat(client):
    service = client.app.state.container.simple_chats
    chat = PlainChat(gateway='https://example.invalid/v1', model='synthetic/model', messages=[
        PlainMessage(role='user', content='Synthetic question'),
        PlainMessage(role='assistant', content='First candidate', variants=['First candidate','Second candidate'], active_variant=0,
                     usage={'input_tokens':10}, variant_usages=[{'input_tokens':10},{'input_tokens':20}]),
        PlainMessage(role='user', content='Synthetic follow-up')])
    client.portal.call(service._save, chat)
    return service, chat


def test_simple_edit_requires_truncation_and_clears_candidates_only_after_confirmed_tail(mrp_client):
    service, chat = install_chat(mrp_client)
    url = f'/api/v1/simple-chats/{chat.id}/messages/{chat.messages[1].id}'
    stamp = chat.updated_at.isoformat()
    denied = mrp_client.patch(url, json={'content':'Edited', 'expected_updated_at':stamp})
    assert denied.status_code == 400
    confirmed = mrp_client.patch(url, json={'content':'Edited','truncate_after':True,'expected_updated_at':stamp})
    assert confirmed.status_code == 200, confirmed.text
    saved = confirmed.json()
    assert len(saved['messages']) == 2
    assert saved['messages'][1]['variants'] == ['Edited']
    assert saved['messages'][1]['variant_usages'] == [{}]
    assert saved['messages'][1]['usage'] == {}


def test_stale_simple_edit_and_delete_leave_new_tail_and_candidates_intact(mrp_client):
    service, chat = install_chat(mrp_client)
    stamp = chat.updated_at.isoformat()
    chat.messages.append(PlainMessage(role='assistant',content='New tail'))
    mrp_client.portal.call(service._save, chat)
    url = f'/api/v1/simple-chats/{chat.id}/messages/{chat.messages[1].id}'
    assert mrp_client.patch(url,json={'content':'Stale','truncate_after':True,'expected_updated_at':stamp}).status_code == 409
    assert mrp_client.request('DELETE',url,json={'truncate_after':True,'expected_updated_at':stamp}).status_code == 409
    saved = mrp_client.get(f'/api/v1/simple-chats/{chat.id}').json()
    assert len(saved['messages']) == 4
    assert saved['messages'][1]['variants'] == ['First candidate','Second candidate']
