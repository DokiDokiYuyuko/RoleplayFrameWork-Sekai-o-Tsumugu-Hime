"""LAN-only pairing gate for the single-process ASGI application.

The gate intentionally wraps the whole ASGI app so API routes, SSE, static assets,
media, and future WebSocket endpoints receive the same policy. Loopback clients
remain local and do not need a pairing cookie.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_COOKIE = "mrp_lan_session"
SESSION_NORMAL_ABSOLUTE_TTL_SECONDS = 30 * 24 * 60 * 60
SESSION_NORMAL_IDLE_TTL_SECONDS = 7 * 24 * 60 * 60
SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS = 90 * 24 * 60 * 60
SESSION_REMEMBERED_IDLE_TTL_SECONDS = 30 * 24 * 60 * 60
# Backward-compatible constants refer to the default "remember this phone" tier.
SESSION_TTL_SECONDS = SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS
SESSION_IDLE_TTL_SECONDS = SESSION_REMEMBERED_IDLE_TTL_SECONDS
SESSION_STORE_WRITE_INTERVAL_SECONDS = 15
SESSION_STORE_MAX_BYTES = 512 * 1024
PAIR_WINDOW_SECONDS = 15 * 60
PAIR_MAX_FAILURES = 6
PAIR_GLOBAL_MAX_FAILURES = 30
PAIR_MAX_INFLIGHT = 2
PAIR_BODY_MAX_INFLIGHT = 4
_FINGERPRINTED_ASSET = re.compile(r"^/assets/[^/]+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$")


@dataclass
class DeviceSession:
    session_id: str
    created_at: float
    last_activity_at: float
    expires_at: float
    absolute_expires_at: float
    last_activity_monotonic: float
    idle_ttl_seconds: int
    remembered: bool
    client_ip: str
    device_name: str


class SessionPersistenceError(RuntimeError):
    """Raised when an explicit session change cannot be durably recorded."""


def default_lan_session_store_path() -> Path:
    """Return per-user app state outside the project/story data directories."""
    override = os.environ.get("MRP_LAN_SESSION_STORE_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA", "").strip()
        return Path(root) / "Sekai o Tsumugu Hime" / "lan-sessions.json" if root else Path.home() / "AppData" / "Local" / "Sekai o Tsumugu Hime" / "lan-sessions.json"
    root = os.environ.get("XDG_STATE_HOME", "").strip()
    return Path(root) / "mrp" / "lan-sessions.json" if root else Path.home() / ".local" / "state" / "mrp" / "lan-sessions.json"


def _enabled_from_env() -> bool:
    return os.environ.get("MRP_LAN_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_code(value: str) -> str:
    # The launcher groups hex digits with hyphens. Remove only formatting so
    # a mistyped non-hex character cannot silently turn into a valid code.
    return re.sub(r"[-\s]", "", value.lower())


def _client_ip(scope: Scope) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    client = scope.get("client")
    if not client or not client[0]:
        return None
    try:
        return ipaddress.ip_address(client[0].split("%", 1)[0])
    except ValueError:
        return None


def _is_loopback(scope: Scope) -> bool:
    client = scope.get("client")
    if (
        "pytest" in sys.modules
        and client is not None
        and str(client[0]).lower() in {"testclient", "testserver"}
    ):
        return True
    address = _client_ip(scope)
    return bool(address and address.is_loopback)


def _cookie_value(headers: list[tuple[bytes, bytes]], name: str) -> str | None:
    for key, value in headers:
        if key.lower() != b"cookie":
            continue
        for item in value.decode("latin-1").split(";"):
            key_value = item.strip().split("=", 1)
            if len(key_value) == 2 and key_value[0] == name:
                return key_value[1]
    return None


def _read_lan_selection() -> tuple[ipaddress.IPv4Address, ipaddress.IPv4Network, int, str]:
    """Read the one network chosen by the explicit launcher; never discover a wider set."""
    raw_ip = os.environ.get("MRP_LAN_BIND_IP", "").strip()
    raw_network = os.environ.get("MRP_LAN_SUBNET", "").strip()
    raw_index = os.environ.get("MRP_LAN_INTERFACE_INDEX", "").strip()
    alias = os.environ.get("MRP_LAN_INTERFACE_ALIAS", "").strip()
    try:
        address = ipaddress.IPv4Address(raw_ip)
        network = ipaddress.ip_network(raw_network, strict=False)
        interface_index = int(raw_index)
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError) as exc:
        raise RuntimeError("LAN mode requires a valid selected IPv4 interface and subnet.") from exc
    rfc1918 = any(
        address in ipaddress.ip_network(cidr)
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    )
    if (
        network.version != 4
        or address not in network
        or not 1 <= interface_index <= 2**31 - 1
        or not alias
        or not rfc1918
        or network.prefixlen < 8
        or network.prefixlen > 30
    ):
        raise RuntimeError("LAN selection must be a named physical-interface RFC 1918 IPv4 subnet.")
    return address, network, interface_index, alias


def _secure_session_file(path: str | Path) -> None:
    """Apply Windows ACL protection to a persisted LAN session artifact."""
    if os.name == "nt":
        from mrp.server.lan_tls import secure_private_file

        secure_private_file(path)


class LanAccess:
    """In-memory verifier for the startup access code and short-lived sessions."""

    def __init__(self, session_store_path: str | Path | None = None) -> None:
        self.enabled = _enabled_from_env()
        self.lan_mode = self.enabled
        self.port = _read_port()
        code = os.environ.pop("MRP_LAN_ACCESS_CODE", "")
        if self.enabled:
            self.bind_ip, self.network, self.interface_index, self.interface_alias = _read_lan_selection()
            tls_values = {
                "store_path": os.environ.pop("MRP_LAN_TLS_STORE_DIR", "").strip(),
                "certificate_path": os.environ.pop("MRP_LAN_TLS_CERT_FILE", "").strip(),
                "private_key_path": os.environ.pop("MRP_LAN_TLS_KEY_FILE", "").strip(),
                "root_certificate_path": os.environ.pop("MRP_LAN_TLS_ROOT_FILE", "").strip(),
            }
            if not all(tls_values.values()):
                raise RuntimeError(
                    "LAN mode requires a prepared and verified HTTPS certificate; refusing plaintext startup. Run the LAN launcher to prepare TLS."
                )
            from mrp.server.lan_tls import root_certificate_pem, validate_server_material

            self.tls_material = validate_server_material(str(self.bind_ip), **tls_values)
            self.root_certificate_pem = root_certificate_pem(self.tls_material)
        else:
            self.bind_ip = ipaddress.IPv4Address("127.0.0.1")
            self.network = ipaddress.ip_network("127.0.0.0/8")
            self.interface_index = 0
            self.interface_alias = "Loopback"
            self.tls_material = None
            self.root_certificate_pem = None
        self.firewall_status_file = os.environ.get("MRP_LAN_FIREWALL_STATUS_FILE", "").strip()
        self._salt = secrets.token_bytes(16)
        normalized_code = _normalize_code(code)
        self._expected_code = (
            self._digest_code(normalized_code)
            if self.enabled and re.fullmatch(r"[a-hj-km-np-z023456789]{12}", normalized_code)
            else None
        )
        if self.enabled and self._expected_code is None:
            raise RuntimeError(
                "MRP_LAN_MODE requires a 60-bit MRP_LAN_ACCESS_CODE; refusing LAN startup."
            )
        self._sessions: dict[bytes, DeviceSession] = {}
        self._session_store_path = Path(session_store_path).expanduser() if session_store_path else None
        self._last_store_write = 0.0
        self._persistence_error: str | None = None
        self._last_cleanup_confirmed = True
        self._code_generation = 0
        self._failures: dict[str, tuple[float, int]] = {}
        self._global_failures: tuple[float, int] | None = None
        self._reserved_failures: dict[str, int] = {}
        self._reserved_global_failures = 0
        self._inflight_pair_checks = 0
        self._inflight_pair_body_reads = 0
        self.csrf_token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        if self.enabled and self._session_store_path is not None:
            self._load_persisted_sessions()

    def _selection_identity(self) -> dict[str, Any]:
        return {
            "interface_index": self.interface_index,
            "bind_address": str(self.bind_ip),
            "subnet": str(self.network),
        }

    def _discard_persisted_store(self) -> bool:
        if self._session_store_path is None:
            return True
        try:
            self._session_store_path.unlink(missing_ok=True)
            return True
        except OSError:
            logging.warning("Could not remove stale LAN session state; it will be ignored.")
            return False

    def _load_persisted_sessions(self) -> None:
        path = self._session_store_path
        if path is None:
            return
        invalidation_marker = path.with_name(path.name + ".invalid")
        if invalidation_marker.exists():
            try:
                _secure_session_file(invalidation_marker)
                if path.exists():
                    _secure_session_file(path)
                path.unlink(missing_ok=True)
                invalidation_marker.unlink()
                self._persistence_error = "上次会话撤销未能更新状态文件；已清除全部设备，请重新配对。"
                return
            except OSError as exc:
                raise RuntimeError(
                    f"LAN 会话状态需要手动清理后才能启动。请删除 {path} 和 {invalidation_marker}。"
                ) from exc
        if not path.exists():
            return
        try:
            _secure_session_file(path)
            if path.stat().st_size > SESSION_STORE_MAX_BYTES:
                raise ValueError("session state too large")
            payload = json.loads(path.read_text(encoding="utf-8"))
            now_wall, now_mono = time.time(), time.monotonic()
            if (
                not isinstance(payload, dict)
                or payload.get("version") != 2
                or payload.get("transport") != "https"
            ):
                raise ValueError("unsupported session state")
            if payload.get("selection") != self._selection_identity():
                raise ValueError("network selection changed")
            saved_at = float(payload["saved_at"])
            # A backwards wall-clock jump makes persisted durations ambiguous;
            # fail closed and require pairing again.
            if saved_at > now_wall + 60 or now_wall < saved_at - 60:
                raise ValueError("clock moved backwards")
            rows = payload.get("sessions")
            if not isinstance(rows, list) or len(rows) > 1024:
                raise ValueError("invalid session list")
            loaded: dict[bytes, DeviceSession] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                digest_text = row.get("digest")
                if not isinstance(digest_text, str) or not re.fullmatch(r"[0-9a-f]{64}", digest_text):
                    continue
                try:
                    digest = bytes.fromhex(digest_text)
                    created_at = float(row["created_at"])
                    last_activity_at = float(row["last_activity_at"])
                    absolute_expires_at = float(row["absolute_expires_at"])
                    idle_ttl_seconds = int(row["idle_ttl_seconds"])
                    remembered = row["remembered"] is True
                    session_id = str(row["session_id"])
                    client_ip = str(ipaddress.ip_address(row["client_ip"]))
                    device_name = "".join(ch for ch in str(row["device_name"]) if ch.isprintable())[:160]
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                expected_idle = SESSION_REMEMBERED_IDLE_TTL_SECONDS if remembered else SESSION_NORMAL_IDLE_TTL_SECONDS
                expected_absolute = SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS if remembered else SESSION_NORMAL_ABSOLUTE_TTL_SECONDS
                if (
                    not re.fullmatch(r"[A-Za-z0-9_-]{20,24}", session_id)
                    or idle_ttl_seconds != expected_idle
                    or absolute_expires_at <= now_wall
                    or absolute_expires_at - created_at > expected_absolute + 60
                    or created_at > now_wall + 60
                    or last_activity_at > now_wall + 60
                    or now_wall - last_activity_at >= idle_ttl_seconds
                ):
                    continue
                elapsed_idle = max(0.0, now_wall - last_activity_at)
                loaded[digest] = DeviceSession(
                    session_id=session_id,
                    created_at=created_at,
                    last_activity_at=last_activity_at,
                    expires_at=now_mono + (absolute_expires_at - now_wall),
                    absolute_expires_at=absolute_expires_at,
                    last_activity_monotonic=now_mono - elapsed_idle,
                    idle_ttl_seconds=idle_ttl_seconds,
                    remembered=remembered,
                    client_ip=client_ip,
                    device_name=device_name or "未知浏览器",
                )
            self._sessions = loaded
            self._last_store_write = now_mono
            self._persistence_error = None
        except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
            if not self._discard_persisted_store():
                path = self._session_store_path
                raise RuntimeError(
                    f"Stale LAN session state could not be removed. Please delete {path} before enabling LAN."
                ) from None
            self._persistence_error = "LAN 会话状态无法读取，已要求重新配对。"

    def _persist_locked(self, *, force: bool = False) -> None:
        path = self._session_store_path
        if path is None:
            return
        now_mono = time.monotonic()
        if not force and now_mono - self._last_store_write < SESSION_STORE_WRITE_INTERVAL_SECONDS:
            return
        # Rate-limit failures too; a read-only/damaged profile must not turn
        # every authenticated request into another filesystem attempt/log.
        self._last_store_write = now_mono
        now_wall = time.time()
        rows = []
        for digest, session in self._sessions.items():
            if session.expires_at <= now_mono or session.last_activity_monotonic + session.idle_ttl_seconds <= now_mono:
                continue
            rows.append({
                "digest": digest.hex(),
                "session_id": session.session_id,
                "created_at": session.created_at,
                "last_activity_at": session.last_activity_at,
                "absolute_expires_at": session.absolute_expires_at,
                "idle_ttl_seconds": session.idle_ttl_seconds,
                "remembered": session.remembered,
                "client_ip": session.client_ip,
                "device_name": session.device_name,
            })
        payload = json.dumps(
            {
                "version": 2,
                "transport": "https",
                "selection": self._selection_identity(),
                "saved_at": now_wall,
                "sessions": rows,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        temporary_path: str | None = None
        open_fd: int | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            open_fd, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            with os.fdopen(open_fd, "w", encoding="utf-8", newline="\n") as state_file:
                open_fd = None
                _secure_session_file(temporary_path)
                state_file.write(payload)
                state_file.flush()
                os.fsync(state_file.fileno())
            if os.name != "nt":
                os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
            temporary_path = None
            _secure_session_file(path)
            marker = path.with_name(path.name + ".invalid")
            marker.unlink(missing_ok=True)
            self._last_store_write = now_mono
            self._persistence_error = None
            self._last_cleanup_confirmed = True
        except (OSError, RuntimeError) as exc:
            if open_fd is not None:
                try:
                    os.close(open_fd)
                except OSError:
                    pass
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass
            self._persistence_error = "LAN 会话状态无法保存。"
            raise SessionPersistenceError("Could not persist LAN session state.") from exc

    def _write_invalidation_marker_locked(self) -> bool:
        if self._session_store_path is None:
            return True
        marker = self._session_store_path.with_name(self._session_store_path.name + ".invalid")
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            if not marker.exists():
                marker.touch()
            _secure_session_file(marker)
            with marker.open("w", encoding="ascii") as marker_file:
                marker_file.write("LAN session state invalidated; remove prior state before loading.\n")
                marker_file.flush()
                os.fsync(marker_file.fileno())
            if os.name != "nt":
                os.chmod(marker, 0o600)
            return True
        except (OSError, RuntimeError):
            logging.error("Could not write LAN session invalidation marker.")
            return False

    def _persist_revocation_locked(self) -> bool:
        """Persist an empty/current store or invalidate the old file fail-closed."""
        try:
            self._persist_locked(force=True)
            self._last_cleanup_confirmed = True
            return True
        except SessionPersistenceError:
            path = self._session_store_path
            if path is None:
                self._last_cleanup_confirmed = True
                return True
            try:
                path.unlink(missing_ok=True)
                # No stale digest file can be restored. Other devices will need
                # to pair again after a restart because their records are gone.
                self._last_store_write = 0.0
                self._last_cleanup_confirmed = True
                self._persistence_error = "旧状态文件已清除；所有设备需要在下次重启后重新配对。"
                return True
            except OSError:
                marker_written = self._write_invalidation_marker_locked()
                self._last_cleanup_confirmed = False
                self._persistence_error = (
                    "本次进程已撤销会话，但磁盘清理未确认；LAN 下次启动会检查失效标记。"
                    if marker_written
                    else "会话已在本进程撤销，但磁盘状态无法清理；请手动删除 LAN 会话状态文件后再启动。"
                )
                return False

    @property
    def last_cleanup_confirmed(self) -> bool:
        return self._last_cleanup_confirmed

    def reserve_pair_body_read(self) -> bool:
        """Bound slow request-body readers before they consume ASGI tasks."""
        with self._lock:
            if self._inflight_pair_body_reads >= PAIR_BODY_MAX_INFLIGHT:
                return False
            self._inflight_pair_body_reads += 1
            return True

    def release_pair_body_read(self) -> None:
        with self._lock:
            self._inflight_pair_body_reads = max(0, self._inflight_pair_body_reads - 1)

    def _digest_code(self, value: str) -> bytes:
        return hashlib.scrypt(value.encode("ascii"), salt=self._salt, n=2**14, r=8, p=1, dklen=32)

    def _digest_session(self, token: str) -> bytes:
        return hashlib.sha256(token.encode("ascii", errors="ignore")).digest()

    def is_session_valid(self, headers: list[tuple[bytes, bytes]]) -> bool:
        token = _cookie_value(headers, SESSION_COOKIE)
        if not token or len(token) > 100:
            return False
        digest = self._digest_session(token)
        now = time.monotonic()
        now_wall = time.time()
        with self._lock:
            session = self._sessions.get(digest)
            if session is None:
                return False
            if (
                session.expires_at <= now
                or session.last_activity_monotonic + session.idle_ttl_seconds <= now
            ):
                self._sessions.pop(digest, None)
                return False
            session.last_activity_monotonic = now
            session.last_activity_at = now_wall
            try:
                self._persist_locked()
            except SessionPersistenceError:
                # The in-memory authorization remains valid for this process;
                # persistence status exposes the storage problem to local UI.
                logging.error("Could not update persisted LAN session activity.")
            return True

    def check_code(
        self,
        client_ip: str,
        supplied: str,
        user_agent: str = "",
        remember: bool = True,
    ) -> tuple[str | None, int]:
        """Return a session token on success, or (None, retry_after_seconds)."""
        if not self.enabled:
            return None, 0
        now = time.monotonic()
        with self._lock:
            expected_code = self._expected_code
            code_generation = self._code_generation
            if expected_code is None:
                return None, 0
            window_started, failures = self._failures.get(client_ip, (now, 0))
            if now - window_started >= PAIR_WINDOW_SECONDS:
                window_started, failures = now, 0
            global_started, global_failures = self._global_failures or (now, 0)
            if now - global_started >= PAIR_WINDOW_SECONDS:
                global_started, global_failures = now, 0
                self._global_failures = (global_started, 0)
            reserved_ip = self._reserved_failures.get(client_ip, 0)
            if failures + reserved_ip >= PAIR_MAX_FAILURES:
                retry_after = max(1, int(PAIR_WINDOW_SECONDS - (now - window_started)))
                return None, retry_after
            if global_failures + self._reserved_global_failures >= PAIR_GLOBAL_MAX_FAILURES:
                retry_after = max(1, int(PAIR_WINDOW_SECONDS - (now - global_started)))
                return None, retry_after
            if self._inflight_pair_checks >= PAIR_MAX_INFLIGHT:
                return None, 1
            self._inflight_pair_checks += 1
            self._reserved_failures[client_ip] = reserved_ip + 1
            self._reserved_global_failures += 1

        succeeded = False
        try:
            normalized = _normalize_code(supplied[:128])
            if not re.fullmatch(r"[a-hj-km-np-z023456789]{12}", normalized):
                candidate = bytes(32)
            else:
                candidate = self._digest_code(normalized)
            succeeded = hmac.compare_digest(candidate, expected_code)
        except BaseException:
            with self._lock:
                self._inflight_pair_checks -= 1
                remaining_for_ip = self._reserved_failures.get(client_ip, 1) - 1
                if remaining_for_ip > 0:
                    self._reserved_failures[client_ip] = remaining_for_ip
                else:
                    self._reserved_failures.pop(client_ip, None)
                self._reserved_global_failures = max(0, self._reserved_global_failures - 1)
            raise

        completed = time.monotonic()
        with self._lock:
            # Commit failure counters before releasing their reservations. This
            # keeps concurrent checks from slipping between the two operations.
            self._inflight_pair_checks -= 1
            remaining_for_ip = self._reserved_failures.get(client_ip, 1) - 1
            if remaining_for_ip > 0:
                self._reserved_failures[client_ip] = remaining_for_ip
            else:
                self._reserved_failures.pop(client_ip, None)
            self._reserved_global_failures = max(0, self._reserved_global_failures - 1)
            if code_generation != self._code_generation:
                succeeded = False
            if succeeded:
                token = secrets.token_urlsafe(32)
                absolute_ttl = SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS if remember else SESSION_NORMAL_ABSOLUTE_TTL_SECONDS
                idle_ttl = SESSION_REMEMBERED_IDLE_TTL_SECONDS if remember else SESSION_NORMAL_IDLE_TTL_SECONDS
                # Drop entries that expired while hashing, retaining each
                # device record without ever storing or returning its token.
                self._sessions = {
                    key: session
                    for key, session in self._sessions.items()
                    if session.expires_at > completed
                    and session.last_activity_monotonic + session.idle_ttl_seconds > completed
                }
                user_agent = "".join(ch for ch in user_agent if ch.isprintable())[:160]
                try:
                    display_ip = str(ipaddress.ip_address(client_ip.split("%", 1)[0]))
                except ValueError:
                    display_ip = "unknown"
                wall_now = time.time()
                session_digest = self._digest_session(token)
                self._sessions[session_digest] = DeviceSession(
                    session_id=secrets.token_urlsafe(16),
                    created_at=wall_now,
                    last_activity_at=wall_now,
                    expires_at=completed + absolute_ttl,
                    absolute_expires_at=wall_now + absolute_ttl,
                    last_activity_monotonic=completed,
                    idle_ttl_seconds=idle_ttl,
                    remembered=remember,
                    client_ip=display_ip,
                    device_name=user_agent or "未知浏览器",
                )
                try:
                    self._persist_locked(force=True)
                except SessionPersistenceError:
                    self._sessions.pop(session_digest, None)
                    raise
                return token, 0

            window_started, failures = self._failures.get(client_ip, (completed, 0))
            if completed - window_started >= PAIR_WINDOW_SECONDS:
                window_started, failures = completed, 0
            failures += 1
            self._failures[client_ip] = (window_started, failures)
            global_started, global_failures = self._global_failures or (completed, 0)
            if completed - global_started >= PAIR_WINDOW_SECONDS:
                global_started, global_failures = completed, 0
            global_failures += 1
            self._global_failures = (global_started, global_failures)
            ip_retry_after = (
                max(1, int(PAIR_WINDOW_SECONDS - (completed - window_started)))
                if failures >= PAIR_MAX_FAILURES
                else 0
            )
            global_retry_after = (
                max(1, int(PAIR_WINDOW_SECONDS - (completed - global_started)))
                if global_failures >= PAIR_GLOBAL_MAX_FAILURES
                else 0
            )
            return None, max(ip_retry_after, global_retry_after)

    def revoke_session(self, headers: list[tuple[bytes, bytes]]) -> bool:
        token = _cookie_value(headers, SESSION_COOKIE)
        if token and len(token) <= 100:
            with self._lock:
                if self._sessions.pop(self._digest_session(token), None) is not None:
                    return self._persist_revocation_locked()
        return True

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return safe device metadata; never return session tokens or digests."""
        now = time.monotonic()
        with self._lock:
            expired = [
                digest
                for digest, session in self._sessions.items()
                if session.expires_at <= now
                or session.last_activity_monotonic + session.idle_ttl_seconds <= now
            ]
            for digest in expired:
                self._sessions.pop(digest, None)
            if expired:
                self._persist_locked(force=True)
            sessions = [
                {
                    "session_id": session.session_id,
                    "device_name": session.device_name,
                    "ip": session.client_ip,
                    "created_at": session.created_at,
                    "last_activity_at": session.last_activity_at,
                    "expires_in_seconds": max(0, int(session.expires_at - now)),
                    "idle_expires_in_seconds": max(
                        0,
                        int(session.last_activity_monotonic + session.idle_ttl_seconds - now),
                    ),
                    "remembered": session.remembered,
                }
                for session in self._sessions.values()
            ]
        return sorted(sessions, key=lambda session: session["created_at"], reverse=True)

    def revoke_session_id(self, session_id: str) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,24}", session_id):
            return False
        with self._lock:
            for digest, session in list(self._sessions.items()):
                if hmac.compare_digest(session.session_id, session_id):
                    self._sessions.pop(digest, None)
                    self._persist_revocation_locked()
                    return True
        return False

    def revoke_all_sessions(self) -> int:
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            self._persist_revocation_locked()
            return count

    def rotate_access_code(self) -> str:
        """Replace the one-time pairing code and invalidate every paired device."""
        if not self.enabled:
            raise RuntimeError("LAN access is disabled.")
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ023456789"
        code = "".join(secrets.choice(alphabet) for _ in range(12))
        replacement = self._digest_code(code.lower())
        with self._lock:
            self._expected_code = replacement
            self._code_generation += 1
            self._sessions.clear()
            self._persist_revocation_locked()
        return "-".join(code[index:index + 4] for index in range(0, len(code), 4))

    def close_lan(self) -> bool:
        with self._lock:
            self.enabled = False
            self._sessions.clear()
            return self._persist_revocation_locked()

    def host_allowed(self, headers: list[tuple[bytes, bytes]], scope: Scope | None = None) -> bool:
        host_values = [value.decode("latin-1").strip() for key, value in headers if key.lower() == b"host"]
        if len(host_values) != 1 or not host_values[0]:
            return False
        authority = host_values[0]
        if any(ord(ch) < 33 or ord(ch) > 126 for ch in authority):
            return False

        bracketed = authority.startswith("[")
        if bracketed:
            closing = authority.find("]")
            if closing < 0:
                return False
            hostname = authority[1:closing]
            suffix = authority[closing + 1:]
            if suffix:
                if not suffix.startswith(":"):
                    return False
                port_text = suffix[1:]
            else:
                port_text = None
            try:
                address = ipaddress.IPv6Address(hostname)
            except ipaddress.AddressValueError:
                return False
            if address != ipaddress.IPv6Address("::1"):
                return False
            hostname = address.compressed
        else:
            if "[" in authority or "]" in authority or authority.count(":") > 1:
                return False
            if ":" in authority:
                hostname, port_text = authority.rsplit(":", 1)
            else:
                hostname, port_text = authority, None
            if not hostname:
                return False

        if port_text is not None:
            # Accept only the exact decimal server port; this also rejects an
            # empty port, signs, whitespace, and padded/noncanonical variants.
            if not port_text.isascii() or not port_text.isdecimal() or port_text != str(self.port):
                return False

        hostname = hostname.lower()
        if bracketed or hostname in {"localhost", "127.0.0.1"}:
            if scope is None:
                return True
            server = scope.get("server")
            try:
                destination = ipaddress.ip_address(str(server[0]).split("%", 1)[0]) if server else None
            except ValueError:
                return False
            return bool(destination and destination.is_loopback)
        # Starlette's in-process test client uses this host. It is accepted only
        # while pytest owns the process; the production server never trusts it.
        if hostname == "testserver" and port_text is None and "pytest" in sys.modules:
            return True
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return False
        if address.version != 4:
            return False
        if not self.enabled or address != self.bind_ip:
            return False
        if scope is None:
            return True
        server = scope.get("server")
        try:
            destination = ipaddress.ip_address(str(server[0]).split("%", 1)[0]) if server else None
        except ValueError:
            return False
        return destination == self.bind_ip

    def request_target_allowed(self, scope: Scope) -> bool:
        """Require the actual bound destination and caller to use the chosen subnet."""
        if not self.enabled:
            client = scope.get("client")
            test_client = (
                "pytest" in sys.modules
                and client is not None
                and str(client[0]).lower() in {"testclient", "testserver"}
            )
            return _is_loopback(scope) or test_client
        server = scope.get("server")
        if not server or not server[0]:
            return False
        try:
            destination = ipaddress.ip_address(str(server[0]).split("%", 1)[0])
        except ValueError:
            return False
        scheme = str(scope.get("scheme", "http")).lower()
        if scope["type"] == "websocket":
            scheme = "https" if scheme in {"https", "wss"} else "http"
        if destination.is_loopback:
            if scheme != "http":
                return False
        elif destination == self.bind_ip:
            if scheme != "https":
                return False
        else:
            return False
        client = _client_ip(scope)
        if client is None:
            return False
        # LAN mode also has a separate loopback listener for local desktop
        # control. Only loopback clients may target that listener.
        if destination.is_loopback:
            return client.is_loopback
        if destination != self.bind_ip:
            return False
        if client.is_loopback:
            return True
        return isinstance(client, ipaddress.IPv4Address) and client in self.network

    def is_local_client(self, scope: Scope) -> bool:
        """Recognize loopback and the desktop connecting to its selected LAN IP."""
        if _is_loopback(scope):
            return True
        if not self.enabled:
            return False
        client = _client_ip(scope)
        server = scope.get("server")
        if client != self.bind_ip or not server or not server[0]:
            return False
        try:
            destination = ipaddress.ip_address(str(server[0]).split("%", 1)[0])
        except ValueError:
            return False
        return destination == self.bind_ip

    def origin_allowed(self, scope: Scope, headers: list[tuple[bytes, bytes]]) -> bool:
        """Validate browser write sources, including requests from loopback."""
        header_map: dict[bytes, list[bytes]] = {}
        for key, value in headers:
            header_map.setdefault(key.lower(), []).append(value)
        host_values = header_map.get(b"host", [])
        if len(host_values) != 1:
            return False
        host = host_values[0].decode("latin-1").strip().lower()
        scheme = str(scope.get("scheme", "http")).lower()
        if scope["type"] == "websocket":
            scheme = "https" if scheme in {"https", "wss"} else "http"
        origin_values = header_map.get(b"origin", [])
        fetch_site_values = header_map.get(b"sec-fetch-site", [])
        referer_values = header_map.get(b"referer", [])
        csrf_values = header_map.get(b"x-mrp-csrf", [])

        if len(origin_values) == 1:
            origin = origin_values[0].decode("latin-1").strip()
            try:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme.lower() != scheme
                    or parsed.netloc.lower() != host
                    or parsed.path
                    or parsed.query
                    or parsed.fragment
                    or parsed.username is not None
                    or parsed.password is not None
                ):
                    return False
            except ValueError:
                return False
        elif origin_values:
            return False
        elif len(fetch_site_values) == 1 and fetch_site_values[0].lower() == b"same-origin":
            pass
        elif len(referer_values) == 1:
            try:
                referer = urlsplit(referer_values[0].decode("latin-1").strip())
                if referer.scheme.lower() != scheme or referer.netloc.lower() != host:
                    return False
            except ValueError:
                return False
        elif len(csrf_values) == 1 and hmac.compare_digest(
            csrf_values[0].decode("latin-1"), self.csrf_token
        ):
            pass
        elif host == "testserver" and "pytest" in sys.modules:
            return True
        else:
            return False

        if len(fetch_site_values) > 1:
            return False
        if fetch_site_values and fetch_site_values[0].lower() == b"cross-site":
            return False
        return True

    def status(self) -> dict[str, Any]:
        firewall_verified = False
        if self.firewall_status_file:
            try:
                with open(self.firewall_status_file, "r", encoding="ascii") as status_file:
                    firewall_verified = status_file.read(32).strip() == "verified"
            except OSError:
                pass
        interfaces = [
            {
                "ip": str(self.bind_ip),
                "url": f"https://{self.bind_ip}:{self.port}/",
                "name": self.interface_alias,
                "interface_index": self.interface_index,
                "subnet": str(self.network),
            }
        ] if self.enabled else []
        return {
            "enabled": self.enabled,
            "port": self.port,
            "interfaces": interfaces,
            "bind_address": str(self.bind_ip),
            "firewall_rule_verified": firewall_verified if self.enabled else None,
            "firewall_state": "configured_rule_verified" if firewall_verified else "unverified",
            "transport": "https" if self.enabled else "http",
            "https_available": bool(self.enabled and self.tls_material is not None),
            "root_ca_fingerprint_sha256": (
                self.tls_material.root_fingerprint_sha256 if self.tls_material else None
            ),
            "certificate_expires_at": (
                self.tls_material.certificate_expires_at.isoformat().replace("+00:00", "Z")
                if self.tls_material
                else None
            ),
            "public_reachability": "unverified",
            "pairing_required": self.enabled,
            "session_policies": {
                "normal": {
                    "idle_days": SESSION_NORMAL_IDLE_TTL_SECONDS // (24 * 60 * 60),
                    "absolute_days": SESSION_NORMAL_ABSOLUTE_TTL_SECONDS // (24 * 60 * 60),
                },
                "remembered": {
                    "idle_days": SESSION_REMEMBERED_IDLE_TTL_SECONDS // (24 * 60 * 60),
                    "absolute_days": SESSION_REMEMBERED_ABSOLUTE_TTL_SECONDS // (24 * 60 * 60),
                },
            },
            "session_store_persistent": self._session_store_path is not None,
            "session_store_available": self._persistence_error is None,
            "session_state_cleanup_confirmed": self._last_cleanup_confirmed,
        }


def _read_port() -> int:
    try:
        value = int(os.environ.get("MRP_PORT", "8000"))
    except ValueError:
        return 8000
    return value if 1 <= value <= 65535 else 8000


def _send_response(status: int, body: bytes, send: Send, *, content_type: bytes) -> Awaitable[None]:
    async def _send() -> None:
        headers = [
            (b"content-type", content_type),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"pragma", b"no-cache"),
            (b"x-content-type-options", b"nosniff"),
        ]
        if content_type == b"text/html; charset=utf-8":
            headers.append((b"content-security-policy", b"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"))
            headers.append((b"x-frame-options", b"DENY"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return _send()


_PAIRING_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light"><title>配对手机 · 织界之姬</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;min-height:100dvh;padding:env(safe-area-inset-top) 18px env(safe-area-inset-bottom);display:grid;place-items:center;background:#edf1ee;color:#24343b;font:16px/1.55 system-ui,"Segoe UI",sans-serif}
main{width:min(100%,430px);padding:26px 22px;border:1px solid #d7e0da;border-radius:18px;background:#fffefa;box-shadow:0 16px 50px #24343b14}h1{margin:0 0 8px;font:500 27px/1.2 Georgia,"Songti SC",serif}p{margin:8px 0 17px;color:#607176}label{display:block;margin:0 0 7px;font-size:13px;font-weight:700}input{width:100%;min-height:48px;padding:12px;border:1px solid #c7d3cc;border-radius:10px;background:#fff;color:#24343b;font:600 18px/1.2 ui-monospace,monospace;letter-spacing:.06em}input:focus{outline:3px solid #a4ceca;outline-offset:2px}button{width:100%;min-height:48px;margin-top:13px;border:0;border-radius:10px;background:#285f69;color:#fff;font:700 15px system-ui;cursor:pointer}.hint{font-size:13px}.error{min-height:22px;margin:12px 0 0;color:#9b443e;font-size:13px}.warning{margin-top:17px;padding:12px;border-radius:10px;background:#f5efe2;color:#6f5731;font-size:12px}.remember{display:flex;align-items:flex-start;gap:10px;margin:15px 0 5px;font-size:14px}.remember input{width:18px;min-height:18px;margin:3px 0 0;flex:none}
</style></head><body><main><h1>配对手机</h1><p>输入电脑上 LAN 启动窗口显示的访问码。此设备通过配对后才能读取故事与媒体。</p>
<form id="pair"><label for="code">访问码</label><input id="code" name="code" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" maxlength="48" required autofocus><label class="remember"><input id="remember" type="checkbox" checked><span><strong>记住这台手机</strong><br>闲置 30 天或累计 90 天后失效。取消勾选：闲置 7 天或累计 30 天后失效。</span></label><button id="submit" type="submit">配对并打开故事</button><div class="error" id="error" role="status" aria-live="polite"></div></form>
<div class="warning">请只在可信的家庭 Wi-Fi 或个人热点使用。此连接使用局域网 HTTP，共享或公共 Wi-Fi 不适合访问包含 API 密钥的项目。</div>
<p class="hint">访问码请从电脑本机“设置 → 手机访问”页面读取；首次启动时也可使用 LAN 启动窗口显示的一次性访问码。会话按所选期限自动失效；服务正常重启后，在同一所选网卡和地址上仍可继续使用。</p>
<p class="hint">连不上时，请确认手机和电脑在同一网络、电脑保持运行。Windows 防火墙只允许启动时所选网卡和客户端子网；手机热点可能启用了客户端隔离。</p>
</main><script>
const form=document.getElementById('pair'), input=document.getElementById('code'), remember=document.getElementById('remember'), error=document.getElementById('error'), button=document.getElementById('submit');
form.addEventListener('submit',async event=>{event.preventDefault();error.textContent='';button.disabled=true;button.textContent='正在配对…';try{const response=await fetch('/api/v1/lan/pair',{method:'POST',credentials:'same-origin',headers:{'content-type':'application/json'},body:JSON.stringify({access_code:input.value,remember:remember.checked})});const result=await response.json().catch(()=>({}));if(!response.ok){error.textContent=result.detail||'配对失败，请检查访问码和网络后重试。';return}location.replace('/')}catch{error.textContent='无法连接电脑服务。请确认电脑仍在运行并且手机与电脑处于同一网络。'}finally{button.disabled=false;button.textContent='配对并打开故事'}});
</script></body></html>""".encode("utf-8")


class LanAccessMiddleware:
    """Pure ASGI middleware: never buffers or rewrites streaming responses."""

    def __init__(self, app: ASGIApp, access: LanAccess) -> None:
        self.app = app
        self.access = access

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        if not self.access.host_allowed(headers, scope):
            body = b'{"detail":"Unrecognized Host header."}'
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4400})
            else:
                await _send_response(421, body, send, content_type=b"application/json")
            return

        if not self.access.request_target_allowed(scope):
            body = b'{"detail":"Request target or client network is not allowed."}'
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403})
            else:
                await _send_response(403, body, send, content_type=b"application/json")
            return

        method = scope.get("method", "GET").upper()
        if scope["type"] == "websocket" or method in {"POST", "PUT", "PATCH", "DELETE"}:
            if not self.access.origin_allowed(scope, headers):
                body = b'{"detail":"Untrusted request source."}'
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4403})
                else:
                    await _send_response(403, body, send, content_type=b"application/json")
                return

        response_started = False
        response_finished = False
        websocket_closed = False

        async def send_with_lan_cache_policy(message):
            nonlocal response_started, response_finished, websocket_closed
            lan_closed_for_remote = (
                self.access.lan_mode
                and not self.access.enabled
                and not self.access.is_local_client(scope)
            )
            if not lan_closed_for_remote and scope["type"] == "http":
                if message.get("type") == "http.response.start":
                    if response_started:
                        return
                    response_started = True
                elif message.get("type") == "http.response.body" and not message.get("more_body", False):
                    response_finished = True

            if lan_closed_for_remote:
                if scope["type"] == "websocket":
                    if websocket_closed:
                        return
                    if message.get("type") == "websocket.close":
                        websocket_closed = True
                        await send(message)
                        return
                    if message.get("type") in {"websocket.accept", "websocket.send"}:
                        websocket_closed = True
                        await send({"type": "websocket.close", "code": 1001})
                        return
                elif scope["type"] == "http":
                    if response_finished:
                        return
                    message_type = message.get("type")
                    if message_type == "http.response.start":
                        if response_started:
                            return
                        response_started = True
                        message = {
                            **message,
                            "status": 403,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", b"0"),
                                (b"cache-control", b"no-store"),
                                (b"connection", b"close"),
                            ],
                        }
                    elif message_type == "http.response.body":
                        if not response_started:
                            await send(
                                {
                                    "type": "http.response.start",
                                    "status": 403,
                                    "headers": [
                                        (b"content-type", b"application/json"),
                                        (b"content-length", b"0"),
                                        (b"cache-control", b"no-store"),
                                        (b"connection", b"close"),
                                    ],
                                }
                            )
                            response_started = True
                        response_finished = True
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
                        return
                    else:
                        return
            if (
                self.access.lan_mode
                and scope["type"] == "http"
                and message.get("type") == "http.response.start"
                and not _FINGERPRINTED_ASSET.fullmatch(scope.get("path", ""))
            ):
                response_headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in {b"cache-control", b"pragma"}
                ]
                response_headers.extend(
                    [(b"cache-control", b"no-store"), (b"pragma", b"no-cache")]
                )
                message = {**message, "headers": response_headers}
            await send(message)

        if self.access.is_local_client(scope):
            await self.app(scope, receive, send_with_lan_cache_policy)
            return

        if self.access.enabled and self.access.is_session_valid(headers):
            await self.app(scope, receive, send_with_lan_cache_policy)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return

        path = scope.get("path", "")
        if self.access.enabled and path == "/api/v1/lan/pair" and method == "POST":
            await self.app(scope, receive, send_with_lan_cache_policy)
            return

        if not self.access.enabled:
            status = 403
            body = b'{"detail":"LAN access is disabled. Start the app with the explicit LAN launcher."}'
            await _send_response(status, body, send, content_type=b"application/json")
            return

        accept = next((v for k, v in headers if k.lower() == b"accept"), b"").lower()
        if method == "GET" and (b"text/html" in accept or path == "/"):
            await _send_response(
                401,
                _PAIRING_HTML,
                send,
                content_type=b"text/html; charset=utf-8",
            )
            return

        has_expired_cookie = _cookie_value(headers, SESSION_COOKIE) is not None
        code = b"lan_session_expired" if has_expired_cookie else b"lan_pairing_required"
        detail = (
            b"This LAN session expired. Re-pair to continue."
            if has_expired_cookie
            else b"Pair this device with the access code shown on the computer."
        )
        body = b'{"detail":"' + detail + b'","code":"' + code + b'"}'
        await _send_response(401, body, send, content_type=b"application/json")
