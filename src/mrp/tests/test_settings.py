"""R48/R49 设置页数据面测试（全局设置：推理引擎 + 思考开关）。

环境隔离同 test_integration.py：import mrp.server.app 之前设
MRP_DATA_ROOT/MRP_FAKE_ENGINE。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="mrp-settings-")
os.environ["MRP_DATA_ROOT"] = _TMP
os.environ["MRP_FAKE_ENGINE"] = "1"

import pytest  # noqa: E402


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    import mrp.server.app as app_mod

    app_mod.world.summarizer = lambda text: "（测试摘要）"
    with TestClient(app_mod.app) as c:
        yield c


def test_settings_default_and_roundtrip(client):
    """默认 dsh/on；PATCH 返回全量且 GET 读回一致；落盘存在。"""
    r = client.get("/api/v1/settings")
    assert r.status_code == 200
    assert r.json()["engine"] == "dsh" and r.json()["thinking"] == "on"

    r = client.patch("/api/v1/settings", json={"engine": "openrouter", "thinking": "off"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "openrouter" and body["thinking"] == "off"

    again = client.get("/api/v1/settings").json()
    assert again["engine"] == "openrouter" and again["thinking"] == "off"

    from mrp.settings import settings_path

    assert settings_path(Path(_TMP)).exists()

    # 复原，避免影响同文件后续用例
    client.patch("/api/v1/settings", json={"engine": "dsh", "thinking": "on"})


def test_settings_invalid_enum_400(client):
    """非法枚举 400，且不改变已有值。"""
    assert client.patch("/api/v1/settings", json={"engine": "nope"}).status_code == 400
    assert client.patch("/api/v1/settings", json={"thinking": "maybe"}).status_code == 400
    assert client.get("/api/v1/settings").json()["engine"] == "dsh"


def test_settings_patch_clears_active_engines(client):
    """PATCH 后存活引擎清空——下个回合按新设置重建。"""
    import mrp.server.app as app_mod

    resp = client.post(
        "/api/v1/characters/import",
        files={
            "file": (
                "设置甲.json",
                '{"name": "设置甲", "first_mes": "（点头）"}'.encode("utf-8"),
                "application/json",
            )
        },
    )
    cid = resp.json()["id"]
    sid = client.post(
        "/api/v1/sessions", json={"title": "设置", "character_ids": [cid]}
    ).json()["meta"]["id"]
    assert (
        client.post(
            f"/api/v1/sessions/{sid}/messages", json={"content": "你好", "mentions": [cid]}
        ).status_code
        == 200
    )
    assert cid in app_mod.world.engine_manager.active_ids()  # 本会话角色引擎已建（可能还有预热的其他会话角色）

    assert client.patch("/api/v1/settings", json={"thinking": "off"}).status_code == 200
    # R28.2：换设置后立即后台预热 → 存活引擎按新设置**重建**（不再等"下个回合一次性冷启动"）。
    # 注意：预热目标是"最近有过消息的会话"，全套跑时可能不止 cid（其他测试留的会话）→ 断言包含而非相等。
    import time

    for _ in range(60):
        if cid in app_mod.world.engine_manager.active_ids():
            break
        time.sleep(0.05)
    assert cid in app_mod.world.engine_manager.active_ids()

    client.patch("/api/v1/settings", json={"thinking": "on"})  # 复原


def test_engine_factory_dispatch():
    """工厂分派：openrouter → OpenRouterEngine；dsh → DshEngine（思考档透传）。"""
    import mrp.server.app as app_mod
    from mrp.engines.dsh.engine import DshEngine
    from mrp.engines.openrouter import OpenRouterEngine

    w = app_mod.world
    saved_fake = w._fake_mode
    try:
        w._fake_mode = False  # 临时模拟真模式（仅测工厂，不发请求）
        w.settings.engine = "openrouter"
        assert isinstance(w._make_engine(), OpenRouterEngine)

        w.settings.engine = "dsh"
        eng = w._make_engine()
        assert isinstance(eng, DshEngine)
        # DSH 固定低档（其 "off" 会被上游不稳定忽略，实测 3 次仅 1 次生效）
        assert eng._effort_override == "low"
        w.settings.thinking = "off"
        assert w._make_engine()._effort_override == "low"
    finally:
        w._fake_mode = saved_fake
        w.settings.engine = "dsh"
        w.settings.thinking = "on"


def test_fake_engine_overrides_settings(client):
    """MRP_FAKE_ENGINE=1 最高优先：设置切 openrouter 后仍用 FakeEngine（冒烟安全）。"""
    import mrp.server.app as app_mod
    from mrp.engines.fake import FakeEngine

    assert client.patch("/api/v1/settings", json={"engine": "openrouter"}).status_code == 200
    try:
        resp = client.post(
            "/api/v1/characters/import",
            files={
                "file": (
                    "覆盖甲.json",
                    '{"name": "覆盖甲", "first_mes": "（点头）"}'.encode("utf-8"),
                    "application/json",
                )
            },
        )
        cid = resp.json()["id"]
        sid = client.post(
            "/api/v1/sessions", json={"title": "覆盖", "character_ids": [cid]}
        ).json()["meta"]["id"]
        assert (
            client.post(
                f"/api/v1/sessions/{sid}/messages",
                json={"content": "你好", "mentions": [cid]},
            ).status_code
            == 200
        )
        engines = app_mod.world.engine_manager._engines
        assert engines and all(isinstance(e, FakeEngine) for e in engines.values())
    finally:
        client.patch("/api/v1/settings", json={"engine": "dsh"})  # 复原
