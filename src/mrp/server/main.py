"""程序化入口（D3）：`python -m mrp.server.main` 或 console script `mrp-server`。

- `app = create_app()`：从 env 构建容器（与旧 `mrp.server.app:app` 行为一致），
  可直接作为 ASGI target 使用（`uvicorn mrp.server.main:app` / 打包嵌入）。
- `main()`：自持 `uvicorn.Config` + `Server.run`，host/port 从 env 读
  （`MRP_HOST` 默认 127.0.0.1、`MRP_PORT` 默认 8000）。
"""
from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from mrp.server.app import create_app
from mrp.server.lan_runtime import serve_lan_with_local_control

app = create_app()


def main() -> None:
    host = os.environ.get("MRP_HOST", "127.0.0.1")
    port = int(os.environ.get("MRP_PORT", "8000"))
    lan_mode = os.environ.get("MRP_LAN_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
    if lan_mode:
        selected_ip = str(app.state.lan_access.bind_ip)
        if host != selected_ip:
            raise RuntimeError("MRP_HOST must equal the selected LAN IPv4 address; refusing broader binding.")
    if not lan_mode:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            import logging

            logging.getLogger("mrp.server").warning(
                "Non-loopback MRP_HOST is ignored while LAN mode is off; binding to 127.0.0.1."
            )
        host = "127.0.0.1"
        uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level="info",
                proxy_headers=False,
                forwarded_allow_ips="",
            )
        ).run()
        return
    import asyncio

    project_root = Path(__file__).resolve().parents[3]
    asyncio.run(
        serve_lan_with_local_control(
            app,
            host,
            port,
            tls_material=app.state.lan_access.tls_material,
            project_root=project_root,
        )
    )


if __name__ == "__main__":
    main()
