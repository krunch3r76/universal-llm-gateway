"""Phase 5 migration tests — dispatch-surface-split Phase 4 cutover.

Covers:
- M1: /api/v1/team/generate route is absent (deleted in Phase 4)
- M2: /api/v1/frontier/generate route is absent (deleted in Phase 4)
- M3: MCP server registers only team_dispatch + frontier_dispatch (not *_generate)

M4 (legacy_field_use telemetry) is NOT applicable: Phase 4 deleted the routes
outright (returning 404) rather than implementing a 422-with-redirect migration
path.  No legacy_field_use signals are emitted.  Stragglers surface as 404s.

M5 (pipeline(op="async", result_delivery=...) escape hatch) is an integration
test requiring a running pipeline.
Manual verification: POST /api/v1/pipelines/dispatch with result_delivery={...};
assert execution_id returned; assert pipeline completes with status="completed";
assert agent-bus thread received ≥1 turn (envelope or agent reply).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from .route import frontier_router, team_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _route_paths(router: Any) -> set[str]:
    """Return the set of route paths registered on a router (without prefix)."""
    return {r.path for r in router.routes}


# ---------------------------------------------------------------------------
# M1 — /team/generate route is absent
# ---------------------------------------------------------------------------


def test_m1_team_generate_route_absent() -> None:
    paths = _route_paths(team_router)
    assert "/generate" not in paths, (
        "/team/generate was re-registered after Phase 4 deletion.  "
        f"Routes present: {paths}"
    )


def test_m1_team_dispatch_route_present() -> None:
    paths = _route_paths(team_router)
    assert any("dispatch" in p for p in paths), (
        "/team/dispatch missing from team_router — Phase 1 registration broken.  "
        f"Routes present: {paths}"
    )


def test_m1_team_generate_returns_404() -> None:
    app = FastAPI()
    app.include_router(team_router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/v1/team/generate",
        json={"role": "orion", "messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.status_code == 404, (
        f"Expected 404 for retired /team/generate, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# M2 — /frontier/generate route is absent
# ---------------------------------------------------------------------------


def test_m2_frontier_generate_route_absent() -> None:
    paths = _route_paths(frontier_router)
    assert "/generate" not in paths, (
        "/frontier/generate was re-registered after Phase 4 deletion.  "
        f"Routes present: {paths}"
    )


def test_m2_frontier_dispatch_route_present() -> None:
    paths = _route_paths(frontier_router)
    assert any("dispatch" in p for p in paths)


def test_m2_frontier_generate_returns_404() -> None:
    app = FastAPI()
    app.include_router(frontier_router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/api/v1/frontier/generate",
        json={
            "model": "openai/gpt-5.4",
            "messages": [{"role": "user", "content": "x"}],
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# M3 — MCP server registers only team_dispatch + frontier_dispatch
# ---------------------------------------------------------------------------
# M3 lives in services/mcp-server/tools/test_frontier_registration.py because
# register_frontier_tools is in a separate service package.  See that file.
#
# Summary: _ToolNameRecorder (duck-typed FastMCP) records names registered by
# register_frontier_tools(); asserts team_dispatch + frontier_dispatch present,
# team_generate + frontier_generate absent.
