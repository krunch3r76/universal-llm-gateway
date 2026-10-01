"""Shared MCP→worker HTTP base URL + retry budget constants."""

from __future__ import annotations

import os

_DEFAULT_WORKER_URL = "http://127.0.0.1:8091"
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_ATTEMPT_TIMEOUTS_S = (3.0, 5.0, 5.0)
_DEFAULT_BACKOFF_S = (0.5, 1.5)
_DEFAULT_TOTAL_BUDGET_S = 15.0


def _worker_base_url() -> str:
    """Resolve the worker base URL for mcp→host reachability.

    Prefer ``GIT_INTEGRATION_WORKER_URL`` when set. Otherwise, from the mcp
    container, use ``STARGATE_URL`` so host-side ``/api/v1/git/*`` calls ride the
    existing proxy (worker binds 127.0.0.1 — unreachable via host.docker.internal).
    Host-local callers fall back to loopback.
    """
    explicit = os.environ.get("GIT_INTEGRATION_WORKER_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    stargate = os.environ.get("STARGATE_URL", "").strip()
    if stargate:
        return stargate.rstrip("/")
    return _DEFAULT_WORKER_URL


__all__ = [
    "_DEFAULT_ATTEMPT_TIMEOUTS_S",
    "_DEFAULT_BACKOFF_S",
    "_DEFAULT_MAX_ATTEMPTS",
    "_DEFAULT_TOTAL_BUDGET_S",
    "_DEFAULT_WORKER_URL",
    "_worker_base_url",
]
