"""One runtime per branch, with request leases protecting in-use instances."""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

_request_leases: ContextVar[object | None] = ContextVar("branch_request_leases", default=None)


class StoryGateRegistry:
    """Task-owned reentrant story gates, shared by publication and mutation."""
    def __init__(self):
        self._gates = {}

    def gate(self, story_id):
        return self._gates.setdefault(story_id, _StoryGate())


class _StoryGate:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.owner = None
        self.depth = 0

    async def __aenter__(self):
        task = asyncio.current_task()
        if self.owner is task:
            self.depth += 1
            return self
        await self.lock.acquire()
        self.owner, self.depth = task, 1
        return self

    async def __aexit__(self, *_):
        if self.owner is not asyncio.current_task():
            raise RuntimeError('Only the story gate owner may release it')
        self.depth -= 1
        if not self.depth:
            self.owner = None
            self.lock.release()


class BranchRuntimeRegistry:
    def __init__(self):
        self._gates = {}
        self._leases = Counter()

    def gate(self, branch_id):
        return self._gates.setdefault(branch_id, asyncio.Lock())

    def pin(self, branch_id):
        scope = _request_leases.get()
        if scope is not None and scope.active:
            key = (self, branch_id)
            if key not in scope.leases:
                scope.leases[key] = True
                self._leases[branch_id] += 1

    def leased(self, branch_id):
        return self._leases[branch_id] > 0

    @contextmanager
    def request_scope(self):
        scope = SimpleNamespace(leases={}, active=True)
        token = _request_leases.set(scope)
        try:
            yield
        finally:
            _request_leases.reset(token)
            scope.active = False
            for registry, branch_id in scope.leases:
                registry._leases[branch_id] -= 1
                if not registry._leases[branch_id]:
                    del registry._leases[branch_id]

