"""FastAPI 应用装配（W1 波 1 重构）：`create_app(container)` + 兼容 shim。

- 应用级状态（注册表/运行器/引擎/记忆/事件总线）全部在 `AppContainer` 实例上，
  `create_app()` 可多次调用（多实例/嵌入），实例之间互不共享。
- 路由实现见 `mrp/server/routers/`；共享依赖见 `mrp/server/deps.py`。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mrp.shared.story_errors import ConversationConflict

from mrp.server.container import PROJECT_ROOT, AppContainer, build_container_from_env
from mrp.server.lan_access import LanAccess, LanAccessMiddleware, default_lan_session_store_path
from mrp.server.routers import ALL_ROUTERS
from mrp.server.routers.system import make_lifespan, mount_web
from mrp.server.branch_leases import BranchLeaseMiddleware
from mrp.server.story_transport import StoryCommandProjectionMiddleware
from mrp.storage.story_sqlite import BranchRevisionConflict, OperationConflict


def create_app(container: AppContainer | None = None) -> FastAPI:
    """组装应用：容器注入（None → 从 env 构建）+ 路由 + 前端静态托管 + lifespan。

    容器所有权：`create_app()` 自建的容器由应用在关停时 `aclose()`；
    外部传入的容器视为**借用**（嵌入/多实例/兼容 shim——同进程内可能被多个 app
    或多个 TestClient 复用，中途不能释放 SQLite 与 runner），其释放由调用方
    `await container.aclose()` 负责。
    """
    # Consume the one-time LAN code before constructing subsystems that could
    # indirectly emit environment diagnostics. LanAccess retains only a salted
    # verifier in memory.
    lan_access = LanAccess(session_store_path=default_lan_session_store_path())
    owns_container = container is None
    c = container if container is not None else build_container_from_env()
    app = FastAPI(
        title="世界を紡ぐ姫 · 织界之姬",
        version="0.1.0",
        lifespan=make_lifespan(c, close_container=owns_container),
    )
    app.state.container = c
    app.add_middleware(BranchLeaseMiddleware, registry=c.branch_runtimes)
    app.add_middleware(StoryCommandProjectionMiddleware)
    app.state.lan_access = lan_access
    @app.exception_handler(ConversationConflict)
    async def conversation_conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    @app.exception_handler(BranchRevisionConflict)
    @app.exception_handler(OperationConflict)
    async def branch_storage_conflict(request, exc):
        code = "operation_conflict" if isinstance(exc, OperationConflict) else "revision_conflict"
        return JSONResponse(status_code=409, content={"detail": {"code": code, "message": str(exc)}})
    # Pure ASGI middleware preserves SSE streaming while covering every route,
    # static asset, and media response with one LAN pairing boundary.
    app.add_middleware(LanAccessMiddleware, access=app.state.lan_access)
    for r in ALL_ROUTERS:
        app.include_router(r)
    mount_web(app, c.web_dist)
    return app


# ---------------------------------------------------------------------------
# 兼容 shim — 波 3 移除（波 2 已完成容器/storage/生命周期接线）
#
# 保留旧模块级名字供现有测试与既有启动方式过渡使用：
#   `world` / `app` / `DIR_CHARACTERS` / `DIR_LOREBOOKS` / `DIR_SESSIONS` /
#   `DIR_SAVES` / `DIR_MEMORIES` / `PROJECT_ROOT` / `WEB_DIST`
# 现有测试形如 `import mrp.server.app as app_mod; app_mod.world.summarizer = ...`，
# `uvicorn mrp.server.app:app` 也仍可直接使用。
#
# 惰性构建（PEP 562）：`import mrp.server.app` 本身不再创建容器，首次取上述
# 名字时才按 env 构建一次（可观测行为与旧址模块级 `world = World()` 一致），
# 这样 `mrp.server.main` 走 `create_app()` 时不会白建第二份容器。
#
# 注意：shim 的 `app` 是"借用"容器（`create_app(world)`），lifespan 关停不会
# 释放 SQLite/runner——同一进程内多个 TestClient 会复用这份世界。
#
# 波 3 迁移步骤：测试改为 conftest 的 `create_app(make_test_container(...))`
# 后，整段（`_COMPAT_NAMES` 起到文件末尾的 TYPE_CHECKING 块）删除。
# ---------------------------------------------------------------------------

_COMPAT_NAMES = frozenset(
    {
        "world",
        "app",
        "PROJECT_ROOT",
        "WEB_DIST",
        "DIR_CHARACTERS",
        "DIR_LOREBOOKS",
        "DIR_SESSIONS",
        "DIR_SAVES",
        "DIR_MEMORIES",
    }
)
_compat: dict[str, object] = {}


def _build_compat() -> dict[str, object]:
    world = build_container_from_env()
    _compat.update(
        world=world,
        app=create_app(world),
        PROJECT_ROOT=PROJECT_ROOT,
        WEB_DIST=world.web_dist,
        DIR_CHARACTERS=world.characters_dir,
        DIR_LOREBOOKS=world.lorebooks_dir,
        DIR_SESSIONS=world.sessions_dir,
        DIR_SAVES=world.saves_dir,
        DIR_MEMORIES=world.memories_dir,
    )
    return _compat


def __getattr__(name: str):
    if name in _COMPAT_NAMES:
        return (_compat or _build_compat())[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if TYPE_CHECKING:  # 仅为类型检查/IDE 可见；运行时由 __getattr__ 提供
    from mrp.server.container import AppContainer as _AppContainer

    world: _AppContainer
    app: FastAPI
    PROJECT_ROOT: Path
    WEB_DIST: Path | None
    DIR_CHARACTERS: Path
    DIR_LOREBOOKS: Path
    DIR_SESSIONS: Path
    DIR_SAVES: Path
    DIR_MEMORIES: Path
