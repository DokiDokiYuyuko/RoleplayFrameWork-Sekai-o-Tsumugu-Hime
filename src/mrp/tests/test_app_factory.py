"""W1 重构验收测试：create_app 工厂 / 容器隔离 / 请求校验 / 前端静态托管。

用 opt-in fixture（见 conftest.py），不依赖模块级 MRP_DATA_ROOT 手法——
现有测试文件的 env hack 保持不动（迁移归波 2）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mrp.server.app import create_app
from mrp.server.container import AppContainer
from mrp.shared.models import Character, CharacterCard
from mrp.tests.conftest import make_test_container


def _import_char(client: TestClient, name: str, first_mes: str = "（点头）你好。") -> str:
    resp = client.post(
        "/api/v1/characters/import",
        files={
            "file": (
                f"{name}.json",
                f'{{"name": "{name}", "first_mes": "{first_mes}"}}'.encode("utf-8"),
                "application/json",
            )
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _new_session(client: TestClient, *character_ids: str) -> str:
    resp = client.post(
        "/api/v1/sessions",
        json={"title": "W1 测试会话", "character_ids": list(character_ids)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["meta"]["id"]


# ---------- ① 两实例互不共享状态 ----------


def test_two_apps_do_not_share_state(tmp_path: Path):
    ca = make_test_container(tmp_path / "a")
    cb = make_test_container(tmp_path / "b")
    app_a, app_b = create_app(ca), create_app(cb)
    assert app_a.state.container is ca and app_b.state.container is cb
    assert ca.characters is not cb.characters
    assert ca.runners is not cb.runners
    assert ca.memory_store.db_path != cb.memory_store.db_path

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        cid = _import_char(client_a, "隔离甲")
        sid = _new_session(client_a, cid)
        assert [s["id"] for s in client_a.get("/api/v1/sessions").json()] == [sid]

        # B 实例完全看不到 A 的角色/会话
        assert client_b.get("/api/v1/characters").json() == []
        assert client_b.get("/api/v1/sessions").json() == []
        assert client_b.get(f"/api/v1/sessions/{sid}").status_code == 404
        assert client_b.get(f"/api/v1/characters/{cid}").status_code == 404


# ---------- ② 请求校验（B17）----------


def test_validation_422(mrp_client: TestClient):
    cid = _import_char(mrp_client, "校验甲")
    sid = _new_session(mrp_client, cid)

    # content 超长（20000 上限）
    r = mrp_client.post(
        f"/api/v1/sessions/{sid}/messages", json={"content": "x" * 20001}
    )
    assert r.status_code == 422, r.text
    # 边界内可通过校验（fake 引擎，不会真的走网络）
    r = mrp_client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "短消息"})
    assert r.status_code == 200, r.text

    # mentions 超限（20 上限）
    r = mrp_client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "短消息", "mentions": [f"c{i}" for i in range(21)]},
    )
    assert r.status_code == 422, r.text

    # character_ids 超限（20 上限）
    r = mrp_client.post(
        "/api/v1/sessions",
        json={"title": "超限", "character_ids": [f"c{i}" for i in range(21)]},
    )
    assert r.status_code == 422, r.text

    # 记忆检索 k clamp（1..50）
    assert mrp_client.get(f"/api/v1/characters/{cid}/memories?k=0").status_code == 422
    assert mrp_client.get(f"/api/v1/characters/{cid}/memories?k=51").status_code == 422
    assert mrp_client.get(f"/api/v1/characters/{cid}/memories?k=50").status_code == 200


# ---------- ③ env 工厂 + 端到端（B16：200 同步）----------


def test_env_factory_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MRP_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("MRP_FAKE_ENGINE", "1")
    app = create_app()  # 不传容器 → 从 env 构建
    container = app.state.container
    assert container.fake_mode is True
    assert container.data_root == tmp_path / "data"

    with TestClient(app) as client:
        cid = _import_char(client, "端到端甲", first_mes="（抬头）你来了。")
        sid = _new_session(client, cid)

        # 建会话即落开场白
        state = client.get(f"/api/v1/sessions/{sid}").json()
        assert [m["kind"] for m in state["messages"]].count("roleplay") == 1

        # B16：发送消息改为 200（原声明 202 但同步等待整回合）
        r = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "晚上好"})
        assert r.status_code == 200, r.text
        messages = r.json()["messages"]
        assert any(m["actor"] == "player" for m in messages)
        assert any(m["actor"] != "player" for m in messages)

        # 读回：玩家消息 + 角色回复都在
        again = client.get(f"/api/v1/sessions/{sid}").json()
        assert len(again["messages"]) >= 3


# ---------- ④ 静态托管与路径穿越（D9）----------


def test_static_fallback_rejects_path_traversal(tmp_path: Path):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html>index-ok</html>", encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    try:
        (web / "link").symlink_to(outside, target_is_directory=True)  # 穿越向量
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows 当前用户没有创建符号链接的权限")
        raise

    container = make_test_container(tmp_path, web_dist=web)
    app = create_app(container)

    with TestClient(app) as client:
        assert client.get("/").text == "<html>index-ok</html>"
        assert client.get("/assets/app.js").status_code == 200
        # 真实文件命中 → 返回原文件
        r = client.get("/index.html")
        assert r.status_code == 200 and "index-ok" in r.text
        # 穿越尝试 → 不得泄漏外部文件（回落 index.html）
        r = client.get("/link/secret.txt")
        assert r.status_code == 200 and "TOP-SECRET" not in r.text
        # 未匹配的 api 路径 → 404（不被 SPA fallback 吞掉）
        assert client.get("/api/v1/definitely-not-exists").status_code == 404


# ---------- B3：runners LRU 上限 + 列摘要不建 runner ----------


async def test_runner_cache_lru_eviction(tmp_path: Path):
    container = make_test_container(tmp_path, runner_cache=2)
    await container.save_character(Character(id="char-x", card=CharacterCard(name="甲")))
    ids = []
    for i in range(3):
        runner = await container.create_session(f"会话{i}", ["char-x"], "", [])
        await container.persist_session(runner)  # 端点语义：建会话即落盘
        ids.append(runner.state.meta.id)
    assert len(container.runners) == 2
    assert ids[0] not in container.runners  # 最旧被淘汰
    assert list(container.runners) == ids[1:]
    # 被淘汰会话仍可从磁盘重新加载（数据不丢）
    assert await container.load_session(ids[0]) is not None


async def test_runner_cache_keeps_busy_runners(tmp_path: Path):
    """淘汰只挑空闲 runner（否则会重建出"陈旧影子 runner"，见 _register_runner）"""
    container = make_test_container(tmp_path, runner_cache=1)
    await container.save_character(Character(id="char-x", card=CharacterCard(name="甲")))
    busy = await container.create_session("忙会话", ["char-x"], "", [])
    busy.busy = lambda: True  # 模拟回合进行中
    await container.create_session("新会话", ["char-x"], "", [])
    assert busy.state.meta.id in container.runners
    assert len(container.runners) == 2  # 全部忙 → 暂时超限

    busy.busy = lambda: False
    await container.create_session("再来", ["char-x"], "", [])
    assert len(container.runners) == 1  # 忙者释放后回收至上限


async def test_session_summaries_do_not_build_runners(mrp_container: AppContainer):
    container = mrp_container
    await container.save_character(Character(id="char-y", card=CharacterCard(name="乙")))
    runner = await container.create_session("摘要会话", ["char-y"], "", [])
    await container.persist_session(runner)
    container.runners.clear()  # 模拟服务重启：磁盘有会话、内存无 runner

    out = await container.session_summaries()
    assert [s["id"] for s in out] == [runner.state.meta.id]
    assert out[0]["title"] == "摘要会话"
    assert container.runners == {}  # C1/B3：列摘要不再为每个会话建 runner


# ---------- 记忆编辑/删除端点（B14 相邻：同步 SQLite 已 to_thread）----------


def test_memory_patch_delete_endpoints(mrp_client: TestClient):
    from mrp.shared.models import MemoryRecord

    cid = _import_char(mrp_client, "记忆甲")
    store = mrp_client.app.state.container.memory_store
    rec = MemoryRecord(character_id=cid, session_id="s1", content="旧内容")
    store.add(rec)  # 同步写入口（测试种子数据）
    rid = rec.id

    r = mrp_client.patch(
        f"/api/v1/characters/{cid}/memories/{rid}", json={"content": "新内容", "importance": 5}
    )
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "新内容" and r.json()["importance"] == 5
    assert mrp_client.get(f"/api/v1/characters/{cid}/memories?q=新内容").json()["records"]

    assert mrp_client.patch(
        f"/api/v1/characters/{cid}/memories/mem-nonexistent", json={"content": "x"}
    ).status_code == 404
    assert mrp_client.delete(f"/api/v1/characters/{cid}/memories/{rid}").status_code == 200
    assert mrp_client.delete(f"/api/v1/characters/{cid}/memories/{rid}").status_code == 404


# ---------- 兼容 shim（波 2 删除）----------


def test_compat_shim_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import mrp.server.app as app_mod

    if app_mod._compat:  # 已被其他测试构建 → 不打扰（保持既有共享世界语义）
        pytest.skip("compat container already built by an earlier test")
    monkeypatch.setenv("MRP_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("MRP_FAKE_ENGINE", "1")
    assert app_mod.world.data_root == tmp_path / "data"
    assert app_mod.DIR_SESSIONS == tmp_path / "data" / "sessions"
    assert app_mod.DIR_CHARACTERS == tmp_path / "data" / "characters"
    with TestClient(app_mod.app) as client:
        assert client.get("/api/v1/health").status_code == 200
    app_mod._compat.clear()  # 还原：后续测试按当时 env 重新构建
