"""Stargate passthrough auth for extraction queue reset."""

from __future__ import annotations

from fastapi.routing import APIRoute

from systems.proxy.dependencies import get_auth_dependency
from systems.proxy.routers.api import rag_articles


def _auth_dependency_callables(route: APIRoute) -> list[object]:
    return [dep.call for dep in route.dependant.dependencies]


def test_extraction_queue_reset_proxy_requires_auth() -> None:
    """Reset proxy uses the same auth dependency as extraction queue GET."""
    queue_get: APIRoute | None = None
    queue_reset: APIRoute | None = None
    for route in rag_articles.router.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path == "/rag/extraction/queue" and "GET" in route.methods:
            queue_get = route
        if route.path == "/rag/extraction/queue/reset" and "POST" in route.methods:
            queue_reset = route
    assert queue_get is not None
    assert queue_reset is not None
    assert get_auth_dependency in _auth_dependency_callables(queue_get)
    assert get_auth_dependency in _auth_dependency_callables(queue_reset)
