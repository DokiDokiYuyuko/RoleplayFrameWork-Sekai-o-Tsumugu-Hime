"""集成测试：FakeEngine 驱动全链路（计划 §11 汇合点验证）。

覆盖：SSE 事件序列、可见性断言（含说话者自身）、注入断言（世界书/记忆进 TurnContext）、
swipe 重roll、离席隔离、注入检查器、存档恢复、API 冒烟（TestClient + FakeEngine）。

注意：本文件在 import mrp.server.app 之前设置 MRP_DATA_ROOT/MRP_FAKE_ENVine，
World() 在模块导入时创建——测试隔离依赖这一点（勿在其他测试里先 import app）。
"""
from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="mrp-int-")
os.environ["MRP_DATA_ROOT"] = _TMP
os.environ["MRP_FAKE_ENGINE"] = "1"

import pytest  # noqa: E402

from mrp.engines.dsh.process import EngineManager  # noqa: E402
from mrp.engines.fake import FakeEngine  # noqa: E402
from mrp.orchestrator.session import SessionRunner  # noqa: E402
from mrp.shared.models import (  # noqa: E402
    Character,
    CharacterCard,
    Lorebook,
    LorebookEntry,
    MemoryRecord,
    SessionMeta,
    SessionState,
)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, session_id: str, event: str, payload: dict, *, lossy: bool = False) -> None:
        self.events.append((event, payload))

    def names(self) -> list[str]:
        return [e for e, _ in self.events]


def make_runner(
    replies: list[str] | None = None,
    with_lorebook: bool = False,
    with_memory: bool = False,
    char_b_present: bool = True,
) -> tuple[SessionRunner, RecordingSink]:
    card_a = CharacterCard(
        name="测试甲", description="合成角色甲简介", first_mes="合成开场甲。"
    )
    card_b = CharacterCard(name="测试乙", description="合成角色乙简介", first_mes="合成开场乙。")
    char_a = Character(id="char-a", card=card_a, aliases=["测试别名甲三"], talkativeness=0.8)
    char_b = Character(id="char-b", card=card_b, present=char_b_present, talkativeness=0.3)

    books = []
    if with_lorebook:
        books.append(
            Lorebook(
                id="book-1",
                name="测试书",
                entries=[
                    LorebookEntry(uid=0, keys=["魔法"], content="这个世界存在一种失传的魔法。"),
                    LorebookEntry(uid=1, constant=True, content="合成测试场所是固定上下文。"),
                ],
            )
        )

    memory = None
    if with_memory:

        class _Mem:
            def search(self, character_id, query, k=4, *, current_turn=None, session_id=None):
                return [
                    (
                        MemoryRecord(
                            id="mem-1", character_id=character_id, content="上周你失约过一次。"
                        ),
                        0.9,
                    )
                ]

        memory = _Mem()

    state = SessionState(
        meta=SessionMeta(id="sess-int", character_ids=["char-a", "char-b"]),
        characters=[char_a, char_b],
    )
    sink = RecordingSink()
    manager = EngineManager(engine_factory=lambda: FakeEngine(replies=replies))
    runner = SessionRunner(
        state, manager, lorebooks=books, memory_store=memory, sink=sink, rng_seed=42
    )
    return runner, sink


# ---------- Part 1：SessionRunner 级（深度断言） ----------


async def test_full_turn_sse_sequence_and_injections():
    runner, sink = make_runner(
        replies=["嗯，来了。"], with_lorebook=True, with_memory=True
    )
    created = await runner.player_say("测试甲，这个魔法阵是怎么回事？")

    # 消息：玩家 1 + 角色 1
    assert len(created) == 2
    assert created[0].actor == "player"
    assert created[1].actor == "char-a"  # 提及触发
    assert created[1].content == "嗯，来了。"

    # SSE 事件序列（R2.3 整段版 + 导演决策留痕 + R33 流式 delta）
    names = sink.names()
    assert names[0] == "message.final"  # 玩家消息
    assert "director.decision" in names
    i = names.index("director.decision")
    assert names[i + 1] == "message.pending"
    assert names[i + 2] == "message.delta"  # R33 伪流式：final 前有 delta 序列
    assert names[-1] == "cost.update"
    # 角色的 final 在最后一个 delta 之后（玩家 final 在最前，不计）
    last_delta = max(j for j, n in enumerate(names) if n == "message.delta")
    last_final = max(j for j, n in enumerate(names) if n == "message.final")
    assert last_final > last_delta
    # delta 拼接 == final content（offset 语义验证）
    deltas = [p for e, p in sink.events if e == "message.delta"]
    assert "".join(d["delta"] for d in deltas) == "嗯，来了。"
    assert [d["offset"] for d in deltas] == sorted(d["offset"] for d in deltas)
    assert "cost.update" in names

    # 导演决策：mention → char-a
    decision = runner.state.director_log[-1]
    assert decision.trigger == "mention" and decision.chosen == ["char-a"]

    # 注入断言（R4.2/R5.1 的自动化验证）：FakeEngine 收到的 TurnContext
    engine = runner.engines._engines["char-a"]
    ctx = engine.calls[0]
    entry_ids = [i.entry_id for i in ctx.injections]
    assert any("book-1:0" in e for e in entry_ids)  # 关键词触发
    assert any("book-1:1" in e for e in entry_ids)  # constant
    assert any(e.startswith("mem-1") or e == "mem-1" for e in entry_ids)  # 记忆检索

    # 注入检查器（R6.4）
    insp = runner.inspection("char-a", created[1].turn)
    assert insp is not None and "[世界设定]" in insp["prompt"]
    assert insp["tokens_by_section"].get("lorebook", 0) > 0


async def test_speaker_sees_own_history():
    """说话者自己的历史消息必须在其后续上下文里（可见性 bug 回归测试）。"""
    runner, _ = make_runner(replies=["第一句", "第二句"])
    await runner.player_say("测试甲，说两句")
    # char-a 已发言一次；再强制它说一次，检查上下文含自己第一句
    await runner.player_say("继续", force_character="char-a")
    engine = runner.engines._engines["char-a"]
    ctx = engine.calls[-1]
    own = [m for m in ctx.visible_messages if m.actor == "char-a"]
    assert any(m.content == "第一句" for m in own)


async def test_absent_character_isolation():
    """R3.2：离席期间的消息对离席者不可见，且回归不回溯。"""
    runner, _ = make_runner(replies=["好的。", "我知道了。"], char_b_present=True)
    await runner.set_presence("char-b", False)  # 测试乙离席
    await runner.player_say("测试甲，这是只说给你听的秘密。")  # 测试甲回应

    # 测试乙回归后发言——她的上下文不含离席期间的消息
    await runner.set_presence("char-b", True)
    await runner.player_say("测试乙，你回来了", force_character="char-b")
    engine = runner.engines._engines["char-b"]
    ctx = engine.calls[-1]
    contents = [m.content for m in ctx.visible_messages]
    assert not any("秘密" in c for c in contents)  # 离席期间的消息不可见


async def test_swipe_retracts_and_regenerates():
    """R32.1（v3 语义）：swipe 原位重roll——同 id、候选保留、上下文不含被换下文本。"""
    runner, _ = make_runner(replies=["旧回复", "新回复", "三号候选"])
    created = await runner.player_say("测试甲，你怎么看？")
    old = created[1]
    assert old.content == "旧回复"

    new = await runner.swipe(old.id)
    assert new is not None and new.content == "新回复"
    # 原位：同 id、旧候选物化为 variants[0]、无 retracted
    assert new.id == old.id
    assert new.status == "final"
    assert [v.content for v in new.variants] == ["旧回复", "新回复"]
    assert new.active_variant == 1
    assert not any(m.status == "retracted" for m in runner.state.messages)

    # 三连 swipe：候选累积到 3 个（须在后续回合之前——swipe 仅限末条消息）
    third = await runner.swipe(new.id)
    assert third is not None and third.content == "三号候选"
    assert [v.content for v in third.variants] == ["旧回复", "新回复", "三号候选"]
    assert third.active_variant == 2

    # 再来一回合：char-a 的上下文只含 active 候选，不含任何被换下候选
    await runner.player_say("继续说", force_character="char-a")
    engine = runner.engines._engines["char-a"]
    ctx = engine.calls[-1]
    assert not any(m.content == "旧回复" for m in ctx.visible_messages)
    assert not any(m.content == "新回复" for m in ctx.visible_messages)
    assert any(m.content == "三号候选" for m in ctx.visible_messages)


async def test_open_round_first_mes():
    runner, sink = make_runner()
    created = await runner.open_round()
    assert [m.actor for m in created] == ["char-a", "char-b"]  # 加入顺序
    assert created[0].content == "合成开场甲。"


# ---------- Part 2：API 级冒烟（TestClient + FakeEngine） ----------


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    import mrp.server.app as app_mod

    app_mod.world.summarizer = lambda text: "（测试摘要）"  # consolidate 不触网
    with TestClient(app_mod.app) as c:
        yield c


def _import_char(client, name: str, first_mes: str) -> str:
    resp = client.post(
        "/api/v1/characters/import",
        files={"file": (f"{name}.json", f'{{"name": "{name}", "first_mes": "{first_mes}"}}'.encode(), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_api_smoke_full_flow(client):
    # 1. 导入角色 ×2
    cid_a = _import_char(client, "测试甲", "（点头）你来了。")
    cid_b = _import_char(client, "测试乙", "（微笑）欢迎。")
    assert len(client.get("/api/v1/characters").json()) == 2

    # 2. 建会话 → 开场白
    resp = client.post(
        "/api/v1/sessions",
        json={"title": "测试会话", "character_ids": [cid_a, cid_b]},
    )
    assert resp.status_code == 200, resp.text
    session = resp.json()
    sid = session["meta"]["id"]
    firsts = [m for m in session["messages"] if m["kind"] == "roleplay"]
    assert len(firsts) == 2  # R2.1 first_mes

    # 3. 发言（提及测试甲）→ 200（波 2：B16 202→200，同步返回整回合）+ 玩家与角色消息
    resp = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "测试甲，今晚排练几点？"},
    )
    assert resp.status_code == 200, resp.text
    messages = resp.json()["messages"]
    assert len(messages) >= 2
    char_reply = next(m for m in messages if m["actor"] != "player")
    assert char_reply["generation_meta"]["model"]  # R1.3 模型配置随行

    # 4. swipe 重roll
    resp = client.post(f"/api/v1/sessions/{sid}/messages/{char_reply['id']}/swipe")
    assert resp.status_code == 200 and resp.json()["status"] == "final"

    # 5. 静音/在场
    assert client.post(f"/api/v1/sessions/{sid}/characters/{cid_b}", json={"action": "mute"}).status_code == 200
    assert client.post(f"/api/v1/sessions/{sid}/characters/{cid_b}", json={"action": "absent"}).status_code == 200

    # 6. 注入检查器 + 成本
    turn = char_reply["turn"]
    assert client.get(f"/api/v1/sessions/{sid}/inspections/{cid_a}/{turn}").status_code == 200
    cost = client.get(f"/api/v1/sessions/{sid}/cost").json()
    assert cost["total"]["output_tokens"] > 0

    # 7. 记忆固化（fake summarizer）+ 检索。整理默认关闭，冒烟前临时打开。
    blocked = client.post(f"/api/v1/sessions/{sid}/memory/consolidate")
    assert blocked.status_code == 400 and "记忆整理已关闭" in blocked.text
    turned = client.patch("/api/v1/settings", json={"memory_consolidation_enabled": True})
    assert turned.status_code == 200, turned.text
    resp = client.post(f"/api/v1/sessions/{sid}/memory/consolidate")
    assert resp.status_code == 200 and len(resp.json()["records"]) == 2
    assert client.patch("/api/v1/settings", json={"memory_consolidation_enabled": False}).status_code == 200
    resp = client.get(f"/api/v1/characters/{cid_a}/memories", params={"q": "排练"})
    assert resp.status_code == 200

    # 8. 存档/列表/恢复（R6.5）
    resp = client.post(f"/api/v1/sessions/{sid}/save")
    assert resp.status_code == 200
    save_id = resp.json()["id"]
    assert any(s["id"] == save_id for s in client.get(f"/api/v1/saves?session_id={sid}").json())
    restored = client.post(f"/api/v1/saves/{save_id}/restore").json()
    assert restored["meta"]["id"] == sid

    # 8.5 存档命名持久化（2026-09-25 修复：此前 name 不落盘，列表永远显示会话标题；空名回落标题）
    named = client.post(f"/api/v1/sessions/{sid}/save", params={"name": "我的存档"}).json()
    assert named["name"] == "我的存档"
    rows = client.get(f"/api/v1/saves?session_id={sid}").json()
    assert next(s for s in rows if s["id"] == named["id"])["name"] == "我的存档"
    unnamed = client.post(f"/api/v1/sessions/{sid}/save").json()
    assert unnamed["name"] and unnamed["name"] != "我的存档"  # 空名 → 回落会话标题
    rows = client.get(f"/api/v1/saves?session_id={sid}").json()
    assert next(s for s in rows if s["id"] == unnamed["id"])["name"] == unnamed["name"]

    # 9. 世界书导入 + 条目编辑（R6.3）
    book_json = {
        "entries": {
            "0": {"uid": 0, "key": ["魔法"], "content": "失传的魔法。", "comment": "", "disable": False},
            "1": {"uid": 1, "key": ["社规"], "content": "排练不许迟到。", "disable": False},
        }
    }
    resp = client.post(
        "/api/v1/lorebooks/import",
        files={"file": ("book.json", __import__("json").dumps(book_json).encode(), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    book_id = resp.json()["id"]
    resp = client.patch(
        f"/api/v1/lorebooks/{book_id}/entries/0", json={"constant": True}
    )
    assert resp.status_code == 200 and resp.json()["constant"] is True

    # 10. 健康
    health = client.get("/api/v1/health").json()
    assert health["ok"] and health["characters"] == 2


def test_openapi_schema_builds():
    """防回归：请求模型注解必须全部可解析。

    事故（2026-09-25）：app.py 漏 import CharacterCard / LorebookEntry，
    配合模块顶部 `from __future__ import annotations` 变成不可解析的字符串注解 →
    ai-edit / preview-turn / persona-preview / 世界书 PATCH 四个端点只能 500
    （openapi() 同样构建失败）。本测试让这类"注解漂移"在测试阶段即暴露。
    """
    import mrp.server.app as app_mod

    schema = app_mod.app.openapi()
    for p in (
        "/api/v1/characters/ai-edit",
        "/api/v1/characters/preview-turn",
        "/api/v1/characters/persona-preview",
        "/api/v1/lorebooks/{book_id}",
    ):
        assert p in schema["paths"], p


def test_character_delete_removes_card_and_unscoped_memories():
    """Library deletion clears library memory, retaining independent story snapshots."""
    import mrp.server.app as app_mod
    from fastapi.testclient import TestClient
    from mrp.shared.models import MemoryRecord

    app_mod.world.summarizer = lambda text: "（测试摘要）"
    with TestClient(app_mod.app) as client:
        resp = client.post(
            "/api/v1/characters/import",
            files={
                "file": (
                    "待删.json",
                    '{"name": "待删角色", "description": "将被删除"}'.encode("utf-8"),
                    "application/json",
                )
            },
        )
        assert resp.status_code == 200, resp.text
        cid = resp.json()["id"]

        app_mod.world.memory_store.add(
            MemoryRecord(character_id=cid, content="待清理记忆")
        )
        assert len(client.get(f"/api/v1/characters/{cid}/memories").json()["records"]) == 1
        assert (app_mod.DIR_CHARACTERS / f"{cid}.json").exists()

        resp = client.delete(f"/api/v1/characters/{cid}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["purged_memories"] == 1

        assert cid not in app_mod.world.characters
        assert not (app_mod.DIR_CHARACTERS / f"{cid}.json").exists()
        assert client.get(f"/api/v1/characters/{cid}").status_code == 404
        assert client.delete(f"/api/v1/characters/{cid}").status_code == 404
        assert client.get(f"/api/v1/characters/{cid}/memories").json()["records"] == []


def test_bundle_export_import_roundtrip():
    """F10.3 迁移包：导出→导入往返（别名/世界书名无损；id 冲突换新不覆盖；坏包 400）。"""
    import json as _json

    import mrp.server.app as app_mod
    from fastapi.testclient import TestClient

    app_mod.world.summarizer = lambda text: "（测试摘要）"
    with TestClient(app_mod.app) as client:
        cid = client.post(
            "/api/v1/characters/import",
            files={"file": ("打包甲.json", '{"name": "打包甲", "first_mes": "嗨"}'.encode("utf-8"), "application/json")},
        ).json()["id"]
        assert client.patch(f"/api/v1/characters/{cid}", json={"aliases": ["小包"]}).status_code == 200
        book = {
            "name": "打包书",
            "description": "迁移用",
            "entries": {"0": {"uid": 0, "key": ["包"], "content": "内容。"}},
        }
        assert client.post(
            "/api/v1/lorebooks/import",
            files={"file": ("b.json", _json.dumps(book).encode("utf-8"), "application/json")},
        ).status_code == 200

        before_export = len(app_mod.world.characters)  # 导出时实例里的全部角色
        resp = client.get("/api/v1/bundle/export")
        assert resp.status_code == 200 and resp.content[:2] == b"PK", "导出不是 zip"

        rep = client.post(
            "/api/v1/bundle/import",
            files={"file": ("bundle.zip", resp.content, "application/zip")},
        ).json()
        assert rep["ok"] is True

        names = [x["name"] for x in rep["characters"]]
        # 包里带了当时实例的全部角色（含其他用例留下的）→ 至少包含本次两个角色
        assert "打包甲" in names
        new_cid = next(x["id"] for x in rep["characters"] if x["name"] == "打包甲")
        assert new_cid != cid, "id 冲突应换新 id（不覆盖）"
        # 别名无损（侧车）
        assert client.get(f"/api/v1/characters/{new_cid}").json()["aliases"] == ["小包"]
        # 世界书名无损（侧车）
        assert any(x["name"] == "打包书" for x in rep["lorebooks"])
        # 角色数翻倍（导出时 N 个 → 导入后 2N 个；冲突换新 id 不覆盖）
        assert len(app_mod.world.characters) == before_export * 2

        bad = client.post(
            "/api/v1/bundle/import", files={"file": ("bad.zip", b"not a zip", "application/zip")}
        )
        assert bad.status_code == 400
