import sys
from pathlib import Path
from types import SimpleNamespace

_stargate_root = str(Path(__file__).resolve().parents[7])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    rejection,
)
from systems.routing.selection.decision import (  # noqa: E402
    ConstraintFailure,
    DecisionTrace,
    FeasibilityTier,
    GatewayCandidate,
)
from systems.routing.selection.types import Gateway  # noqa: E402


def _capacity_candidate(
    gateway_name: str,
    gateway_url: str,
    free_vram_mb: int,
) -> GatewayCandidate:
    return GatewayCandidate(
        gateway=Gateway(
            ref=SimpleNamespace(remote_stargate_url=gateway_url),
            name=gateway_name,
            ram_free_mb=64_000,
            vram_free_mb=free_vram_mb,
        ),
        tier=FeasibilityTier.T0_INFEASIBLE,
        constraints_failed=(
            ConstraintFailure(
                constraint="has_enough_vram",
                reason="insufficient VRAM",
                details={"vram_free_hardware": free_vram_mb},
            ),
        ),
    )


def test_capacity_details_prefer_sticky_bound_candidate() -> None:
    jupiter = _capacity_candidate(
        "edge-jupiter-gateway",
        "http://jupiter:9999",
        900,
    )
    localhost = _capacity_candidate(
        "edge-localhost-gateway",
        "unix:///tmp/universal-protocol/edge-localhost.sock",
        32108,
    )
    trace = DecisionTrace(
        model_id="qwen3-5-27b-q8-0-32768",
        original_model_id=None,
        request_id="req-1",
        candidates=(jupiter, localhost),
        selection_reason=(
            "sticky_capacity_wait: bound=edge-localhost-gateway, at_capacity"
        ),
    )

    details = rejection._build_capacity_details(
        "qwen3-5-27b-q8-0-32768",
        trace,
        {"has_enough_vram"},
        [],
    )

    assert details["gateway_id"] == "edge-localhost-gateway"
    assert (
        details["gateway_url"] == "unix:///tmp/universal-protocol/edge-localhost.sock"
    )
    assert details["vram_free_hardware"] == 32108


def test_capacity_details_fall_back_to_first_capacity_candidate() -> None:
    jupiter = _capacity_candidate(
        "edge-jupiter-gateway",
        "http://jupiter:9999",
        900,
    )
    trace = DecisionTrace(
        model_id="qwen3-5-27b-q8-0-32768",
        original_model_id=None,
        request_id="req-1",
        candidates=(jupiter,),
        selection_reason="no_feasible_gateways",
    )

    details = rejection._build_capacity_details(
        "qwen3-5-27b-q8-0-32768",
        trace,
        {"has_enough_vram"},
        [],
    )

    assert details["gateway_id"] == "edge-jupiter-gateway"
    assert details["gateway_url"] == "http://jupiter:9999"
    assert details["vram_free_hardware"] == 900
