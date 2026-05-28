"""Tests for event loop blocking in async FastAPI handlers with sync DB calls.

When a FastAPI handler is declared ``async def`` but calls synchronous
SQLAlchemy operations, the sync call blocks the event loop.  This prevents
other requests from being processed concurrently — a management API probe
sent while a slow DB query is running must wait until that query finishes.

The fix: change ``async def`` to ``def`` for handlers that call sync DB code,
so FastAPI runs them in a thread pool instead of on the event loop.

Per the FastAPI docs: "If you are using a third party library that doesn't
have support for using await, then declare your path operation functions as
normally, with just def."
"""

from __future__ import annotations

import inspect
import logging

log = logging.getLogger(__name__)


def test_no_async_handlers_in_empire_api(client):
    """No route handler in the Empire API should be ``async def``.

    Empire uses synchronous SQLAlchemy (``Session``, not ``AsyncSession``).
    Declaring handlers ``async def`` causes synchronous DB calls to block
    the uvicorn event loop, starving all concurrent requests.

    FastAPI automatically dispatches ``def`` handlers to a thread pool,
    keeping the event loop free.  Handlers that previously used ``await``
    for ``asyncio.to_thread`` should be refactored to use ``def`` and call
    the blocking code directly — FastAPI's thread pool provides the same
    isolation.
    """
    app = client.app

    async_handlers = []
    for route in app.routes:
        if not hasattr(route, "endpoint"):
            continue
        if not inspect.iscoroutinefunction(route.endpoint):
            continue
        path = getattr(route, "path", "")
        # Only check Empire's own API routes, not FastAPI builtins
        if not path.startswith(("/api/", "/token")):
            continue
        methods = getattr(route, "methods", {"?"})
        async_handlers.append(
            (route.endpoint.__name__, path, ",".join(sorted(methods)))
        )

    assert not async_handlers, (
        f"Found {len(async_handlers)} async def handlers that block the "
        f"event loop with synchronous DB calls.  Convert to plain def:\n"
        + "\n".join(
            f"  {method:8s} {path:50s} {name}()"
            for name, path, method in sorted(async_handlers, key=lambda x: x[1])
        )
    )
