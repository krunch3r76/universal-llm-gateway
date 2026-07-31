"""G4: every GIW /api/v1 route must map to a Stargate proxy path MCP callers use."""

from __future__ import annotations

from fastapi import FastAPI

from services.git_integration_worker.app import create_app

# Docs/meta only — not MCP/cursor caller surfaces.
_GIW_OPENAPI_EXCLUDE = frozenset(
    {
        "/api/v1/git/openapi.json",
        "/api/v1/git/docs",
    }
)


def _giw_api_v1_paths(app) -> set[str]:
    return {
        path
        for path in app.openapi()["paths"].keys()
        if path.startswith("/api/v1") and path not in _GIW_OPENAPI_EXCLUDE
    }


def _stargate_api_v1_paths() -> set[str]:
    from systems.proxy.routers.api import router as api_v1_router

    app = FastAPI()
    app.include_router(api_v1_router)
    return set(app.openapi()["paths"].keys())


def _stargate_covers_giw_path(giw_path: str, stargate_paths: set[str]) -> bool:
    if giw_path.startswith("/api/v1/git/"):
        return "/api/v1/git/{path}" in stargate_paths
    if giw_path == "/api/v1/triggers":
        return "/api/v1/triggers" in stargate_paths
    if giw_path.startswith("/api/v1/triggers/"):
        return "/api/v1/triggers/{path}" in stargate_paths
    if giw_path == "/api/v1/cursor/dispatch":
        return "/api/v1/providers/cursor/dispatch" in stargate_paths
    if giw_path == "/api/v1/cursor/catalog":
        return "/api/v1/providers/cursor/catalog" in stargate_paths
    return False


def test_giw_api_v1_routes_have_stargate_proxy_coverage(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    monkeypatch.setenv("PROJECT_ASK_URL", "http://127.0.0.1:8770")

    giw_paths = _giw_api_v1_paths(create_app())
    stargate_paths = _stargate_api_v1_paths()

    uncovered = sorted(
        path for path in giw_paths if not _stargate_covers_giw_path(path, stargate_paths)
    )
    assert uncovered == [], (
        "GIW /api/v1 routes missing Stargate proxy coverage "
        f"(host-reachable but Stargate-404 class): {uncovered}"
    )
