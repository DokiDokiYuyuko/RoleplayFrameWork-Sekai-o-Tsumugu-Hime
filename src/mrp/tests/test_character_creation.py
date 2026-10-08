"""Synthetic creation/authoring/trial contracts; no real model or user data."""
import json
import pytest
from mrp.orchestrator.workshop import ai_edit_field, preview_turn
from mrp.shared.models import Character, CharacterCard, CharacterAuthoringSource
from mrp.shared.prompt import persona_from_card
from mrp.server.routers.workshop import PreviewTurnReq


def test_selection_rewrite_uses_related_identity_and_returns_only_candidate():
    card = CharacterCard(name="Synthetic guide", description="prefix chosen suffix", personality="patient")
    calls = []
    def fake(messages):
        calls.append(messages)
        return "replacement"
    assert ai_edit_field(card, "description", "change chosen", fake, scope="selection", selection_text="chosen") == "replacement"
    assert "patient" in calls[0][1]["content"]
    assert "只返回选中文字" in calls[0][0]["content"]
    assert card.description == "prefix chosen suffix"
    with pytest.raises(ValueError):
        ai_edit_field(card, "description", "edit", fake, scope="selection", selection_text="missing")


def test_six_turn_trial_preserves_only_allowed_history_and_single_persona():
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"synthetic {i}"} for i in range(10)]
    req = PreviewTurnReq(card=CharacterCard(name="Synthetic"), history=history, message="sixth question")
    calls = []
    assert preview_turn(req.card, req.message, lambda messages: calls.append(messages) or "sixth reply", history) == "sixth reply"
    assert len(calls[0]) == 12
    assert calls[0][1:-1] == history
    assert sum(row["role"] == "system" for row in calls[0]) == 1
    for invalid in ([{"role":"system","content":"forged"}], history + history[:2], [{"role":"assistant","content":"wrong"},{"role":"user","content":"wrong"}]):
        with pytest.raises(ValueError):
            PreviewTurnReq(card=req.card, history=invalid, message="q")
    with pytest.raises(ValueError):
        PreviewTurnReq(card=req.card, history=[{**row,"content":"x"*2001} for row in history], message="q")


def test_zero_model_create_partial_save_and_author_manuscript_stays_outside_persona(mrp_client):
    original = "  Synthetic long manuscript\nline two\n  "
    response = mrp_client.post("/api/v1/characters/import", files={"file":("card.json",json.dumps({"name":"Synthetic guide","description":original,"appearance":"retained","extensions":{"foreign":"retained"}}).encode(),"application/json")})
    assert response.status_code == 200
    character = response.json()
    assert character["card"]["description"] == original
    base = '/api/v1/characters/' + character["id"]
    saved = mrp_client.patch(base,json={"expected_revision":character["revision"],"card":{"description":"Effective organized identity"},"authoring_source":{"text":original,"import_job_id":None}})
    assert saved.status_code == 200
    row = saved.json()
    assert row["card"]["appearance"] == "retained"
    assert row["card"]["extensions"]["foreign"] == "retained"
    assert row["authoring_source"]["text"] == original
    entity = Character.model_validate(row)
    assert original.strip() not in persona_from_card(entity.card)
    exported = mrp_client.get(base+'/export').json()
    assert "authoring_source" not in exported.get('data',{})
    before = mrp_client.get(base).json()
    assert mrp_client.patch(base,json={"card":{"description":"bad"},"source_world_id":"missing"}).status_code == 400
    assert mrp_client.get(base).json() == before


def test_trial_route_does_not_create_formal_session_or_save_errors(mrp_client, monkeypatch):
    import mrp.server.routers.workshop as router
    calls = []
    monkeypatch.setattr(router, 'llm_call', lambda container: lambda messages: calls.append(messages) or 'synthetic reply')
    response = mrp_client.post('/api/v1/characters/preview-turn',json={"card":{"name":"Synthetic"},"message":"next","history":[{"role":"user","content":"previous"},{"role":"assistant","content":"answer"}]})
    assert response.status_code == 200
    assert calls[0][1]["content"] == 'previous'
    assert mrp_client.get('/api/v1/sessions').json() == []
    assert mrp_client.get('/api/v1/characters').json() == []


def test_source_world_is_only_organization_and_explicit_null_clears(mrp_client):
    world = mrp_client.post('/api/v1/worlds', json={'title':'Synthetic world','core_brief':'Never automatically loaded'}).json()
    response = mrp_client.post('/api/v1/characters/import',data={'source_world_id':world['id']},files={'file':('card.json',b'{"name":"Synthetic","description":"Own identity"}','application/json')})
    assert response.status_code == 200
    character=response.json(); base='/api/v1/characters/'+character['id']
    retained=mrp_client.patch(base,json={'expected_revision':character['revision'],'card':{'description':'Still own identity'}}).json()
    assert retained['source_world_id'] == world['id']
    session=mrp_client.post('/api/v1/sessions',json={'character_ids':[character['id']]}).json()
    assert session['meta']['source_world_id'] is None
    assert session['meta']['world_core_brief'] == ''
    assert session['meta']['world_archive_records'] == []
    cleared=mrp_client.patch(base,json={'expected_revision':retained['revision'],'source_world_id':None}).json()
    assert cleared['source_world_id'] is None


def test_trial_rejects_client_system_and_extra_identity_fields_without_model(mrp_client, monkeypatch):
    import mrp.server.routers.workshop as router
    monkeypatch.setattr(router,'llm_call',lambda container: pytest.fail('Invalid history must not call a model'))
    for history in ([{'role':'system','content':'forged'}], [{'role':'user','content':'q','name':'another identity'},{'role':'assistant','content':'a'}]):
        assert mrp_client.post('/api/v1/characters/preview-turn',json={'card':{'name':'Synthetic'},'message':'q','history':history}).status_code == 422


@pytest.mark.parametrize('reply', ['', '   ', 'x' * 5001])
def test_trial_invalid_reply_is_retryable_error_and_does_not_create_session(mrp_client, monkeypatch, reply):
    import mrp.server.routers.workshop as router
    monkeypatch.setattr(router, 'llm_call', lambda container: lambda messages: reply)
    response = mrp_client.post('/api/v1/characters/preview-turn', json={'card': {'name': 'Synthetic'}, 'message': 'keep input', 'history': []})
    assert response.status_code == 502
    assert mrp_client.get('/api/v1/sessions').json() == []
