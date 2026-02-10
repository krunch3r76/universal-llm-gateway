"""
Jobs API Router - Background task execution with SSE streaming.

Endpoints:
    POST   /api/v1/jobs           - Create new job
    GET    /api/v1/jobs/{id}      - Get job status
    GET    /api/v1/jobs/{id}/logs - Stream job logs via SSE
    DELETE /api/v1/jobs/{id}      - Cancel job
"""

from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from universal_logging import get_logger

try:
    from ....jobs import (
        JobStatus,
        MeasureJobRequest,
        MeasurementJob,
        get_job_store,
    )
except ImportError:
    from src.jobs import (
        JobStatus,
        MeasureJobRequest,
        MeasurementJob,
        get_job_store,
    )

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/jobs", tags=["Jobs"])


class MeasureJobRequestModel(BaseModel):
    """Request model for measurement job."""

    type: Literal["measure"] = Field(
        ..., description="Job type (currently only 'measure')"
    )
    model_id: str = Field(..., description="Model ID to measure")
    contexts: list[int] | None = Field(
        default=None,
        description="Context lengths to measure. None = auto-detect",
    )
    mode: Literal["gpu", "cpu", "auto"] = Field(
        default="auto",
        description="Measurement mode: gpu, cpu, or auto (try gpu first)",
    )
    n_batch: int = Field(
        default=512,
        description="Batch size for measurement",
    )
    gpu_index: int = Field(
        default=0,
        description="GPU index to use for measurement",
    )
    vram_cap_mb: int | None = Field(
        default=None,
        description="Max VRAM in MB for 'fits' determination (simulate smaller GPU)",
    )
    ram_cap_mb: int | None = Field(
        default=None,
        description="Max RAM in MB for 'fits' determination (simulate smaller system)",
    )
    enable_hybrid: bool = Field(
        default=True,
        description="Try partial GPU offload when full fails (binary search)",
    )
    mmproj_path: str | None = Field(
        default=None,
        description="Path to mmproj/CLIP file for vision models (e.g., mmproj-F16.gguf)",
    )
    use_static_catalog: bool = Field(
        default=False,
        description="Update static catalog instead of local catalog",
    )
    safety_margin: int | None = Field(
        default=None,
        description="Safety margin for hybrid mode (subtract N layers from max found). Default: 2. Set to 0 for no margin.",
    )


class JobResponse(BaseModel):
    """Response model for job endpoints."""

    job_id: str
    status: str
    stream_url: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


class JobListResponse(BaseModel):
    """Response model for job list."""

    jobs: list[JobResponse]
    count: int


@router.post("", response_model=JobResponse)
async def create_job(
    request: MeasureJobRequestModel, http_request: Request
) -> JobResponse:
    """
    Start a new background job.

    Currently supports measurement jobs that profile VRAM/RAM usage
    for different context sizes.

    Returns job ID and SSE stream URL for real-time progress.
    """
    if request.type != "measure":
        raise HTTPException(
            status_code=400,
            detail=f"Unknown job type: {request.type}. Supported: measure",
        )

    # Generate job ID
    job_id = f"job-{uuid4().hex[:8]}"

    gateway_config = getattr(http_request.app.state, "gateway_config", None)
    if gateway_config is None:
        raise HTTPException(
            status_code=500, detail="Gateway configuration not initialized"
        )

    # Create measurement job
    job_request = MeasureJobRequest(
        model_id=request.model_id,
        contexts=request.contexts,
        mode=request.mode,
        n_batch=request.n_batch,
        gpu_index=request.gpu_index,
        vram_cap_mb=request.vram_cap_mb,
        ram_cap_mb=request.ram_cap_mb,
        enable_hybrid=request.enable_hybrid,
        safety_margin=request.safety_margin,
        mmproj_path=request.mmproj_path,
        use_static_catalog=request.use_static_catalog,
    )

    job = MeasurementJob(
        job_id=job_id,
        request=job_request,
        gateway_config=gateway_config,
    )

    # Store and start job
    store = get_job_store()
    await store.add(job)
    await job.start()

    logger.info(f"Created measurement job {job_id} for {request.model_id}")

    return JobResponse(
        job_id=job_id,
        status=job.status.value,
        stream_url=f"/api/v1/jobs/{job_id}/logs",
        created_at=job.created_at.isoformat() if job.created_at else None,
    )


@router.get("", response_model=JobListResponse)
async def list_jobs() -> JobListResponse:
    """
    List all jobs.

    Returns all jobs including completed ones.
    """
    store = get_job_store()
    jobs = await store.list_all()

    return JobListResponse(
        jobs=[
            JobResponse(
                job_id=job.job_id,
                status=job.status.value,
                stream_url=f"/api/v1/jobs/{job.job_id}/logs"
                if job.status in (JobStatus.PENDING, JobStatus.RUNNING)
                else None,
                result=job.result,
                error=job.error,
                created_at=job.created_at.isoformat() if job.created_at else None,
                completed_at=job.completed_at.isoformat() if job.completed_at else None,
            )
            for job in jobs
        ],
        count=len(jobs),
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    """
    Get job status and result.

    Returns current status, and result/error if completed.
    """
    store = get_job_store()
    job = await store.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    return JobResponse(
        job_id=job.job_id,
        status=job.status.value,
        stream_url=f"/api/v1/jobs/{job_id}/logs"
        if job.status in (JobStatus.PENDING, JobStatus.RUNNING)
        else None,
        result=job.result,
        error=job.error,
        created_at=job.created_at.isoformat() if job.created_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )


@router.get("/{job_id}/logs")
async def stream_logs(job_id: str) -> StreamingResponse:
    """
    Stream job logs via Server-Sent Events (SSE).

    Returns real-time log messages as SSE events.
    Keep-alive messages sent every second during execution.
    Final message indicates job completion status.

    Example usage:
        curl -N http://localhost:9998/api/v1/jobs/{job_id}/logs
    """
    store = get_job_store()
    job = await store.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    return StreamingResponse(
        job.stream_logs(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.delete("/{job_id}", response_model=JobResponse)
async def cancel_job(job_id: str) -> JobResponse:
    """
    Cancel a running job.

    Cancels the job if still running. Returns current status.
    """
    store = get_job_store()
    job = await store.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
        await job.cancel()
        logger.info(f"Cancelled job {job_id}")

    return JobResponse(
        job_id=job.job_id,
        status=job.status.value,
        result=job.result,
        error=job.error,
        created_at=job.created_at.isoformat() if job.created_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )


@router.post("/cleanup")
async def cleanup_jobs(max_age_seconds: int = 3600) -> dict[str, Any]:
    """
    Remove completed jobs older than max_age.

    Useful for cleaning up old job records.
    Default: remove jobs completed more than 1 hour ago.
    """
    store = get_job_store()
    removed = await store.cleanup_completed(max_age_seconds)

    return {
        "status": "success",
        "removed": removed,
        "message": f"Removed {removed} completed jobs older than {max_age_seconds}s",
    }
