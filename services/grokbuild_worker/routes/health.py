"""``GET /api/v1/grokbuild/health`` — readiness probe for grokbuild-worker.

Returned payload mirrors the V1 health contract documented in
``tmp/prompts/grokbuild-v2/02-worker-service-shell.md``:

```json
{
  "status": "ok" | "degraded",
  "checks": {
    "grok_binary": "ok" | "missing" | "not_executable",
    "auth_dir":    "ok" | "missing",
    "sidecar_dir": "ok" | "creating_failed",
    "registry":    "ok" | "missing"
  },
  "service": "grokbuild-worker",
  "version": "<git sha or pkg version>"
}
```

Re-evaluates the same checks the lifespan ran at startup so degraded
state is observable without restart.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/grokbuild", tags=["grokbuild-health"])


class _HealthChecks(BaseModel):
    grok_binary: Literal["ok", "missing", "not_executable"]
    auth_dir: Literal["ok", "missing"]
    sidecar_dir: Literal["ok", "creating_failed"]
    registry: Literal["ok", "missing"]
    grok_auth: Literal["ok", "expired", "missing"]


class HealthResponse(BaseModel):
    """Health envelope returned by ``GET /api/v1/grokbuild/health``."""

    status: Literal["ok", "degraded"]
    checks: _HealthChecks
    service: str = "grokbuild-worker"
    version: str


def _check_grok_binary(grok_bin: Path) -> str:
    if not grok_bin.exists():
        return "missing"
    if not os.access(grok_bin, os.X_OK):
        return "not_executable"
    return "ok"


def _check_auth_dir(auth_dir: Path) -> str:
    if not auth_dir.exists():
        return "missing"
    if not os.access(auth_dir, os.R_OK | os.X_OK):
        return "missing"
    return "ok"


def _check_sidecar_dir(sidecar_dir: Path) -> str:
    if not sidecar_dir.exists():
        return "creating_failed"
    if not os.access(sidecar_dir, os.W_OK | os.X_OK):
        return "creating_failed"
    return "ok"


def _check_registry(registry_path: Path) -> str:
    return "ok" if registry_path.parent.exists() else "missing"


def _evaluate(cfg: Any, state: Any | None = None) -> dict[str, str]:
    """Evaluate all health checks.

    ``state`` is the FastAPI app.state (or None at pre-lifespan call sites).
    ``grok_auth`` is read from ``state.grok_auth_status`` rather than
    probing inline — a subprocess probe on every /health poll would add
    up to 60s latency with warmup; the startup/periodic probe in app.py
    updates state instead.
    """
    from grokbuild.auth_probe import AuthStatus

    grok_auth: str = "missing"
    if state is not None:
        raw = getattr(state, "grok_auth_status", None)
        if raw is AuthStatus.OK:
            grok_auth = "ok"
        elif raw is AuthStatus.EXPIRED:
            grok_auth = "expired"
        elif raw is AuthStatus.MISSING:
            grok_auth = "missing"
    return {
        "grok_binary": _check_grok_binary(cfg.grok_bin_path),
        "auth_dir": _check_auth_dir(cfg.grok_auth_dir),
        "sidecar_dir": _check_sidecar_dir(cfg.sidecar_dir),
        "registry": _check_registry(cfg.registry_path),
        "grok_auth": grok_auth,
    }


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=200,
    summary="Readiness probe; 200 even when degraded.",
)
async def health(request: Request) -> JSONResponse:
    """Return health envelope; ``200`` even when degraded.

    Degraded means ≥1 startup check is still failing. ``grok_auth``
    reflects the most recent probe result stored in app.state
    (updated at startup and optionally by the periodic probe task).
    """
    state = request.app.state
    cfg = state.worker_config
    checks = _evaluate(cfg, state)
    # Option A (OQ-1): grok_auth is operator-actionable, not a worker-health
    # fault — it is reported informationally but does NOT drive the degraded
    # rollup. Status is computed over the four core readiness checks only.
    _rollup_keys = ("grok_binary", "auth_dir", "sidecar_dir", "registry")
    status = "ok" if all(checks[k] == "ok" for k in _rollup_keys) else "degraded"
    return JSONResponse(
        status_code=200,
        content={
            "status": status,
            "checks": checks,
            "service": "grokbuild-worker",
            "version": getattr(state, "worker_version", "unknown"),
        },
    )
