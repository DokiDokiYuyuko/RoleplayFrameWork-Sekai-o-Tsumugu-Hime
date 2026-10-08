"""Appearance-only settings updates must preserve credentials, generation and prior choices."""
import json
import pytest
from mrp.settings import AppSettings, load_settings, save_settings

@pytest.mark.parametrize('style', ['none','star-ring','ripple','petals','rune','burst','ink','future-click'])
def test_click_effect_roundtrip(tmp_path, style):
    save_settings(tmp_path, AppSettings(appearance={'click_effect_id':style,'trail_id':'orbit'}))
    loaded=load_settings(tmp_path)
    assert loaded.appearance.click_effect_id==style
    assert loaded.appearance.trail_id=='orbit'

def test_legacy_and_damaged_click_keep_other_preferences(tmp_path):
    path=tmp_path/'settings.json'
    for appearance in ({'theme_id':'dark','trail_id':'starlight'},
                       {'theme_id':'dark','trail_id':'starlight','click_effect_id':'../bad'}):
        path.write_text(json.dumps({'model':'example/model','appearance':appearance}),encoding='utf-8')
        loaded=load_settings(tmp_path)
        assert loaded.appearance.click_effect_id=='none'
        assert loaded.appearance.theme_id=='dark'
        assert loaded.appearance.trail_id=='starlight'
        assert loaded.model=='example/model'

def test_click_patch_persists_without_refreshing_engines(mrp_client, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Pointer settings must not restart model engines')
    monkeypatch.setattr(mrp_client.app.state.container,'_refresh_llm_config',unexpected)
    before=mrp_client.get('/api/v1/settings').json()
    result=mrp_client.patch('/api/v1/settings',json={'appearance':{'click_effect_id':'rune','typography_id':'wenkai'}})
    assert result.status_code==200
    for field in ('model','gateway','engine','generation'):
        assert result.json()[field]==before[field]
    assert result.json()['appearance']['trail_id']==before['appearance']['trail_id']
    assert mrp_client.get('/api/v1/settings').json()['appearance']['click_effect_id']=='rune'
    assert mrp_client.patch('/api/v1/settings',json={'appearance':{'click_effect_id':'../bad'}}).status_code>=400
