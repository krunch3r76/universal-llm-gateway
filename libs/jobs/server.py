"""FastAPI app for the jobs satellite.

``create_app(registry=None)`` loads the production registry. Tests pass a
registry and may inject ``jobs_today``. Startup reconcile appends ``lost``
and redelivers thread runs before the app accepts requests. OpenAPI is
served at ``/api/v1/jobs/openapi.json``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openapi_mcp.binding import x_mcp

from jobs.delivery import Delivery
from jobs.errors import JobsError
from jobs.journal import Journal
from jobs.routes import router
from jobs.runner import Runner
from jobs.spec import JobSpec, load_registry

_STAMPS = {
    "catalog": ("", "get", True),
    "spec": ("/{job}", "get", True),
    "create_run": ("/{job}", "post", False),
    "status": ("/{job}/runs/{run_id}", "get", True),
    "log": ("/{job}/runs/{run_id}/log", "get", True),
    "cancel": ("/{job}/runs/{run_id}", "delete", False),
}


def create_app(
    registry: Sequence[JobSpec] | None = None,
    *,
    today: Callable[[], date] | None = None,
    client_factory: Any | None = None,
) -> FastAPI:
    """Build the jobs app. ``registry=None`` selects the four production jobs.

    The journal opens ``JOBS_STATE_DIR`` immediately. Reconcile runs in the
    lifespan, before requests are served. ``today`` freezes the sunset clock
    in tests.
    """
    specs = load_registry(registry)
    by_name = {spec.name: spec for spec in specs}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        app.state.jobs_journal.reconcile_on_start()
        await app.state.jobs_delivery.recover()
        yield

    app = FastAPI(title="jobs", lifespan=lifespan, openapi_url="/api/v1/jobs/openapi.json")
    journal = Journal()
    delivery = Delivery(journal, client_factory=client_factory)
    runner = Runner(journal, delivery, by_name)
    app.state.jobs_journal = journal
    app.state.jobs_delivery = delivery
    app.state.jobs_runner = runner
    app.state.jobs_specs = by_name
    app.state.jobs_today = today
    _stamp(router)
    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness for the manage UDS probe. Not an MCP operation."""
        return {"status": "ok"}

    @app.exception_handler(JobsError)
    async def jobs_error(_request: Request, exc: JobsError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=exc.status_code)

    return app


def _stamp(api: Any) -> None:
    """Attach x-mcp stamps. Paths are relative to the router prefix."""
    for route in api.routes:
        op_id = getattr(route, "operation_id", None) or ""
        key = {
            "jobs_catalog": "catalog",
            "jobs_spec": "spec",
            "jobs_create_run": "create_run",
            "jobs_run_status": "status",
            "jobs_run_log": "log",
            "jobs_run_cancel": "cancel",
        }.get(op_id)
        if key is None:
            continue
        _path, _method, readonly = _STAMPS[key]
        route.openapi_extra = x_mcp(key, tool="jobs", readonly=readonly, pipeline=True)


_asgi: FastAPI | None = None


async def app(scope: Any, receive: Any, send: Any) -> None:
    """ASGI entry ``jobs.server:app``. Built on first request, not at import."""
    global _asgi
    if _asgi is None:
        _asgi = create_app()
    await _asgi(scope, receive, send)
