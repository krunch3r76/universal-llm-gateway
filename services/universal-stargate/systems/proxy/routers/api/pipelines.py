"""Pipeline registry metadata endpoint.

Exposes pipeline metadata (steps, models, timeout, domain) from the
PipelineRegistry for MCP tooling (validate_pipeline, pipeline_run
timeout auto-detection).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from universal_logging import get_logger

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

logger = get_logger(__name__)
router = APIRouter(tags=["pipelines"])


def _pipeline_summary(pipeline_id: str, proxy: StargateProxy) -> dict:
    """Build metadata summary for a single pipeline."""
    registry = proxy.pipeline_registry
    assert registry is not None
    spec = registry.pipelines[pipeline_id]
    return {
        "steps": len(spec.steps),
        "models": sorted(
            {
                s.model_ref
                for s in spec.steps
                if s.model_ref and s.model_ref != pipeline_id
            }
        ),
        "domain": spec.domain,
        "version": spec.version,
        "timeout_seconds": spec.options.timeout_seconds,
    }


@router.get("/pipelines")
async def list_pipelines(
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict = Depends(get_auth_dependency),
) -> dict:
    """Return metadata for all registered pipelines."""
    del current_user
    if proxy.pipeline_registry is None:
        raise HTTPException(status_code=503, detail="Pipeline registry unavailable")

    pipelines = {
        pid: _pipeline_summary(pid, proxy) for pid in proxy.pipeline_registry.pipelines
    }
    return {"pipelines": pipelines}
