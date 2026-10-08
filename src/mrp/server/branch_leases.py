"""HTTP request lifetime adapter for branch runtime leases."""


class BranchLeaseMiddleware:
    """Pure ASGI: a lease covers the full HTTP response, including streams."""
    def __init__(self, app, registry):
        self.app, self.registry = app, registry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        with self.registry.request_scope():
            await self.app(scope, receive, send)

