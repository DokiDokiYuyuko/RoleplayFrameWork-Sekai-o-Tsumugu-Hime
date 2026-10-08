from __future__ import annotations

import asyncio
import threading

import pytest

from mrp.orchestrator.assist import OptionChoice
from mrp.shared.models import GroupActor, Message, TokenUsage
from mrp.tests.test_integration import make_runner


class CaptureAssist:
    usage = TokenUsage()

    def __init__(self, *, entered=None, release=None):
        self.material = None
        self.entered = entered
        self.release = release

    def generate(self, material):
        self.material = material
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(5)
        return [OptionChoice(text="\u4f60\u4f1a\u56de\u5e94\u8fd9\u53e5\u8bdd")]


def _append(runner, *, actor, seq, content, kind="roleplay", visible_to="all"):
    message = Message(
        session_id=runner.state.meta.id,
        seq=seq,
        turn=1,
        actor=actor,
        content=content,
        kind=kind,
        visible_to=visible_to,
    )
    runner.state.messages.append(message)
    return message


def test_material_and_prompt_keep_latest_reply_complete_and_name_group():
    runner, _ = make_runner()
    runner.state.groups.append(GroupActor(id="group-c", label="\u6cb3\u8fb9\u5546\u8d29", scene_id="scene-x", joined_seq=0))
    _append(runner, actor="char-a", seq=0, content="\u65e9\u671f\u80cc\u666f")
    latest_text = "\u8fd9\u662f\u6700\u65b0\u56de\u590d\u7684\u5b8c\u6574\u6b63\u6587" * 40
    _append(runner, actor="group-c", seq=1, content=latest_text)

    material = runner.assist_builder.turn_material()
    assert material.anchor_message_id == runner.state.messages[-1].id
    assert material.anchor_label == "\u6cb3\u8fb9\u5546\u8d29"
    assert material.anchor_text == latest_text
    assert all("\u6700\u65b0\u56de\u590d" not in line for line in material.recent_lines)

    generator = CaptureAssist()
    from mrp.orchestrator.assist import AssistGenerator
    captured = {}
    def call(messages):
        captured["messages"] = messages
        return "[]"
    AssistGenerator(llm_call=call).generate(material)
    request = captured["messages"][1]["content"]
    assert latest_text in request
    assert "\u6cb3\u8fb9\u5546\u8d29" in request


def test_scene_reply_after_transition_is_treated_as_current_exchange():
    runner, _ = make_runner()
    _append(runner, actor="director", seq=0, content="\u5207\u6362\u5230\u6d1e\u7a74", kind="scene")
    reply = _append(runner, actor="char-a", seq=1, content="\u65b0\u573a\u666f\u91cc\u7684\u56de\u590d")
    assert runner.assist_builder.cold_start_material() is None
    assert runner.assist_builder.turn_material().anchor_message_id == reply.id


@pytest.mark.asyncio
async def test_candidate_generation_rejects_result_if_latest_message_changes():
    runner, _ = make_runner()
    runner.state.meta.options_enabled = True
    original = _append(runner, actor="char-a", seq=0, content="\u539f\u59cb\u56de\u590d")
    entered, release = threading.Event(), threading.Event()
    generator = CaptureAssist(entered=entered, release=release)
    runner.assist_generator = generator

    task = asyncio.create_task(runner.generate_candidates())
    assert await asyncio.to_thread(entered.wait, 3)
    assert generator.material.anchor_message_id == original.id
    async with runner.runtime.turn_lock:
        _append(runner, actor="char-b", seq=1, content="\u751f\u6210\u4e2d\u65b0\u5230\u7684\u56de\u590d")
    release.set()
    result = await task
    assert result["options"] == []
    assert result["reason"] == "stale"
    assert result["anchor_message_id"] == original.id


@pytest.mark.asyncio
async def test_candidate_batch_returns_branch_and_anchor_metadata():
    runner, _ = make_runner()
    runner.state.meta.options_enabled = True
    latest = _append(runner, actor="char-a", seq=0, content="\u6700\u65b0\u7684\u53ef\u89c1\u56de\u590d")
    generator = CaptureAssist()
    runner.assist_generator = generator
    result = await runner.generate_candidates()
    assert result["branch_id"] == runner.state.meta.id
    assert result["anchor_message_id"] == latest.id
    assert result["anchor_fingerprint"] == latest.fingerprint
    assert result["anchor_label"] == "\u6797\u6eaa"
    assert result["options"][0]["text"] == "\u4f60\u4f1a\u56de\u5e94\u8fd9\u53e5\u8bdd"
