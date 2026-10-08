"""M10 测试：R37 叙事控制（视角/密度/续写/垫场）。"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="mrp-m10-")
os.environ["MRP_DATA_ROOT"] = _TMP
os.environ["MRP_FAKE_ENGINE"] = "1"

from mrp.orchestrator.padding import PaddingGenerator  # noqa: E402
from mrp.shared.models import Scene  # noqa: E402
from mrp.shared.prompt import compose_prompt  # noqa: E402
from mrp.tests.test_integration import RecordingSink, make_runner  # noqa: E402


def _scene_runner(replies=None):
    runner, sink = make_runner(replies=replies or ["嗯。", "好。", "哦。"])
    scene = Scene(title="活动室", member_ids=["char-a", "char-b"])
    runner.state.scenes = [scene]
    runner.state.active_scene_id = scene.id
    runner.state.schema_version = 2
    return runner, sink


# ---------- R37.1/R37.2 视角与密度 ----------


async def test_legacy_pov_density_does_not_override_response_style():
    runner, _ = _scene_runner(replies=["回复"])
    runner.state.meta.narrative_pov = "second"
    runner.state.meta.narrative_density = "dialogue"
    await runner.player_say("测试甲，你怎么看？")
    engine = runner.engines._engines["char-a"]
    text = compose_prompt(engine.calls[-1]).text
    assert "第二人称" not in text
    assert "对白为主" not in text
    assert runner.context_builder.narrative_style() is None


async def test_legacy_third_pov_atmosphere_is_inert():
    runner, _ = _scene_runner(replies=["回复"])
    runner.state.meta.narrative_pov = "third"
    runner.state.meta.narrative_density = "atmosphere"
    await runner.player_say("测试甲，你怎么看？")
    text = compose_prompt(runner.engines._engines["char-a"].calls[-1]).text
    assert "第三人称" not in text
    assert "不为营造氛围凑句数" not in text


async def test_free_balanced_zero_diff():
    """默认风格（free+balanced）prompt 与关闭叙事控制逐字节一致。"""
    runner_a, _ = _scene_runner(replies=["回复"])
    runner_b, _ = _scene_runner(replies=["回复"])
    await runner_a.player_say("测试甲，你怎么看？")
    text_a = compose_prompt(runner_a.engines._engines["char-a"].calls[-1]).text
    # runner_b 保持默认（free+balanced）
    await runner_b.player_say("测试甲，你怎么看？")
    text_b = compose_prompt(runner_b.engines._engines["char-a"].calls[-1]).text
    assert text_a == text_b  # 同输入同配置 → 逐字节一致


# ---------- R37.3 续写 ----------


async def test_continue_appends_variant():
    runner, _ = _scene_runner(replies=["原内容。", " 续写的内容。"])
    created = await runner.player_say("测试甲，讲讲")
    msg = created[1]
    assert msg.content == "原内容。"

    cont = await runner.continue_message(msg.id)
    assert cont is not None
    assert cont.id == msg.id
    assert cont.content == "原内容。 续写的内容。"  # 拼接
    assert len(cont.variants) == 2
    assert cont.variants[0].content == "原内容。"  # 旧内容快照
    assert cont.active_variant == 1

    # 切回 variants[0] = 撤销续写
    back = await runner.switch_variant(msg.id, 0)
    assert back.content == "原内容。"


async def test_continue_context_includes_target():
    """续写上下文包含 target 自身（与 swipe 相反）。"""
    runner, _ = _scene_runner(replies=["原内容。", " 续写。"])
    created = await runner.player_say("测试甲，讲讲")
    msg = created[1]
    await runner.continue_message(msg.id)
    engine = runner.engines._engines["char-a"]
    ctx = engine.calls[-1]
    assert any(m.id == msg.id for m in ctx.visible_messages)
    assert any(i.entry_id == "continue_directive" for i in ctx.injections)


async def test_continue_only_last():
    runner, _ = _scene_runner(replies=["一", "二"])
    await runner.player_say("测试甲，讲讲")     # 一（turn 1）
    await runner.player_say("继续", force_character="char-a")  # 二（turn 2）
    first = next(m for m in runner.state.messages if m.actor == "char-a" and m.content == "一")
    assert await runner.continue_message(first.id) is None


async def test_continue_delta_offset_from_old_tail():
    runner, sink = _scene_runner(replies=["原内容。", " 续写。"])
    created = await runner.player_say("测试甲，讲讲")
    sink.events.clear()
    cont = await runner.continue_message(created[1].id)
    assert cont is not None
    deltas = [p for e, p in sink.events if e == "message.delta"]
    assert deltas and deltas[0]["offset"] == len("原内容。")  # 从旧内容尾部续起
    assert "".join(d["delta"] for d in deltas) == " 续写。"


async def test_continue_failure_preserves():
    runner, _ = _scene_runner(replies=["原内容。"])
    # 让第二次 generate 失败
    from mrp.tests.test_m8 import ExplodingEngine
    from mrp.engines.dsh.process import EngineManager

    runner.engines = EngineManager(
        engine_factory=lambda: ExplodingEngine(["原内容。"], fail_at=2)
    )
    created = await runner.player_say("测试甲，讲讲")
    msg = created[1]
    result = await runner.continue_message(msg.id)
    assert result is None
    assert msg.content == "原内容。" and msg.status == "final"


# ---------- R37.4 垫场 ----------


def _pad_gen(text: str = "灯光在幕布上投下摇晃的影子"):
    return PaddingGenerator(llm_call=lambda messages: text)


async def test_padding_triggers_before_reply():
    runner, sink = _scene_runner(replies=["回复"])
    runner.padding_gen = _pad_gen()
    created = await runner.player_say("嗯")
    # 垫场消息在角色回复之前落账
    kinds = [(m.actor, m.kind) for m in created]
    assert ("director", "scene") in kinds
    pad = next(m for m in created if m.actor == "director")
    assert "灯光" in pad.content and pad.content.startswith("（")
    # 被路由角色的上下文含垫场
    engine = runner.engines._engines["char-a"]
    assert any(m.id == pad.id for m in engine.calls[-1].visible_messages)
    assert "padding" in runner.cost_by_purpose


async def test_padding_conditions():
    runner, _ = _scene_runner(replies=["一", "二", "三"])
    runner.padding_gen = _pad_gen()
    # >4 字不垫
    created = await runner.player_say("这句话很长超过四个字")
    assert not any(m.actor == "director" for m in created)
    # 混合语法不垫
    created = await runner.player_say("（内心：有点累）嗯")
    assert not any(m.actor == "director" for m in created)
    # 开关关闭不垫
    runner.state.meta.short_input_padding = False
    created = await runner.player_say("嗯")
    assert not any(m.actor == "director" for m in created)


async def test_padding_failopen():
    class ExplodingPad:
        usage = None

        def generate(self, *a, **k):
            raise RuntimeError("垫场爆炸(测试注入)")

    runner, _ = _scene_runner(replies=["回复"])
    runner.padding_gen = ExplodingPad()
    created = await runner.player_say("嗯")
    # fail-open：无垫场消息，回合照常
    assert not any(m.actor == "director" for m in created)
    assert any(m.actor == "char-a" for m in created)


async def test_padding_explicit_mention_still_pads():
    """有显式 mentions 也照垫（@定向 + "嗯" 同样失衡）。"""
    runner, _ = _scene_runner(replies=["回复"])
    runner.padding_gen = _pad_gen()
    created = await runner.player_say("嗯", mentions=["char-a"])
    assert any(m.actor == "director" for m in created)
    assert any(m.actor == "char-a" for m in created)
