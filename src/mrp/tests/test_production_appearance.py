import pytest
from mrp.settings import AppSettings, load_settings, save_settings


def test_new_appearance_roundtrip_and_unrelated_settings_preserved(mrp_client, monkeypatch):
    monkeypatch.setattr(mrp_client.app.state.container, '_refresh_llm_config',
                        lambda *args, **kwargs: pytest.fail('Appearance must not restart engines'))
    before = mrp_client.get('/api/v1/settings').json()
    patch = {'avatar_frame_id': 'silverwing', 'dialogue_avatar_size': 56,
             'primary_button_skin': False, 'secondary_button_skin': True,
             'card_ornaments': False, 'card_border': True, 'background_art': False,
             'portrait_placeholder': 'initial', 'reading_width': 'wide', 'reading_font_size': 22}
    result = mrp_client.patch('/api/v1/settings', json={'appearance': patch})
    assert result.status_code == 200, result.text
    loaded = mrp_client.get('/api/v1/settings').json()
    assert all(loaded['appearance'][key] == value for key, value in patch.items())
    assert all(loaded[key] == before[key] for key in ['engine', 'gateway', 'model', 'generation'])
    for value in ({'dialogue_avatar_size': 100}, {'reading_font_size': 25},
                  {'reading_width': 'unbounded'}, {'avatar_frame_id': '../private'}):
        assert mrp_client.patch('/api/v1/settings', json={'appearance': value}).status_code == 422


def test_old_appearance_gets_defaults_without_losing_saved_theme(tmp_path):
    save_settings(tmp_path, AppSettings(appearance={'theme_id': 'dark', 'density': 'compact'}))
    actual = load_settings(tmp_path).appearance
    assert actual.theme_id == 'dark' and actual.density == 'compact'
    assert actual.primary_button_skin and not actual.secondary_button_skin
    assert actual.avatar_frame_id == 'iris-wreath' and actual.reading_font_size == 18
