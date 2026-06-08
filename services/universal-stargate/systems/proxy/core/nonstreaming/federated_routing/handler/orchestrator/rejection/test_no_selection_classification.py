"""Classification polarity for permanent vs transient capacity (thread 1236 F2)."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

_stargate_root = Path(__file__).resolve().parents[8]
if str(_stargate_root) not in sys.path:
    sys.path.insert(0, str(_stargate_root))

_constraint_mod_path = Path(__file__).resolve().parents[4] / "constraint_retryable.py"
_spec = importlib.util.spec_from_file_location(
    "constraint_retryable_classification_test", _constraint_mod_path
)
assert _spec and _spec.loader
_constraint_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_constraint_mod)

constraint_failure_is_retryable = _constraint_mod.constraint_failure_is_retryable

from systems.routing.selection.decision import (  # noqa: E402
    ConstraintFailure,
    FeasibilityTier,
    GatewayCandidate,
)
from systems.routing.selection.types import Gateway  # noqa: E402

_TRANSIENT = frozenset(
    {
        "compute_type_capacity",
        "circuit_breaker",
        "eviction_blocked_by_busy_models",
    }
)
_RESOURCE = frozenset({"has_enough_vram", "has_enough_ram"})


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
