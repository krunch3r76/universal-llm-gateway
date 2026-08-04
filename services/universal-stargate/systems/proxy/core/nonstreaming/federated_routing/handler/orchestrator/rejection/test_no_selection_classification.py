"""Classification polarity for permanent vs transient capacity (thread 1236 F2)."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

_stargate_root = Path(__file__).resolve().parents[8]
if str(_stargate_root) not in sys.path:
    sys.path.insert(0, str(_stargate_root))

from model_id import ModelId  # noqa: E402
from universal_protocol import ErrorCode  # noqa: E402

from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.rejection import (  # noqa: E501,E402
    no_selection_outcome as rejection_mod,
)
from systems.routing.selection.decision import (  # noqa: E402
    ConstraintFailure,
    DecisionTrace,
    FeasibilityTier,
    GatewayCandidate,
)
from systems.routing.selection.types import Gateway  # noqa: E402

_constraint_mod_path = Path(__file__).resolve().parents[4] / "constraint_retryable.py"
_spec = importlib.util.spec_from_file_location(
    "constraint_retryable_classification_test", _constraint_mod_path
)
assert _spec and _spec.loader
_constraint_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_constraint_mod)

constraint_failure_is_retryable = _constraint_mod.constraint_failure_is_retryable

_TRANSIENT = frozenset(
    {
        "compute_type_capacity",
        "circuit_breaker",
        "eviction_blocked_by_busy_models",
    }
)
_RESOURCE = frozenset({"has_enough_vram", "has_enough_ram"})

TARGET = ModelId.parse("qwen3-32b-awq-16384")


def _classify(failures: tuple[ConstraintFailure, ...]) -> tuple[bool, bool]:
    candidate = GatewayCandidate(
        gateway=Gateway(
            ref=SimpleNamespace(remote_stargate_url="http://jupiter:9999"),
            name="edge-jupiter-gateway",
            ram_free_mb=64_000,
            vram_free_mb=29_123,
        ),
        tier=FeasibilityTier.T0_INFEASIBLE,
        constraints_failed=failures,
    )

    def _is_transient(candidate) -> bool:
        failed = {failure.constraint for failure in candidate.constraints_failed}
        if failed & _TRANSIENT:
            return True
        if failed & _RESOURCE:
            return "can_fit_with_eviction" not in failed
        return False

    def _is_permanent(candidate) -> bool:
        for failure in candidate.constraints_failed:
            if failure.constraint == "can_fit_with_eviction":
                return not constraint_failure_is_retryable(failure)
        failed = {failure.constraint for failure in candidate.constraints_failed}
        return bool(failed & _RESOURCE) and ("can_fit_with_eviction" in failed)

    return _is_transient(candidate), _is_permanent(candidate)


def _gateway_candidate(
    constraint: str, *, details: dict | None = None
) -> GatewayCandidate:
    return GatewayCandidate(
        gateway=Gateway(
            ref=SimpleNamespace(remote_stargate_url="http://jupiter:9999"),
            name="edge-jupiter-gateway",
            ram_free_mb=64_000,
            vram_free_mb=29_123,
        ),
        tier=FeasibilityTier.T0_INFEASIBLE,
        constraints_failed=(
            ConstraintFailure(
                constraint=constraint,
                reason="test",
                details=details or {"retryable": True},
            ),
        ),
    )


def _trace_for(constraint: str, *, details: dict | None = None) -> DecisionTrace:
    return DecisionTrace(
        model_id="qwen3-32b-awq-16384",
        original_model_id=None,
        request_id="req-classify",
        candidates=(_gateway_candidate(constraint, details=details),),
        selection_tier=FeasibilityTier.T0_INFEASIBLE,
        selection_reason="test",
    )


@pytest.mark.parametrize(
    "constraint",
    [
        "compute_type_capacity",
        "circuit_breaker",
        "eviction_blocked_by_busy_models",
    ],
)
@pytest.mark.asyncio
async def test_transient_constraints_enter_wait_with_transient_capacity_mode(
    monkeypatch: pytest.MonkeyPatch,
    constraint: str,
) -> None:
    wait_mock = AsyncMock(return_value=(None, _trace_for(constraint), 100))
    monkeypatch.setattr(rejection_mod, "_wait_and_retry_selection", wait_mock)
    context = SimpleNamespace(
        request_id="req-wait",
        selected_model=TARGET,
        model_sticky=True,
        excluded_gateway_ids=set(),
        _capacity_deadline_mono=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        await rejection_mod.handle_selection_rejection(
            selected_gateway=None,
            trace=_trace_for(constraint),
            context=context,
            event_bus=None,
            federated_manager=SimpleNamespace(),
            federated_gateways=[],
            routing_config={},
            decision_engine=SimpleNamespace(),
            placement=SimpleNamespace(model_id=TARGET),
            gateways_for_routing=[],
            stability_tracker=SimpleNamespace(),
        )

    wait_mock.assert_awaited_once()
    assert wait_mock.await_args.kwargs["continuation_mode"] == "transient_capacity"
    assert exc_info.value.detail["code"] == ErrorCode.STICKY_CAPACITY


@pytest.mark.asyncio
async def test_permanent_resource_fast_fails_without_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wait_mock = AsyncMock()
    monkeypatch.setattr(rejection_mod, "_wait_and_retry_selection", wait_mock)
    trace = DecisionTrace(
        model_id="qwen3-32b-awq-16384",
        original_model_id=None,
        request_id="req-perm",
        candidates=(
            GatewayCandidate(
                gateway=Gateway(
                    ref=SimpleNamespace(remote_stargate_url="http://jupiter:9999"),
                    name="edge-jupiter-gateway",
                    ram_free_mb=64_000,
                    vram_free_mb=29_123,
                ),
                tier=FeasibilityTier.T0_INFEASIBLE,
                constraints_failed=(
                    ConstraintFailure(
                        constraint="has_enough_vram",
                        reason="short",
                        details={},
                    ),
                    ConstraintFailure(
                        constraint="can_fit_with_eviction",
                        reason="no reclaimable VRAM",
                        details={"retryable": False},
                    ),
                ),
            ),
        ),
        selection_tier=FeasibilityTier.T0_INFEASIBLE,
        selection_reason="test",
    )
    model = TARGET
    context = SimpleNamespace(
        request_id="req-perm",
        selected_model=model,
        model_sticky=True,
        excluded_gateway_ids=set(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await rejection_mod.handle_selection_rejection(
            selected_gateway=None,
            trace=trace,
            context=context,
            event_bus=None,
            federated_manager=SimpleNamespace(),
            federated_gateways=[],
            routing_config={},
            decision_engine=SimpleNamespace(),
            placement=SimpleNamespace(model_id=model),
            gateways_for_routing=[],
            stability_tracker=SimpleNamespace(),
        )

    wait_mock.assert_not_awaited()
    assert exc_info.value.detail["code"] == ErrorCode.INSUFFICIENT_VRAM


def test_permanent_geometry_not_transient() -> None:
    failures = (
        ConstraintFailure(
            constraint="has_enough_vram",
            reason="short",
            details={"vram_free_hardware": 29123},
        ),
        ConstraintFailure(
            constraint="can_fit_with_eviction",
            reason="no reclaimable VRAM",
            details={"retryable": False, "loaded_count": 0},
        ),
    )
    transient, permanent = _classify(failures)
    assert transient is False
    assert permanent is True


def test_busy_block_geometry_is_transient_not_permanent() -> None:
    failures = (
        ConstraintFailure(
            constraint="has_enough_vram",
            reason="short",
            details={},
        ),
        ConstraintFailure(
            constraint="eviction_blocked_by_busy_models",
            reason="busy",
            details={"retryable": True},
        ),
    )
    transient, permanent = _classify(failures)
    assert transient is True
    assert permanent is False
