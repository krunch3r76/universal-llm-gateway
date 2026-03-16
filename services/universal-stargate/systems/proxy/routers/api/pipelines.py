"""Pipeline registry metadata endpoint.

Exposes pipeline metadata (steps, models, timeout, domain) from the
PipelineRegistry for MCP tooling (validate_pipeline, pipeline_run
timeout auto-detection).
"""

from __future__ import annotations

from typing import TypedDict

from fastapi import APIRouter, Depends, HTTPException
from universal_logging import get_logger

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

logger = get_logger(__name__)
router = APIRouter(tags=["pipelines"])

class PipelineSummary(TypedDict):
    steps: int
    models: list[str]
    domain: str
    version: str
    timeout_seconds: float


PipelinesResponse = dict[str, dict[str, PipelineSummary]]


def _require_pipeline_registry(proxy: StargateProxy):
    """Return the pipeline registry or raise when unavailable."""
    registry = proxy.pipeline_registry
    if registry is None:
        raise HTTPException(status_code=503, detail="Pipeline registry unavailable")
    return registry


def _pipeline_summary(pipeline_id: str, proxy: StargateProxy) -> PipelineSummary:
    """Build metadata summary for a single registered pipeline."""
    registry = _require_pipeline_registry(proxy)
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
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> PipelinesResponse:
    """Return metadata for all registered pipelines."""
    registry = _require_pipeline_registry(proxy)

    pipelines = {pid: _pipeline_summary(pid, proxy) for pid in registry.pipelines}
    return {"pipelines": pipelines}
