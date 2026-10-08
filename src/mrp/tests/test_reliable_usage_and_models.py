"""Synthetic receipts, character overrides and completion survive lifecycle changes."""
import asyncio

from mrp.server.container import AppContainer, AppContainerConfig
from mrp.shared.models import Character, CharacterCard, SessionState, SessionMeta, TokenUsage
from mrp.orchestrator.completion import completion_state


def test_receipts_survive_without_story_commit(tmp_path):
    async def scenario():
        root = tmp_path / "data"
        first = AppContainer(data_root=root, config=AppContainerConfig(fake_mode=True))
        character = Character(card=CharacterCard(name="合成角色"))
        await first.save_character(character)
        runner = await first.create_session("合成", [character.id], "", [])
        await first.persist_session(runner)
        sid = runner.state.meta.id
        runner.turns.track_cost(character.id, "main-model", TokenUsage(input_tokens=10, output_tokens=3))
        runner.turns.track_purpose_cost("memory", TokenUsage(input_tokens=5, output_tokens=2))
        runner.turns.track_purpose_cost("continue", TokenUsage(input_tokens=10, output_tokens=3))
        await first.aclose()
        second = AppContainer(data_root=root, config=AppContainerConfig(fake_mode=True))
        restored = await second.load_session(sid)
        report = restored.cost_report()
        assert report["total"] == {"input_tokens": 15, "output_tokens": 5, "cached_tokens": 0}
        assert report["call_count"] == 2
        await second.persist_session(restored)
        # Loading receipts already present in a saved state must not count twice.
        state = await second.sessions.load_state(sid)
        reattached = second._make_runner(state)
        assert reattached.cost_report()["call_count"] == 2
        await reattached.aclose()
        await second.aclose()
    asyncio.run(scenario())


def test_runtime_role_override_and_inheritance(mrp_container):
    explicit = Character(card=CharacterCard(name="指定"))
    explicit.llm.model = "another/model"
    explicit.llm.base_url = "https://different.example/v1"
    explicit.llm.inherit_model = False
    explicit.llm.inherit_base_url = False
    inherited = Character(card=CharacterCard(name="继承"))
    mrp_container._apply_global_llm_settings(explicit)
    mrp_container._apply_global_llm_settings(inherited)
    assert explicit.llm.model == "another/model"
    assert explicit.llm.base_url == "https://different.example/v1"
    assert explicit.llm.api_key_env != mrp_container.config.api_key_env
    mrp_container.config.model = "new/default"
    mrp_container.config.base_url = "https://active.example/v1"
    mrp_container._apply_global_llm_settings(explicit)
    mrp_container._apply_global_llm_settings(inherited)
    assert explicit.llm.model == "another/model"
    assert inherited.llm.model == "new/default"
    assert inherited.llm.base_url == "https://active.example/v1"


def test_provider_completion_reasons_are_explicit():
    assert completion_state("length") == "truncated"
    assert completion_state("content_filter") == "filtered"
    assert completion_state("stop") == "complete"
    assert completion_state(None) == "unknown"
