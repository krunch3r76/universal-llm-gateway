"""``GET /health`` — readiness probe for git-integration-worker."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["git-integration-health"])


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    """Return readiness plus toolchain PATH plane for spawn-vs-effective probes."""
    version = getattr(request.app.state, "worker_version", "unknown")
    path_report = getattr(request.app.state, "toolchain_path", None)
    content: dict[str, object] = {
        "status": "ok",
        "service": "git-integration-worker",
        "version": version,
    }
    if path_report is not None:
        content["path_spawn_first"] = path_report.spawn_first
        content["path_first"] = path_report.effective_first
        content["path_corrected"] = path_report.corrected
    return content
