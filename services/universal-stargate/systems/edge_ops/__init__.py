"""Stargate edge-op substrate: resources an edge calls, never calls out to.

Deliberately not ``systems/federation/edge``, which is the unrelated
federation edge *mode*. This package owns the generic edge-op lease substrate
behind ``/api/v1/edge/*``, gated off by default on ``EDGE_OPS_ENABLED``.
"""

from __future__ import annotations

from typing import Any

__all__ = ["EdgeOpsJournal", "edge_ops_router"]


def __getattr__(name: str) -> Any:
    if name == "edge_ops_router":
        from .routes import router as edge_ops_router

        return edge_ops_router
    if name == "EdgeOpsJournal":
        from .store import EdgeOpsJournal

        return EdgeOpsJournal
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
