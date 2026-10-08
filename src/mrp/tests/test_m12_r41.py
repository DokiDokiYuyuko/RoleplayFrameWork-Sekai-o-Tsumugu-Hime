"""M12-R41 辅助候选（**手动触发**）集成测试。

2026-09-24 修订：候选不再自动生成（T1/T2/T3 触发、缓存、额度全部退役），
唯一入口 = `SessionRunner.generate_candidates()`（POST /assist/candidates）。
覆盖：冷启动上下文自选（open/scene）、回合上下文（turn）、零自动、开关、
失败静默、空会话、成本分列、每次点击都是全新生成。
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="mrp-m12-")
os.environ["MRP_DATA_ROOT"] = _TMP
os.environ["MRP_FAKE_ENGINE"] = "1"

from mrp.engines.dsh.process import EngineManager  # noqa: E402
from mrp.engines.fake import FakeEngine  # noqa: E402
from mrp.orchestrator.assist import OptionChoice  # noqa: E402
from mrp.orchestrator.session import SessionRunner  # noqa: E402
from mrp.shared.models import (  # noqa: E402
    Character,
    CharacterCard,
    Scene,
    SessionMeta,
    SessionState,
    TokenUsage,
)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, session_id: str, event: str, payload: dict, *, lossy: bool = False) -> None:
        self.events.append((event, payload))


class FakeAssist:
    """确定性 3 条（第 N 次调用带序号，验证每次点击都是新生成）+ 计数/失败开关/usage。"""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.last_material = None
        self.usage = TokenUsage()

    def generate(self, material):
        self.calls += 1
        self.last_material = material
        if self.fail:
            return []
        target = material.characters[0] if material.characters else None
        self.usage = TokenUsage(input_tokens=11, output_tokens=7)
        return [
            OptionChoice(
                text=f"（看向{target.name if target else '四周'}）第 {self.calls} 批开场。",
                mention_character_id=target.id if target else None,
                kind="hook",
            ),
            OptionChoice(text=f"我先四处看看（第 {self.calls} 批）。", kind="push"),
            OptionChoice(text="（内心：先观察一下。）", kind="soft"),
        ]


def make_runner(assist=None):
    char_a = Character(
        id="char-a", card=CharacterCard(name="测试甲", first_mes="来了。"), talkativeness=0.8
    )
    char_b = Character(
        id="char-b", card=CharacterCard(name="测试乙", first_mes="欢迎。"), talkativeness=0.3
    )
    scene = Scene(title="开场", member_ids=["char-a", "char-b"])
    state = SessionState(
        meta=SessionMeta(id="sess-m12", title="候选", character_ids=["char-a", "char-b"]),
        characters=[char_a, char_b],
        scenes=[scene],
        active_scene_id=scene.id,
    )
    sink = RecordingSink()
    runner = SessionRunner(
        state,
        EngineManager(engine_factory=lambda: FakeEngine(replies=["嗯。"])),
        sink=sink,
        rng_seed=7,
        assist_generator=assist,
    )
    return runner, sink


# ---------- 上下文自选 ----------


async def test_cold_start_open_generates_kickoff():
    gen = FakeAssist()
    runner, _ = make_runner(gen)
    await runner.open_round()

    res = await runner.generate_candidates()
    assert res["source"] == "kickoff"
    assert res["kickoff_kind"] == "open"
    assert res["turn"] == 1
    assert res["scene_id"] == runner.state.active_scene_id
    assert len(res["options"]) == 3
    assert res["options"][0]["mention_character_id"] == "char-a"
    assert res["options"][0]["kind"] == "hook"
    assert gen.last_material.kind == "open"
    assert gen.last_material.opening_lines  # first_mes 进入材料


async def test_scene_switch_generates_scene_kickoff():
    gen = FakeAssist()
    runner, _ = make_runner(gen)
    await runner.open_round()
    await runner.switch_scene_manual("天台", "夜风很大")

    res = await runner.generate_candidates()
    assert res["source"] == "kickoff"
    assert res["kickoff_kind"] == "scene"
    assert res["scene_id"] == runner.state.active_scene_id
    assert gen.last_material.transition  # 过渡描写进入材料


async def test_turn_context_generates_turn_candidates():
    gen = FakeAssist()
    runner, _ = make_runner(gen)
    runner.state.meta.options_style = "dialogue"
    await runner.open_round()
    await runner.player_say("大家好。", mentions=["char-a"])

    res = await runner.generate_candidates()
    assert res["source"] == "turn"
    assert "kickoff_kind" not in res
    assert len(res["options"]) == 3
    m = gen.last_material
    assert m.kind == "turn"
    assert m.style == "dialogue"
    assert m.recent_lines and m.characters  # 最近对话 + 在场名单进入材料
    assert not m.opening_lines and not m.transition


async def test_empty_session_reason_empty():
    runner, _ = make_runner(FakeAssist())
    res = await runner.generate_candidates()
    assert res == {"options": [], "reason": "empty"}


# ---------- 零自动（2026-09-24 修订的核心） ----------


async def test_no_auto_generation_anywhere():
    """开场 / 回合 / 切场景都不再自动生成——生成器零调用。"""
    gen = FakeAssist()
    runner, sink = make_runner(gen)
    await runner.open_round()
    await runner.player_say("我们走吧。", mentions=["char-a"])
    await runner.switch_scene_manual("天台", "夜风")
    assert gen.calls == 0
    assert not any(e == "options.ready" for e, _ in sink.events)  # 事件也不再发


async def test_disabled_zero_calls():
    gen = FakeAssist()
    runner, _ = make_runner(gen)
    runner.state.meta.options_enabled = False
    await runner.open_round()
    res = await runner.generate_candidates()
    assert res == {"options": [], "reason": "disabled"}
    assert gen.calls == 0


async def test_failure_silent():
    gen = FakeAssist()
    gen.fail = True
    runner, _ = make_runner(gen)
    await runner.open_round()
    res = await runner.generate_candidates()
    assert res["options"] == []
    assert res["reason"] == "failed"
    assert res["branch_id"] == runner.state.meta.id
    assert res["anchor_message_id"]
    assert gen.calls == 1  # 调用过但静默返回


# ---------- 每次点击都是全新生成（无缓存/无额度） ----------


async def test_each_call_regenerates():
    gen = FakeAssist()
    runner, _ = make_runner(gen)
    await runner.open_round()
    first = await runner.generate_candidates()
    second = await runner.generate_candidates()
    assert gen.calls == 2
    assert first["options"][0]["text"] != second["options"][0]["text"]  # 批次替换


async def test_cost_tracked_as_assist():
    gen = FakeAssist()
    runner, _ = make_runner(gen)
    await runner.open_round()
    await runner.generate_candidates()
    await runner.generate_candidates()
    by_purpose = runner.cost_report()["by_purpose"]["assist"]
    assert by_purpose["input_tokens"] == 22  # 每次点击独立计账
    assert by_purpose["output_tokens"] == 14


# ---------- R40 意图代笔 ----------


async def test_draft_candidates_uses_independent_writer():
    import json
    from mrp.orchestrator.writing_assistant import WritingGenerator
    gen = FakeAssist()
    runner, _ = make_runner(gen)
    seen = []
    def write(messages):
        seen.append(messages)
        return json.dumps([{"title": str(i), "text": "完整方案" + str(i)} for i in range(3)])
    runner.writing_generator = WritingGenerator(llm_call=write)
    await runner.open_round()
    res = await runner.draft_candidates("安排旅行")
    assert res["source"] == "draft" and len(res["options"]) == 3
    assert "安排旅行" in seen[0][-1]["content"]
    assert gen.calls == 0


async def test_draft_works_with_candidates_disabled_and_empty_session():
    from mrp.orchestrator.writing_assistant import WritingGenerator
    gen = FakeAssist()
    runner, _ = make_runner(gen)
    runner.writing_generator = WritingGenerator(fake=True)
    runner.state.meta.options_enabled = False
    res = await runner.draft_candidates("设计开场")
    assert len(res["options"]) == 3 and gen.calls == 0
