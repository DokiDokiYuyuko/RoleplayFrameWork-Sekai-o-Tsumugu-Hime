"""Runtime controls for the separate local and selected-LAN listeners."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
import subprocess
import ssl

import uvicorn


class LanListenerServer(uvicorn.Server):
    """Secondary Uvicorn listener without a second app lifespan or signal owner."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


class TLS12UvicornConfig(uvicorn.Config):
    """Set a concrete TLS 1.2 floor while keeping Uvicorn's normal TLS setup."""

    def load(self) -> None:
        super().load()
        context = getattr(self, "ssl", None)
        if context is None:
            raise RuntimeError("The selected LAN listener did not initialize TLS; refusing plaintext startup.")
        context.minimum_version = ssl.TLSVersion.TLSv1_2


async def _wait_until_started(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    while not server.started:
        if task.done():
            await task
            raise RuntimeError("MRP listener stopped before startup completed.")
        await asyncio.sleep(0.025)


async def disable_lan_firewall(project_root: Path, port: int) -> bool:
    if os.name != "nt":
        return False
    helper = project_root / "tools" / "windows" / "disable_lan_firewall.ps1"
    try:
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-Port",
            str(port),
            cwd=str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        await process.communicate()
        return process.returncode == 0
    except (OSError, asyncio.SubprocessError):
        return False


async def serve_lan_with_local_control(
    application,
    selected_ip: str,
    port: int,
    *,
    tls_material,
    project_root: Path,
    firewall_disabler=disable_lan_firewall,
) -> None:
    """Serve loopback and the selected LAN address independently.

    The loopback server owns the app lifespan and remains available after a
    local close request. The selected-address server has its own connection
    registry so Uvicorn can stop its listener and active LAN streams only.
    """
    if tls_material is None:
        raise RuntimeError("LAN mode requires validated HTTPS certificate files; refusing plaintext startup.")
    shared_config = {
        "port": port,
        "log_level": "info",
        "proxy_headers": False,
        "forwarded_allow_ips": "",
    }
    local_server = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", lifespan="on", **shared_config)
    )
    lan_server = LanListenerServer(
        TLS12UvicornConfig(
            application,
            host=selected_ip,
            lifespan="off",
            ssl_certfile=str(tls_material.certificate_path),
            ssl_keyfile=str(tls_material.private_key_path),
            ssl_version=ssl.PROTOCOL_TLS_SERVER,
            timeout_graceful_shutdown=2,
            **shared_config,
        )
    )
    lan_shutdown_requested = asyncio.Event()
    local_task = asyncio.create_task(local_server.serve())
    lan_task: asyncio.Task[None] | None = None

    try:
        await _wait_until_started(local_server, local_task)
        lan_task = asyncio.create_task(lan_server.serve())
        await _wait_until_started(lan_server, lan_task)

        async def stop_lan_listener() -> dict[str, bool]:
            if lan_task is None:
                return {"listener_stopped": False, "firewall_rule_removed": False}
            lan_shutdown_requested.set()
            lan_server.should_exit = True
            await lan_task
            listener_stopped = lan_task.done() and not lan_task.cancelled()
            firewall_rule_removed = await firewall_disabler(project_root, port) if listener_stopped else False
            if firewall_rule_removed:
                marker = os.environ.get("MRP_LAN_FIREWALL_STATUS_FILE", "").strip()
                if marker:
                    try:
                        Path(marker).unlink(missing_ok=True)
                    except OSError:
                        pass
            return {
                "listener_stopped": listener_stopped,
                "firewall_rule_removed": firewall_rule_removed,
            }

        async def stop_local_listener() -> None:
            local_server.should_exit = True
            await local_task

        application.state.stop_lan_listener = stop_lan_listener
        application.state.stop_local_listener = stop_local_listener

        async def watch_lan_listener_exit() -> None:
            assert lan_task is not None
            try:
                await lan_task
            except Exception:
                logging.getLogger("mrp.server").exception("Selected LAN listener exited unexpectedly.")
            finally:
                if not lan_shutdown_requested.is_set():
                    access = getattr(application.state, "lan_access", None)
                    if access is not None:
                        access.close_lan()
                    logging.getLogger("mrp.server").warning(
                        "LAN access was disabled after the selected listener stopped unexpectedly; local access remains available."
                    )

        lan_watch = asyncio.create_task(watch_lan_listener_exit())
        try:
            await local_task
        finally:
            if lan_task is not None and not lan_task.done():
                lan_server.should_exit = True
                await lan_task
            lan_watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await lan_watch
    finally:
        application.state.stop_lan_listener = None
        application.state.stop_local_listener = None
        if lan_task is not None and not lan_task.done():
            lan_server.should_exit = True
            await lan_task
        if not local_task.done():
            local_server.should_exit = True
            await local_task
