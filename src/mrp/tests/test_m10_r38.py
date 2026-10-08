"""M10 测试：R38 角色主动性（插话/接话开关制）。"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="mrp-m10-r38-")
os.environ["MRP_DATA_ROOT"] = _TMP
os.environ["MRP_FAKE_ENGINE"] = "1"

from mrp.orchestrator.director_llm import FakeDirectorJudge  # noqa: E402
from mrp.orchestrator.proactive import ProactiveEngine  # noqa: E402
from mrp.shared.models import Message, Scene  # noqa: E402
from mrp.tests.test_integration import make_runner  # noqa: E402


def _runner(judge=None, replies=None):
    runner, sink = make_runner(replies=replies or ["一", "二", "三", "四", "五"])
    scene = Scene(title="活动室", member_ids=["char-a", "char-b"])
    runner.state.scenes = [scene]
    runner.state.active_scene_id = scene.id
    runner.state.schema_version = 2
    if judge is not None:
        runner.director_judge = judge
    return runner, sink


def _char(runner, cid):
    return {c.id: c for c in runner.state.characters}[cid]


# ---------- R38.1 插话 ----------


async def test_interject_via_director():
    """开启者被导演点名追加进本轮 + Banner 理由 + 意愿钩子进导演输入（验收点）。"""
    judge = FakeDirectorJudge(mode="pick")
    runner, _ = _runner(judge=judge, replies=["甲说", "乙插话"])
    _char(runner, "char-b").interject_enabled = True

    created = await runner.player_say("今天天气不错")
    # 导演输入含意愿标记 + 性格钩子（验收）
    data = judge.calls[-1]
    brief_b = next(c for c in data.present if c.id == "char-b")
    assert brief_b.interject_hint is True
    assert brief_b.personality_hook  # 钩子非空
    brief_a = next(c for c in data.present if c.id == "char-a")
    assert brief_a.interject_hint is False
    # chosen 含插话者 + reasons 含 interject（Banner 显示理由）
    d = runner.state.director_log[-1]
    assert "char-b" in d.chosen and "char-a" in d.chosen
    cand_b = next(c for c in d.candidates if c.character_id == "char-b")
    assert "interject" in cand_b.reasons
    # 两个角色都发言
    actors = [m.actor for m in created if m.actor != "player"]
    assert "char-a" in actors and "char-b" in actors


async def test_rules_mode_no_interject():
    """rules 模式（导演 LLM 不启用）→ 开启者也无插话。"""
    judge = FakeDirectorJudge(mode="pick")
    runner, _ = _runner(judge=judge, replies=["甲说"])
    _char(runner, "char-b").interject_enabled = True
    runner.state.meta.director_mode = "rules"
    created = await runner.player_say("今天天气不错")
    assert judge.calls == []  # rules 模式零导演调用
    actors = [m.actor for m in created if m.actor != "player"]
    assert "char-b" not in actors


# ---------- R38.2 接话 ----------


async def test_followup_chain():
    """他人发言后开启者链式反应。"""
    judge = FakeDirectorJudge(mode="pick")
    runner, _ = _runner(judge=judge, replies=["甲先说", "乙接话"])
    _char(runner, "char-b").followup_enabled = True

    created = await runner.player_say("今天天气不错")
    # 甲被导演选中发言 → 乙接话
    assert judge.followup_calls, "接话判定未触发"
    actors = [m.actor for m in created if m.actor != "player"]
    assert actors.count("char-b") == 1  # 接话一次
    # 主动发言分列
    assert "proactive" in runner.cost_by_purpose


async def test_all_off_no_followup_calls():
    """全员关闭：judge_followup 调用计数恒 0（零差异第三层断言）。"""
    judge = FakeDirectorJudge(mode="pick")
    runner, _ = _runner(judge=judge, replies=["甲说", "乙说"])
    # 全员开关默认关
    created = await runner.player_say("今天天气不错")
    assert len(judge.followup_calls) == 0
    # 决策 chosen 与 R35 形状一致（无 interject 追加）
    d = runner.state.director_log[-1]
    assert d.chosen == ["char-a"]  # FakeDirectorJudge=pick 只选第一个在场者
    # 导演输入无意愿标记（零差异第一层：prompt 形状全等）
    data = judge.calls[-1]
    assert all(not c.interject_hint for c in data.present)


# ---------- 规则引擎单测（闸门穷举） ----------


def _mk_char(cid, *, followup=False, interject=False, present=True, muted=False):
    from mrp.shared.models import Character, CharacterCard

    return Character(
        id=cid, card=CharacterCard(name=cid, first_mes="嗨"),
        talkativeness=0.5, muted=muted, present=present,
        followup_enabled=followup, interject_enabled=interject,
    )


def test_proactive_interject_candidates():
    engine = ProactiveEngine()
    chars = [
        _mk_char("a", interject=True),
        _mk_char("b", interject=True, present=False),
        _mk_char("c", interject=True, muted=True),
        _mk_char("d"),
    ]
    assert [c.id for c in engine.interject_candidates(chars)] == ["a"]


def test_proactive_followup_gates():
    engine = ProactiveEngine()
    a, b, c = _mk_char("a"), _mk_char("b", followup=True), _mk_char("c", followup=True)
    history = [Message(id="m1", session_id="s", seq=0, turn=1, actor="player",
                       content="x", kind="roleplay", visible_to="all")]

    # 基本资格
    got = engine.followup_candidates("a", {"a"}, [a, b, c], history, 0, 0, 1)
    assert {x.id for x in got} == {"b", "c"}
    # 链深闸门
    assert engine.followup_candidates("a", {"a"}, [a, b, c], history, 2, 0, 1) == []
    # 全局上限闸门
    assert engine.followup_candidates("a", {"a"}, [a, b, c], history, 0, 1, 1) == []
    # 已发言排除
    assert engine.followup_candidates("a", {"a", "b"}, [a, b, c], history, 0, 0, 3) == [c]
    # 最后发言者本人排除
    got = engine.followup_candidates("b", {"a"}, [a, b, c], history, 0, 0, 3)
    assert "b" not in {x.id for x in got}
    # 连续发言排除：b 已连发 2 条
    hist2 = [
        Message(id="m2", session_id="s", seq=1, turn=2, actor="b", content="x", kind="roleplay", visible_to="all"),
        Message(id="m3", session_id="s", seq=2, turn=3, actor="b", content="x", kind="roleplay", visible_to="all"),
    ]
    got = engine.followup_candidates("a", {"a"}, [a, b, c], hist2, 0, 0, 3)
    assert "b" not in {x.id for x in got} and "c" in {x.id for x in got}


# ---------- 会话级开关 API ----------


async def test_session_level_toggle():
    import mrp.server.app as app_mod
    from fastapi.testclient import TestClient

    app_mod.world.summarizer = lambda text: "（测试摘要）"
    with TestClient(app_mod.app) as client:
        resp = client.post(
            "/api/v1/characters/import",
            files={"file": ("主动甲.json", '{"name": "主动甲", "first_mes": "（点头）"}'.encode("utf-8"), "application/json")},
        )
        cid = resp.json()["id"]
        r = client.post("/api/v1/sessions", json={"title": "r38", "character_ids": [cid]})
        sid = r.json()["meta"]["id"]
        # 会话级开启插话
        assert client.post(
            f"/api/v1/sessions/{sid}/characters/{cid}", json={"action": "interject"}
        ).status_code == 200
        state = client.get(f"/api/v1/sessions/{sid}").json()
        ch = next(c for c in state["characters"] if c["id"] == cid)
        assert ch["interject_enabled"] is True
        # 关闭
        client.post(f"/api/v1/sessions/{sid}/characters/{cid}", json={"action": "uninterject"})
        state = client.get(f"/api/v1/sessions/{sid}").json()
        ch = next(c for c in state["characters"] if c["id"] == cid)
        assert ch["interject_enabled"] is False
        # 库级 PATCH
        assert client.patch(
            f"/api/v1/characters/{cid}", json={"followup_enabled": True}
        ).status_code == 200
        # 会话级上限
        r = client.patch(f"/api/v1/sessions/{sid}", json={"proactive_turn_limit": 2})
        assert r.json()["proactive_turn_limit"] == 2


async def test_list_sessions_carries_all_session_config():
    """会话级配置读回防回归：PATCH 可设字段必须在列表接口下发。

    背景（2026-09-24 用户报"叙事子按钮完全失灵"）：director_mode/narrative_*/
    short_input_padding/proactive_turn_limit 曾只在 PATCH 响应里返回，列表接口
    缺失 → 前端弹层/开关点击后 UI 永远显示旧值（后端实际已生效）。
    """
    import mrp.server.app as app_mod
    from fastapi.testclient import TestClient

    app_mod.world.summarizer = lambda text: "（测试摘要）"
    with TestClient(app_mod.app) as client:
        resp = client.post(
            "/api/v1/characters/import",
            files={"file": ("读回甲.json", '{"name": "读回甲", "first_mes": "（点头）"}'.encode("utf-8"), "application/json")},
        )
        cid = resp.json()["id"]
        sid = client.post(
            "/api/v1/sessions", json={"title": "读回", "character_ids": [cid]}
        ).json()["meta"]["id"]

        patch = {
            "narrative_pov": "second",
            "narrative_density": "dialogue",
            "short_input_padding": False,
            "director_mode": "rules",
            "proactive_turn_limit": 2,
            "options_direct_send": True,  # M12-R41：点击候选直发偏好（读回纪律同受本测试保护）
        }
        assert client.patch(f"/api/v1/sessions/{sid}", json=patch).status_code == 200

        rows = client.get("/api/v1/sessions").json()
        row = next(s for s in rows if s["id"] == sid)
        for k, v in patch.items():
            assert row.get(k) == v, f"列表接口未读回 {k}: {row.get(k)!r} != {v!r}"
