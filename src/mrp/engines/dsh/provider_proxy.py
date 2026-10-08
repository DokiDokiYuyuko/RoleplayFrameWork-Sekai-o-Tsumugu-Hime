"""Loopback HTTP bridge for OpenRouter routing fields omitted by the DSH SDK.

DSH accepts an OpenAI-compatible base URL but has no per-request provider
preferences argument. This bridge preserves the SDK request and response,
including SSE, while adding the same provider object used by direct mode.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from mrp.reasoning import normalize_reasoning


_HOP_HEADERS = {
    "connection", "content-length", "host", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
}


class ProviderRoutingProxy:
    def __init__(self, gateway: str, provider: str, allow_fallbacks: bool,
                 on_request: Callable[[dict[str, Any]], None] | None = None,
                 sampling: dict[str, float] | None = None) -> None:
        self.gateway = gateway.rstrip("/")
        self.provider = provider
        self.allow_fallbacks = allow_fallbacks
        self.on_request = on_request
        self.sampling = dict(sampling or {})
        self.capacity_limit: int | None = None
        self.output_reserve = 0
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, _format: str, *args: object) -> None:
                pass  # Requests contain private story text and an API key.

            def do_POST(self) -> None:
                base_path = urlsplit(bridge.gateway).path.rstrip("/")
                if not self.path.startswith(base_path + "/"):
                    self.send_error(404)
                    return
                suffix = self.path[len(base_path):]
                if suffix.split("?", 1)[0] != "/chat/completions":
                    self.send_error(404)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size <= 0 or size > 32 * 1024 * 1024:
                        self.send_error(413)
                        return
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        self.send_error(400)
                        return
                    if bridge.provider:
                        body["provider"] = {
                            "order": [bridge.provider],
                            "allow_fallbacks": bridge.allow_fallbacks,
                        }
                    body.update(bridge.sampling)
                    normalize_reasoning(body, bridge.gateway)
                    if bridge.on_request is not None:
                        try:
                            bridge.on_request(body)
                        except Exception:
                            pass  # Archiving must never block a model request.
                    if bridge.capacity_limit is not None:
                        from mrp.shared.prompt import estimate_tokens
                        estimated = sum(estimate_tokens(str(message.get("content") or "")) + 8
                                        for message in body.get("messages", []) if isinstance(message, dict))
                        if estimated + bridge.output_reserve > bridge.capacity_limit:
                            self.send_error(413, "Model context capacity exceeded")
                            return
                    headers = {
                        key: value for key, value in self.headers.items()
                        if key.lower() not in _HOP_HEADERS
                    }
                    upstream = bridge.gateway + suffix
                    with httpx.Client(timeout=httpx.Timeout(300.0, connect=20.0)) as client:
                        with client.stream("POST", upstream, json=body, headers=headers) as response:
                            self.send_response(response.status_code)
                            for key, value in response.headers.items():
                                if key.lower() not in _HOP_HEADERS:
                                    self.send_header(key, value)
                            self.end_headers()
                            self.close_connection = True
                            for chunk in response.iter_raw():
                                self.wfile.write(chunk)
                                self.wfile.flush()
                except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError):
                    try:
                        self.send_error(502, "Upstream request failed")
                    except OSError:
                        pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        path = urlsplit(self.gateway).path.rstrip("/")
        return f"http://127.0.0.1:{self._server.server_port}{path}"

    def configure(self, gateway: str, provider: str, allow_fallbacks: bool) -> None:
        self.gateway = gateway.rstrip("/")
        self.provider = provider
        self.allow_fallbacks = allow_fallbacks

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)
