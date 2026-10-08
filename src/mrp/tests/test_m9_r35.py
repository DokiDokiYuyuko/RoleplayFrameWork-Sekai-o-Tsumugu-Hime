"""M9 测试：R35 场景演化 + M9-0 system 注入修复。

环境隔离同 test_integration.py：import mrp.server.app 之前设
MRP_DATA_ROOT/MRP_FAKE_ENGINE。
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="mrp-m9-")
os.environ["MRP_DATA_ROOT"] = _TMP
os.environ["MRP_FAKE_ENGINE"] = "1"

from mrp.shared.prompt import compose_prompt  # noqa: E402
from mrp.tests.test_integration import RecordingSink, make_runner  # noqa: E402


# ---------- M9-0：system 锚注入到达 prompt（存量 bug 修复的锁定测试） ----------


async def test_system_injection_reaches_prompt():
    """R22 潜台词注入（source=system, anchor=system）必须出现在 composed prompt。

    存量 bug：compose_prompt 只渲染 lorebook/memory/at_depth/near 四类，
    system 锚注入被静默丢弃——R22 反心灵感应从未真正生效。本测试锁定修复。
    """
    runner, _ = make_runner(replies=["嗯。"])
    await runner.player_say("（内心：她好烦）", channel="inner")
    created = await runner.player_say("测试甲，你怎么看？")
    assert created[1].actor == "char-a"

    engine = runner.engines._engines["char-a"]
    ctx = engine.calls[-1]
    text = compose_prompt(ctx).text
    assert "玩家情绪暗示" in text, "inner 潜台词注入未到达 prompt（M9-0 回归）"
    assert "她好烦" in text


async def test_hygiene_feedback_reaches_retry_prompt():
    """R34 违规重试反馈必须出现在重试回合的 composed prompt。"""
    from mrp.orchestrator.hygiene import FakeJudge

    runner, _ = make_runner(replies=["违规回复", "修正后回复"])
    runner.hygiene_judge = FakeJudge(mode="strict")
    created = await runner.player_say("测试甲，你怎么看？")
    assert created[1].content == "修正后回复"  # strict：重试成功

    engine = runner.engines._engines["char-a"]
    retry_ctx = engine.calls[-1]
    text = compose_prompt(retry_ctx).text
    assert "驳回" in text, "hygiene_feedback 未到达重试 prompt（M9-0 回归）"


# ---------- R35a：schema v1→v2 迁移 ----------

import json  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

from mrp.shared.models import Scene, SessionState  # noqa: E402


def _v1_state_dict() -> dict:
    """手写最小 v1 SessionState dict（无 schema_version 新键/无 scene_id/scenes）。"""
    return {
        "schema_version": 1,
        "meta": {
            "id": "sess-mig-test",
            "title": "迁移测试",
            "player_persona": "玩家",
            "character_ids": ["char-a", "char-b"],
        },
        "messages": [
            {"id": "msg-1", "session_id": "sess-mig-test", "seq": 0, "turn": 1,
             "actor": "player", "content": "你好", "kind": "roleplay",
             "visible_to": "all", "status": "final", "fingerprint": "x1",
             "created_at": "2026-01-01T00:00:00Z"},
            {"id": "msg-2", "session_id": "sess-mig-test", "seq": 1, "turn": 1,
             "actor": "char-a", "content": "你来了", "kind": "roleplay",
             "visible_to": "all", "status": "final", "fingerprint": "x2",
             "created_at": "2026-01-01T00:00:01Z"},
        ],
        "characters": [
            {"id": "char-a", "card": {"name": "甲", "first_mes": "嗨"},
             "aliases": [], "llm": {"provider": "p", "model": "m", "base_url": "u",
                                    "api_key_env": "K", "sampling": {}},
             "talkativeness": 0.5, "muted": False, "present": True},
            {"id": "char-b", "card": {"name": "乙", "first_mes": "哦"},
             "aliases": [], "llm": {"provider": "p", "model": "m", "base_url": "u",
                                    "api_key_env": "K", "sampling": {}},
             "talkativeness": 0.5, "muted": False, "present": True},
        ],
        "director_log": [
            {"turn": 1, "trigger": "talkativeness", "candidates": [], "chosen": ["char-a"],
             "rng_seed": 1, "created_at": "2026-01-01T00:00:00Z"},
        ],
    }


def test_migration_v1_dict():
    from mrp.orchestrator.migration import migrate_state_dict

    raw = _v1_state_dict()
    original = json.dumps(raw, ensure_ascii=False)
    migrated, changed = migrate_state_dict(raw)
    assert changed is True
    state = SessionState.model_validate(migrated)
    assert state.schema_version == 2
    assert len(state.scenes) == 1
    scene = state.scenes[0]
    assert scene.title == "开场"
    assert set(scene.member_ids) == {"char-a", "char-b"}
    assert scene.turn_end is None  # 进行中
    assert state.active_scene_id == scene.id
    # 全部消息归属初始场景；内容 golden 全等
    assert all(m.scene_id == scene.id for m in state.messages)
    assert [m.content for m in state.messages] == ["你好", "你来了"]
    # 原 dict 未被篡改（深拷贝）
    assert json.dumps(raw, ensure_ascii=False) == original


def test_migration_idempotent():
    from mrp.orchestrator.migration import migrate_state_dict

    migrated, changed1 = migrate_state_dict(_v1_state_dict())
    assert changed1 is True
    again, changed2 = migrate_state_dict(migrated)
    assert changed2 is False
    assert again is migrated  # 原样返回


def test_migration_save_dict():
    from mrp.orchestrator.migration import migrate_save_dict

    save = {"schema_version": 1, "state": _v1_state_dict(),
            "memory_refs": [], "saved_at": "2026-01-01T00:00:00Z"}
    migrated, changed = migrate_save_dict(save)
    assert changed is True
    assert migrated["schema_version"] == 2
    assert migrated["state"]["schema_version"] == 2
    # 已 v2 → 原样
    _, changed2 = migrate_save_dict(migrated)
    assert changed2 is False


async def test_migration_via_load_session_with_backup():
    """走 World.load_session 完整路径：迁移落盘 + .v1.bak 备份 + 二次 load 幂等。"""
    import mrp.server.app as app_mod

    raw = _v1_state_dict()
    path = app_mod.DIR_SESSIONS / "sess-mig-test.json"
    text = json.dumps(raw, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    app_mod.world.runners.pop("sess-mig-test", None)

    r = await app_mod.world.load_session("sess-mig-test")
    assert r is not None
    assert r.state.schema_version == 3
    assert r.state.active_scene_id is not None
    assert all(m.scene_id == r.state.active_scene_id for m in r.state.messages)

    bak = app_mod.DIR_SESSIONS / "sess-mig-test.json.v1.bak"
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == text  # 备份=原文

    # 迁移已落盘
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 3

    # 幂等：二次 load 不再迁移（bak 内容不变，state 恒等）
    app_mod.world.runners.pop("sess-mig-test", None)
    r2 = await app_mod.world.load_session("sess-mig-test")
    assert r2.state.schema_version == 3
    assert bak.read_text(encoding="utf-8") == text


async def test_migration_synthetic_v1_session():
    """A newly authored synthetic v1 snapshot preserves messages and creates a backup.

    Fixture text, identities and timestamps are deterministic test constants.
    It contains no historical conversation, model usage or engine session state.
    """
    import mrp.server.app as app_mod

    src = Path(__file__).resolve().parent / "fixtures" / "synthetic-session-v1.json"
    assert src.exists(), f"迁移夹具缺失（应随仓库跟踪）：{src}"
    raw = json.loads(src.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1, "夹具必须是 v1 快照（防呆：勿用 v2 会话覆盖）"
    path = app_mod.DIR_SESSIONS / "sess-synthetic-v1.json"
    shutil.copy(src, path)
    original_messages = raw["messages"]
    app_mod.world.runners.pop("sess-synthetic-v1", None)

    r = await app_mod.world.load_session("sess-synthetic-v1")
    assert r is not None
    assert r.state.schema_version == 3
    assert r.state.scenes and r.state.active_scene_id
    # 消息 golden 全等（除 additive scene_id）
    assert len(r.state.messages) == len(original_messages)
    for m, om in zip(r.state.messages, original_messages):
        assert m.id == om["id"] and m.content == om["content"] and m.seq == om["seq"]
        assert m.scene_id == r.state.active_scene_id
    assert (app_mod.DIR_SESSIONS / "sess-synthetic-v1.json.v1.bak").exists()


async def test_new_session_bootstraps_scene(mrp_client):
    """v3 新会话：create_session 带初始场景；消息落账自动归属。"""
    resp = mrp_client.post(
        "/api/v1/characters/import",
        files={"file": ("场景甲.json", '{"name": "场景甲", "first_mes": "（点头）来了。"}'.encode("utf-8"), "application/json")},
    )
    cid = resp.json()["id"]
    r = mrp_client.post("/api/v1/sessions", json={"title": "v3测试", "character_ids": [cid]})
    assert r.status_code == 200
    state = r.json()
    assert state["schema_version"] == 3
    assert len(state["scenes"]) == 1
    assert state["scenes"][0]["title"] == "开场"
    assert state["active_scene_id"] == state["scenes"][0]["id"]
    assert all(m["scene_id"] == state["active_scene_id"] for m in state["messages"])


# ---------- R35b：LLM 导演 + 场景切换 + 确认档 ----------

from mrp.orchestrator.director_llm import FakeDirectorJudge  # noqa: E402
from mrp.shared.models import Scene  # noqa: E402


def _runner_with_scene(judge=None, replies=None):
    runner, sink = make_runner(replies=replies or ["嗯。", "好。", "哦。"])
    scene = Scene(title="活动室", member_ids=["char-a", "char-b"])
    runner.state.scenes = [scene]
    runner.state.active_scene_id = scene.id
    runner.state.schema_version = 2
    if judge is not None:
        runner.director_judge = judge
    return runner, sink


async def test_scene_card_injection():
    """场景卡注入 order=80 进 TurnContext（经 M9-0 的 system 块进 prompt）。"""
    runner, _ = _runner_with_scene(replies=["嗯。"])
    await runner.player_say("测试甲，你怎么看？")  # 隐式提及 → v1 路径
    engine = runner.engines._engines["char-a"]
    ctx = engine.calls[-1]
    cards = [i for i in ctx.injections if i.entry_id == "scene_card"]
    assert cards and "活动室" in cards[0].content
    assert "测试甲" in cards[0].content  # 在场名单
    assert cards[0].order == 80
    assert "当前场景" in compose_prompt(ctx).text


async def test_director_llm_switch_flow():
    """narration 旁白 → FakeDirectorJudge=switch：过渡消息/present 翻位/场景边界/SSE。"""
    judge = FakeDirectorJudge(mode="switch")
    runner, sink = _runner_with_scene(judge=judge, replies=["嗯。", "好。"])
    created = await runner.player_say("走吧，去天台", channel="narration")

    # 过渡消息（无 first_speaker → 只有玩家旁白 + 过渡）
    transition = [m for m in created if m.actor == "director"]
    assert len(transition) == 1
    t = transition[0]
    assert t.kind == "scene" and t.scene_id == runner.state.active_scene_id
    assert t.actor == "director"
    # 新场景：天台；只有 char-a 跟上（FakeDirectorJudge members=第一个在场者）
    new_scene = runner.active_scene()
    assert new_scene.title == "天台" and new_scene.description == "夜晚，风很大"
    assert new_scene.member_ids == ["char-a"]
    # 旧场景关闭
    old = runner.state.scenes[0]
    assert old.turn_end is not None and old.title == "活动室"
    # present 翻位：char-b 留在楼下
    chars = {c.id: c for c in runner.state.characters}
    assert chars["char-a"].present is True
    assert chars["char-b"].present is False
    # 过渡消息对新在场名单可见 → char-b 看不见
    assert t.can_see("char-b") is False
    assert t.can_see("char-a") is True
    # 决策日志 + SSE
    assert runner.state.director_log[-1].trigger == "llm_switch_scene"
    assert runner.state.director_log[-1].rationale
    assert "scene.switched" in sink.names()
    assert "director.decision" in sink.names()


async def test_director_llm_pick_flow():
    """dialogue 无人提及 → LLM 选角（trigger=llm_route）。"""
    judge = FakeDirectorJudge(mode="pick")
    runner, sink = _runner_with_scene(judge=judge, replies=["选角回复"])
    created = await runner.player_say("今天天气不错")
    assert judge.calls and judge.calls[0].channel == "dialogue"
    assert runner.state.director_log[-1].trigger == "llm_route"
    assert runner.state.director_log[-1].action == "pick_speaker"
    replies = [m for m in created if m.actor != "player"]
    assert len(replies) >= 1 and replies[-1].content == "选角回复"


async def test_director_failopen_fallback():
    """LLM 导演异常 → fail-open 回退 v1 轮盘，回合完成。"""

    class ExplodingDirector:
        def decide(self, data):
            raise RuntimeError("导演爆炸(测试注入)")

    runner, sink = _runner_with_scene(judge=ExplodingDirector(), replies=["轮盘回复"])
    created = await runner.player_say("今天天气不错")
    d = runner.state.director_log[-1]
    assert d.trigger == "talkativeness"
    assert "回退" in d.rationale
    assert any(m.actor == "char-a" for m in created)


async def test_fast_paths_no_llm():
    """R35.4：mentions/force/隐式提及 0 次 LLM 导演调用。"""
    judge = FakeDirectorJudge(mode="pick")
    runner, _ = _runner_with_scene(judge=judge, replies=["一", "二", "三"])
    # 显式 mentions
    await runner.player_say("你好", mentions=["char-a"])
    # force
    await runner.player_say("再说一句", force_character="char-a")
    # 隐式提及（正文含角色名）
    await runner.player_say("测试甲，你怎么看？")
    assert not judge.calls  # 快路径零 LLM


async def test_confirm_mode_pending_and_reject():
    """R35.3 确认档：LLM 决策挂起（无角色回合）；否决 → v1 轮盘执行。"""
    judge = FakeDirectorJudge(mode="pick")
    runner, sink = _runner_with_scene(judge=judge, replies=["确认后回复"])
    runner.state.meta.director_mode = "confirm"

    created = await runner.player_say("今天天气不错")
    assert "director.pending" in sink.names()
    assert not any(m.actor == "char-a" for m in created)  # 挂起未执行
    assert runner.pending_director is not None

    # 否决 → 轮盘执行
    out = await runner.reject_pending_director()
    assert out is not None and any(m.actor != "player" for m in out)
    d = runner.state.director_log[-1]
    assert d.trigger == "talkativeness" and "否决" in d.rationale
    assert runner.pending_director is None


async def test_confirm_mode_confirm_executes():
    judge = FakeDirectorJudge(mode="switch")
    runner, sink = _runner_with_scene(judge=judge, replies=["嗯。"])
    runner.state.meta.director_mode = "confirm"
    await runner.player_say("走吧", channel="narration")
    assert runner.pending_director is not None
    out = await runner.confirm_pending_director()
    assert out is not None
    assert runner.active_scene().title == "天台"
    assert runner.pending_director is None


async def test_downstairs_cannot_see_rooftop():
    """US-S5 核心：切场景后，楼下角色的上下文不含楼上消息。"""
    judge = FakeDirectorJudge(mode="switch")
    runner, _ = _runner_with_scene(judge=judge, replies=["楼上回复", "楼上又回复"])
    await runner.player_say("走吧，去天台", channel="narration")
    assert {c.id: c for c in runner.state.characters}["char-b"].present is False

    # 楼上继续对话（玩家 + char-a）
    await runner.player_say("天台的风好大", mentions=["char-a"])
    # char-b 不在场 → 不被路由；其视角的可见消息不含楼上内容
    visible_b = [m.content for m in runner.state.visible_messages_for("char-b")]
    assert "天台的风好大" not in visible_b
    assert not any("楼上回复" in c for c in visible_b)


async def test_switch_scene_manual_api():
    """玩家手动切场景（trigger=player_scene），可指定带谁。"""
    runner, sink = _runner_with_scene(replies=["嗯。"])
    created = await runner.switch_scene_manual("天台", "夜晚", member_ids=["char-a"])
    assert created is not None
    assert runner.active_scene().title == "天台"
    d = runner.state.director_log[-1]
    assert d.trigger == "player_scene" and d.action == "switch_scene"
    assert "scene.switched" in sink.names()
    assert {c.id: c for c in runner.state.characters}["char-b"].present is False
