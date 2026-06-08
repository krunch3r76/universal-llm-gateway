"""Tests for queue-gate polarity (thread 1236 F1–F3)."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

_stargate_root = Path(__file__).resolve().parents[4]
if str(_stargate_root) not in sys.path:
    sys.path.insert(0, str(_stargate_root))

_constraint_mod_path = Path(__file__).resolve().parent / "constraint_retryable.py"
_spec = importlib.util.spec_from_file_location(
    "constraint_retryable_under_test", _constraint_mod_path
)
assert _spec and _spec.loader
_constraint_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_constraint_mod)

constraint_failure_is_retryable = _constraint_mod.constraint_failure_is_retryable
extract_retryable_constraint = _constraint_mod.extract_retryable_constraint

from systems.routing.selection.decision import (  # noqa: E402
    ConstraintFailure,
    DecisionTrace,
    FeasibilityTier,
    GatewayCandidate,
)
from systems.routing.selection.types import Gateway  # noqa: E402


def _trace_with_failures(
    *failures: ConstraintFailure,
) -> DecisionTrace:
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
    return DecisionTrace(
        model_id="hermes-3-llama-3-1-70b-uncensored-q4-k-m-16384-hybrid",
        original_model_id=None,
        request_id="req-107a17f9",
        candidates=(candidate,),
        selection_reason="no_feasible_gateways",
    )


def test_extract_retryable_none_for_permanent_vram_geometry() -> None:
    trace = _trace_with_failures(
        ConstraintFailure(
            constraint="has_enough_vram",
            reason="insufficient VRAM",
            details={"vram_free_hardware": 29123, "vram_needed": 30993},
        ),
        ConstraintFailure(
            constraint="can_fit_with_eviction",
            reason="insufficient reclaimable resources",
            details={"retryable": False, "loaded_count": 0},
        ),
    )
    assert extract_retryable_constraint(trace) is None


def test_extract_retryable_returns_busy_block_constraint() -> None:
    trace = _trace_with_failures(
        ConstraintFailure(
            constraint="has_enough_vram",
            reason="insufficient VRAM",
            details={"vram_free_hardware": 5000},
        ),
        ConstraintFailure(
            constraint="eviction_blocked_by_busy_models",
            reason="busy models block eviction",
            details={"retryable": True, "loaded_count": 2, "busy_count": 2},
        ),
    )
    assert extract_retryable_constraint(trace) == "eviction_blocked_by_busy_models"


def test_constraint_failure_is_retryable_uses_details_stamp() -> None:
    permanent = ConstraintFailure(
        constraint="can_fit_with_eviction",
        reason="permanent",
        details={"retryable": False},
    )
    transient = ConstraintFailure(
        constraint="eviction_blocked_by_busy_models",
        reason="transient",
        details={"retryable": True},
    )
    assert constraint_failure_is_retryable(permanent) is False
    assert constraint_failure_is_retryable(transient) is True


def test_constraint_failure_fallback_without_details_stamp() -> None:
    assert (
        constraint_failure_is_retryable(
            ConstraintFailure(constraint="can_fit_with_eviction", reason="x")
        )
        is False
    )
    assert (
        constraint_failure_is_retryable(
            ConstraintFailure(constraint="eviction_blocked_by_busy_models", reason="x")
        )
        is True
    )
