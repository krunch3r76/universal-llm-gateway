import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

_stargate_root = str(Path(__file__).resolve().parents[7])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    rejection,
)
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.rejection.capacity_details import (  # noqa: E402,E501
    build_sticky_capacity_failure_summary,
    format_sticky_capacity_message,
)
from systems.proxy.core.nonstreaming.selection_errors import (  # noqa: E402
    raise_capacity_error,
)
from systems.routing.selection.decision import (  # noqa: E402
    ConstraintFailure,
    DecisionTrace,
    EvictionPlanSummary,
    FeasibilityTier,
    GatewayCandidate,
)
from systems.routing.selection.types import Gateway  # noqa: E402

_REQUIRED_DATA_KEYS = frozenset(
    {
        "gateway_id",
        "holders",
        "eviction",
        "wait_budget_s",
        "waited_ms",
        "budget_exhausted",
        "retry_guidance",
    }
)


def _capacity_candidate(
    gateway_name: str,
    gateway_url: str,
    free_vram_mb: int,
    *,
    loaded: frozenset[ModelId] | None = None,
    busy: frozenset[ModelId] | None = None,
) -> GatewayCandidate:
    return GatewayCandidate(
        gateway=Gateway(
            ref=SimpleNamespace(remote_stargate_url=gateway_url),
            name=gateway_name,
            ram_free_mb=64_000,
            vram_free_mb=free_vram_mb,
            loaded_models=loaded or frozenset(),
            busy_models=busy or frozenset(),
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


def test_build_summary_includes_required_envelope_keys() -> None:
    holder = ModelId.parse("gemma-3-27b-q4-0-8192")
    target = ModelId.parse("qwen3-32b-awq-16384")
    candidate = GatewayCandidate(
        gateway=Gateway(
            ref=SimpleNamespace(remote_stargate_url="http://jupiter"),
            name="edge-jupiter-gateway",
            ram_free_mb=64_000,
            vram_free_mb=1_000,
            loaded_models=frozenset({holder}),
            busy_models=frozenset({holder}),
        ),
        tier=FeasibilityTier.T0_INFEASIBLE,
        constraints_failed=(
            ConstraintFailure(
                constraint="eviction_blocked_by_busy_models",
                reason="busy",
                details={"retryable": True},
            ),
        ),
        eviction_plan=EvictionPlanSummary(
            models_to_evict=frozenset({holder}),
            freed_vram_mb=12_000,
            freed_ram_mb=0,
            estimated_cost=-1.0,
        ),
    )
    trace = DecisionTrace(
        model_id=target.routing_key,
        original_model_id=None,
        request_id="req-env",
        candidates=(candidate,),
        selection_reason="sticky_capacity_wait: bound=edge-jupiter-gateway, at_capacity",
    )
    context = SimpleNamespace(_eviction_wait_deadline_mono=None)

    data = build_sticky_capacity_failure_summary(
        model=target,
        trace=trace,
        routing_config={"eviction_wait_timeout_s": 120.0},
        context=context,
        waited_ms=50_000,
    )

    assert _REQUIRED_DATA_KEYS <= data.keys()
    assert data["eviction"]["disposition"] == "attempted"
    assert data["holders"]["busy"] == [holder.routing_key]
    assert data["wait_budget_s"] == 120.0


def test_build_summary_holders_unknown_without_trace() -> None:
    target = ModelId.parse("qwen3-32b-awq-16384")
    data = build_sticky_capacity_failure_summary(
        model=target,
        trace=None,
        routing_config=None,
        waited_ms=0,
        requeue_depth=3,
        requeue_max_depth=3,
        budget_exhausted=False,
    )

    assert data["holders"]["holders_unknown"] is True
    assert data["holders"]["holders_unknown_reason"] == "no_trace"
    assert data["requeue_depth"] == 3
    assert data["requeue_max_depth"] == 3


def test_raise_capacity_error_message_tokens() -> None:
    target = ModelId.parse("qwen3-32b-awq-16384")
    summary = build_sticky_capacity_failure_summary(
        model=target,
        trace=None,
        routing_config=None,
        waited_ms=0,
    )

    with pytest.raises(HTTPException) as exc_info:
        raise_capacity_error(target.routing_key, summary)

    message = exc_info.value.detail["message"]
    assert "eviction=" in message
    assert "waited_ms=" in message
    assert "budget_exhausted=" in message
    assert _REQUIRED_DATA_KEYS <= exc_info.value.detail["data"].keys()


def test_builder_uses_routing_key_not_str_model_id() -> None:
    target = ModelId.parse("qwen3-32b-awq-16384")
    with patch("model_id.ModelId.__str__", return_value="SHOULD_NOT_USE"):
        data = build_sticky_capacity_failure_summary(
            model=target,
            trace=None,
            routing_config=None,
            waited_ms=0,
        )
    assert data["model_id"] == target.routing_key
    assert data["model_id"] != "SHOULD_NOT_USE"


def test_format_message_includes_holder_and_eviction() -> None:
    data = {
        "holders": {"loaded": ["m-a"], "busy": [], "loading": []},
        "eviction": {
            "disposition": "refused",
            "constraint": "eviction_blocked_by_busy_models",
            "reason": "busy",
        },
        "waited_ms": 10,
        "wait_budget_s": 300.0,
        "budget_exhausted": False,
    }
    message = format_sticky_capacity_message("m-target", data)
    assert "loaded=" in message
    assert "eviction=refused" in message


def test_holders_unknown_reason_split() -> None:
    target = ModelId.parse("qwen3-32b-awq-16384")
    trace = DecisionTrace(
        model_id=target.routing_key,
        original_model_id=None,
        request_id="req-split",
        candidates=(),
        selection_reason="no_feasible_gateways",
    )
    data = build_sticky_capacity_failure_summary(
        model=target,
        trace=trace,
        routing_config=None,
        waited_ms=0,
    )
    assert data["holders"]["holders_unknown_reason"] == "no_capacity_candidate_on_trace"


def test_capacity_details_three_clocks() -> None:
    target = ModelId.parse("qwen3-32b-awq-16384")
    now_mono = time.monotonic()
    context = SimpleNamespace(
        _eviction_wait_deadline_mono=now_mono - 1.0,
        _eviction_wait_armed_mono=now_mono - 301.0,
    )
    routing_start = time.time() - 5.0
    data = build_sticky_capacity_failure_summary(
        model=target,
        trace=None,
        routing_config={"eviction_wait_timeout_s": 300.0},
        context=context,
        waited_ms=0,
        budget_exhausted=True,
        routing_start_time=routing_start,
    )
    assert data["budget_exhausted"] is True
    assert data["budget_consumed_ms"] > 0
    assert data["waited_ms"] == 0
    assert data["request_elapsed_ms"] is not None
    assert data["request_elapsed_ms"] >= 4000


def test_sticky_capacity_message_and_retry_after() -> None:
    target = ModelId.parse("qwen3-32b-awq-16384")
    summary = build_sticky_capacity_failure_summary(
        model=target,
        trace=None,
        routing_config={"eviction_wait_timeout_s": 60.0},
        waited_ms=0,
        failure_reason="eviction_blocked_queue_timeout",
        budget_exhausted=True,
    )
    with pytest.raises(HTTPException) as exc_info:
        raise_capacity_error(target.routing_key, summary)
    message = exc_info.value.detail["message"]
    assert "reason=eviction_blocked_queue_timeout" in message
    assert "retry:" in message
    assert exc_info.value.headers is not None
    assert exc_info.value.headers.get("Retry-After") == "60"