import pytest

from services.rag.extraction_admission import (
    _FAILURE_RATIO_THRESHOLD,
    _TIMEOUT_BURST_THRESHOLD,
    ExtractionAdmissionGate,
)

_PIPELINE = "rag-extract-knowledge"


async def _is_open(gate: ExtractionAdmissionGate) -> bool:
    return await gate.wait_for_admission(timeout=0.001)


@pytest.mark.asyncio
async def test_default_open() -> None:
    gate = ExtractionAdmissionGate(pipeline_id=_PIPELINE)
    assert await _is_open(gate)


@pytest.mark.asyncio
async def test_timeout_burst_closes_then_clean_step_opens() -> None:
    gate = ExtractionAdmissionGate(pipeline_id=_PIPELINE)
    for _ in range(_TIMEOUT_BURST_THRESHOLD):
        gate._apply_signal(
            "pipeline.map.iteration.failed",
            {"pipeline_id": _PIPELINE, "failure_type": "timeout"},
        )
    assert not await _is_open(gate)

    gate._apply_signal(
        "pipeline.map.completed",
        {
            "pipeline_id": _PIPELINE,
            "failed_count": 0,
            "total_count": 8,
        },
    )
    assert await _is_open(gate)


@pytest.mark.asyncio
async def test_failure_ratio_closes_gate() -> None:
    gate = ExtractionAdmissionGate(pipeline_id=_PIPELINE)
    failed = max(1, int(10 * _FAILURE_RATIO_THRESHOLD))
    gate._apply_signal(
        "pipeline.map.completed",
        {
            "pipeline_id": _PIPELINE,
            "failed_count": failed,
            "total_count": 10,
        },
    )
    assert not await _is_open(gate)


@pytest.mark.asyncio
async def test_other_pipeline_ignored() -> None:
    gate = ExtractionAdmissionGate(pipeline_id=_PIPELINE)
    for _ in range(_TIMEOUT_BURST_THRESHOLD):
        gate._apply_signal(
            "pipeline.map.iteration.failed",
            {"pipeline_id": "rag-other", "failure_type": "timeout"},
        )
    assert await _is_open(gate)


@pytest.mark.asyncio
async def test_gateway_degraded_recovers() -> None:
    gate = ExtractionAdmissionGate(pipeline_id=_PIPELINE)
    gate._apply_signal(
        "federation.gateway.degraded", {"gateway_id": "edge-jupiter-gateway"}
    )
    assert not await _is_open(gate)
    gate._apply_signal(
        "federation.gateway.recovered", {"gateway_id": "edge-jupiter-gateway"}
    )
    assert await _is_open(gate)


@pytest.mark.asyncio
async def test_model_loading_only_for_observed_models() -> None:
    gate = ExtractionAdmissionGate(pipeline_id=_PIPELINE)
    # Ignored: model not yet observed in our pipeline.
    gate._apply_signal("model.loading.started", {"model_id": "qwen3-5-9b-q8-0-131072"})
    assert await _is_open(gate)

    gate._apply_signal(
        "pipeline.map.iteration.started",
        {
            "pipeline_id": _PIPELINE,
            "model_id": "qwen3-5-9b-q8-0-131072",
        },
    )
    gate._apply_signal("model.loading.started", {"model_id": "qwen3-5-9b-q8-0-131072"})
    assert not await _is_open(gate)

    gate._apply_signal("model.loaded", {"model_id": "qwen3-5-9b-q8-0-131072"})
    assert await _is_open(gate)
