"""opt-in 测试夹具（W1 波 1 新增）。

**不使用 autouse**：现有测试文件依赖"模块级设 MRP_DATA_ROOT → import mrp.server.app"
的隔离方式，这里绝不能改变它们的行为。需要干净容器/应用的新测试显式声明以下 fixture：

- `mrp_container`：tmp_path 上的 `AppContainer`（fake 引擎、不托管静态资源、独立目录）
- `mrp_app`：`create_app(mrp_container)` 的 FastAPI 实例（容器可从 `app.state.container` 取）
- `mrp_client`：已进入 lifespan 上下文的 TestClient（退出时自动 aclose 容器）

迁移说明（波 2）：现有测试逐个切换为这些 fixture 后，删除 app.py 末尾的兼容 shim。
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mrp.server.app import create_app
from mrp.server.container import AppContainer, AppContainerConfig


def make_test_container(
    tmp_path: Path, *, runner_cache: int = 4, web_dist: Path | None = None
) -> AppContainer:
    """测试用容器工厂：fake 引擎 + 独立 data_root，不读 env。"""
    return AppContainer(
        data_root=tmp_path / "data",
        config=AppContainerConfig(fake_mode=True, runner_cache=runner_cache),
        web_dist=web_dist,
    )


def _teardown_sync(container: AppContainer) -> None:
    """同步收尾：取消预热任务 + 关 SQLite（幂等）。

    容器由测试自建（`create_app(container)` 视为借用，lifespan 不释放容器级资源），
    故这里显式同步释放；runner/引擎由各测试的 lifespan 或进程退出处理。
    """
    for t in list(container._warm_tasks):
        t.cancel()
    container._warm_tasks.clear()
    container.memory_store.close()


@pytest.fixture()
def mrp_container(tmp_path: Path) -> Iterator[AppContainer]:
    container = make_test_container(tmp_path)
    yield container
    _teardown_sync(container)


@pytest.fixture()
def mrp_app(tmp_path: Path) -> Iterator[FastAPI]:
    container = make_test_container(tmp_path)
    app = create_app(container)
    with TestClient(app) as client:
        # TestClient 上下文内 lifespan 已启动；容器从 app.state.container 取
        assert client.get("/api/v1/health").status_code == 200
        yield app
    _teardown_sync(container)


@pytest.fixture()
def mrp_client(tmp_path: Path) -> Iterator[TestClient]:
    container = make_test_container(tmp_path)
    with TestClient(create_app(container)) as client:
        yield client
    _teardown_sync(container)
