import pytest

from services.rag.admission_gate import AdmissionGate

_MODEL = "qwen3-5-9b-q8-0-131072"


async def _is_open(gate: AdmissionGate) -> bool:
    return await gate.wait_for_admission(_MODEL, timeout=0.001)


@pytest.mark.asyncio
async def test_gateway_degradation_closes_gate_until_recovered() -> None:
    gate = AdmissionGate([_MODEL])

    assert await _is_open(gate)

    gate._apply_signal(
        "federation.gateway.degraded",
        {"gateway_id": "edge-jupiter-gateway"},
    )
    assert not await _is_open(gate)

    gate._apply_signal("model.loaded", {"model_id": _MODEL})
    assert not await _is_open(gate)

    gate._apply_signal(
        "federation.gateway.recovered",
        {"gateway_id": "edge-jupiter-gateway"},
    )
    assert await _is_open(gate)


@pytest.mark.asyncio
async def test_gateway_recovery_does_not_clear_model_loading_pause() -> None:
    gate = AdmissionGate([_MODEL])

    gate._apply_signal("model.loading.started", {"model_id": _MODEL})
    gate._apply_signal(
        "federation.gateway.degraded",
        {"gateway_id": "edge-jupiter-gateway"},
    )
    assert not await _is_open(gate)

    gate._apply_signal(
        "federation.gateway.recovered",
        {"gateway_id": "edge-jupiter-gateway"},
    )
    assert not await _is_open(gate)

    gate._apply_signal("model.loaded", {"model_id": _MODEL})
    assert await _is_open(gate)
