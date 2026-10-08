"""系统路由与 app 装配辅助：健康检查 / 空闲引擎回收 / lifespan / 前端静态托管。"""
from __future__ import annotations

import asyncio
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mrp.server.container import AppContainer
from mrp.server.deps import get_container
from mrp.storage.data_lease import DataRootLease

router = APIRouter()


@router.get("/api/v1/health")
async def health(container: AppContainer = Depends(get_container)):
    return {
        "ok": True,
        "characters": len(container.characters),
        "lorebooks": len(container.lorebooks),
        "active_engines": container.engine_manager.active_ids(),
        "engines_ready": await container.engine_manager.ready_ids(),  # R28.2：已启动完成（预热观测）
        "warming": container.warming(),  # R28.2：预热进行中
        "vector_memory": getattr(container.memory_store, "vector_enabled", False),
        # A9：新增观测字段（原有字段一律保留）
        "db": str(container.memory_store.db_path),
        "data_root": str(container.data_root),
    }


async def idle_sweeper(container: AppContainer) -> None:
    """R28.2 配套：定期回收空闲引擎进程（此前 sweep_idle 无人调用 → 空闲进程不回收）。"""
    import logging

    log = logging.getLogger("mrp.warmup")
    while True:
        try:
            await asyncio.sleep(60)
            n = await container.engine_manager.sweep_idle()
            if n:
                log.info("回收空闲引擎 %d 个", n)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 —— 回收失败不影响服务
            continue


async def story_notification_pump(container: AppContainer) -> None:
    """Drain committed notifications after restart and retry transient delivery failures."""
    import logging
    while True:
        try:
            await container.dispatch_story_outbox()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger("mrp.outbox").warning("Story notification retry deferred", exc_info=True)
        await asyncio.sleep(5)


def make_lifespan(container: AppContainer, *, close_container: bool = True):
    """生成绑定到给定容器的 lifespan（多实例各自独立启停）。

    `close_container=False` 用于**借用**容器的场景（嵌入方自己管生命周期；
    兼容 shim 的 `world` 还会被同进程内的多个 TestClient 复用，不能在中途
    释放 SQLite/runner）。此时只按旧址行为释放本应用启动的引擎。
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with DataRootLease(container.data_root, purpose="server"):
            # R28.2：启动即后台预热最近会话（不阻塞启动；失败静默）
            container.spawn_tts_if_enabled()
            warm_task = asyncio.create_task(container.warm_recent_session())
            backup_task = asyncio.create_task(container.story_backups.seed_existing())
            sweep_task = asyncio.create_task(idle_sweeper(container))
            notification_task = asyncio.create_task(story_notification_pump(container))
            tasks = (warm_task, backup_task, sweep_task, notification_task)
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if close_container:
                    await container.aclose()
                else:
                    await container.engine_manager.shutdown_all()

    return lifespan


def mount_web(app: FastAPI, web_dist: Path | None) -> None:
    """前端静态服务（单端口远程访问：http://<host>:8000 即前端）。

    D2：目录由容器注入；None / 目录不存在 → 不托管（嵌入/打包场景）。
    D9：SPA fallback 的路径穿越校验用 `is_relative_to`（带分隔符语义，
    原 `str.startswith` 会把 `/dist-evil` 误判为 `/dist` 前缀内）。
    """
    if web_dist is None or not (web_dist / "index.html").exists():
        return
    # Some Windows Python installations lack a JavaScript MIME mapping. Without
    # this, StaticFiles serves Vite's ES modules as text/plain and browsers refuse
    # to execute them, leaving the SPA root blank.
    for extension in (".js", ".mjs"):
        mime, _ = mimetypes.guess_type(f"asset{extension}")
        if mime not in {"application/javascript", "text/javascript"}:
            mimetypes.add_type("text/javascript", extension)
    base = web_dist.resolve()
    app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="web-assets")

    @app.get("/", include_in_schema=False)
    async def web_index():
        # no-store：HTML 引用带 hash 的 assets；旧 HTML + 新 assets 组合会导致加载旧逻辑
        return FileResponse(web_dist / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/{full_path:path}", include_in_schema=False)
    async def web_spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404, "not found")
        candidate = (web_dist / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(base):
            return FileResponse(candidate)
        return FileResponse(web_dist / "index.html", headers={"Cache-Control": "no-store"})
