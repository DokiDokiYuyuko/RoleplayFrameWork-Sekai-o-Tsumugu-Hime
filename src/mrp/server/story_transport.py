"""Project story command responses at the transport boundary, not in persistence."""
from __future__ import annotations

import json

from mrp.contracts.story import project_message, project_turn_run


def command_projection(value):
    if isinstance(value, list):
        return [command_projection(item) for item in value]
    if not isinstance(value, dict):
        return value
    if {"id", "session_id", "seq", "turn", "actor", "content"} <= value.keys():
        return project_message(value)
    if {"id", "session_id", "operation_id", "request_fingerprint", "slots", "status"} <= value.keys():
        return project_turn_run(value)
    return {key: command_projection(item) for key, item in value.items()}


class StoryCommandProjectionMiddleware:
    """Only successful JSON story commands; GET/inspection/export retain their contracts.

    A pure ASGI wrapper leaves streaming and connection cancellation untouched.
    The stored operation result remains a full fidelity persistence object.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope["type"] != "http" or scope.get("method") not in {"POST", "PATCH", "DELETE"}
                or not (path.startswith("/api/v1/sessions/") or path.startswith("/api/v1/scenarios/"))):
            return await self.app(scope, receive, send)
        start = None
        chunks = []

        async def projected_send(message):
            nonlocal start
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                if 200 <= message["status"] < 300 and headers.get(b"content-type", b"").startswith(b"application/json"):
                    start = message
                    return
            if message["type"] == "http.response.body" and start is not None:
                chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                body = b"".join(chunks)
                data = command_projection(json.loads(body))
                body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                start = {**start, "headers": [(key, value) for key, value in start.get("headers", [])
                    if key.lower() != b"content-length"] + [(b"content-length", str(len(body)).encode("ascii"))]}
                await send(start)
                await send({**message, "body": body})
                return
            await send(message)

        await self.app(scope, receive, projected_send)
