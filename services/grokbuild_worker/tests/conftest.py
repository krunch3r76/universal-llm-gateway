"""Pytest configuration for services/grokbuild_worker/tests.

Ensures ``mcp_events`` is available as a no-op stub before any grokbuild
lib imports so the tests work without the mcp-server on sys.path.
This mirrors the try/except fallback in ``grokbuild.events_core`` but
guards against test isolation issues where a prior test loaded the real
module from a different path.
"""

from __future__ import annotations

import sys
import types


def _ensure_mcp_events_stub() -> None:
    """Inject a no-op ``mcp_events`` into sys.modules if not already present."""
    if "mcp_events" in sys.modules:
        return
    stub = types.ModuleType("mcp_events")

    def record(signal: str, **payload: object) -> None:  # noqa: ARG001
        pass

    stub.record = record  # type: ignore[attr-defined]
    sys.modules["mcp_events"] = stub


_ensure_mcp_events_stub()
