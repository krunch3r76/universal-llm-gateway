"""Tests for unified-admission warn→enforce drift gates (Step 5)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from implement_admission.closeout_models import (
    AdapterResult,
    EvidenceUris,
    ImplementCloseout,
)
from implement_admission.closeout_runtime import (
    CloseoutRuntime,
    reset_runtime,
    set_runtime,
)
from implement_admission.drift_gates import (
    DriftGateState,
    apply_closeout_gate_b,
    apply_closeout_gate_c,
    check_bound_source_ref,
    check_closeout_evidence,
    check_closeout_hash_drift,
    check_frontmatter_source_ref,
    check_packet_hash_drift,
    clear_gate_state_cache,
    evaluate_drift_gate,
    gate_state,
)
from implement_admission.spec import (
    Acceptance,
    Closeout,
    CloseoutAdapterKind,
    CloseoutStatus,
    ExecutorStyle,
    ImplementSpec,
    Intent,
    OrchestrationMode,
    Readiness,
    ReadinessState,
    Routing,
    RoutingDerivation,
    Source,
    SourceKind,
    SourceVersion,
    finalize_spec,
    implement_spec_hash,
)


@pytest.fixture(autouse=True)
def _reset_gate_cache() -> None:
    clear_gate_state_cache()
    reset_runtime()
    for key in (
        "UA_DRIFT_GATE_A",
        "UA_DRIFT_GATE_A2",
        "UA_DRIFT_GATE_B",
        "UA_DRIFT_GATE_C",
    ):
        os.environ.pop(key, None)


def _ready_spec(**overrides) -> ImplementSpec:  # noqa: ANN003
    base = dict(
        source=Source(
            source_ref="todo:foo",
            canonical_ref="todo:foo",
            source_kind=SourceKind.TODO,
            source_version=SourceVersion(
                content_hash="sha256:abc",
                packet_sha256="sha256:deadbeef",
            ),
        ),
        intent=Intent(summary="test"),
        readiness=Readiness(state=ReadinessState.READY),
        routing=Routing(
            orchestration_mode=OrchestrationMode.SINGLE,
            executor_style=ExecutorStyle.MECHANICAL,
            derivation=RoutingDerivation(mode_rule="m", style_rule="s"),
        ),
        acceptance=Acceptance(criteria=["done"]),
        closeout=Closeout(adapter=CloseoutAdapterKind.TODO),
    )
    base.update(overrides)
    return finalize_spec(ImplementSpec(**base))


def test_evaluate_drift_gate_off_is_noop() -> None:
    result = evaluate_drift_gate("a", DriftGateState.OFF, tripped=True, reason="miss")
    assert result.action == "noop"


def test_evaluate_drift_gate_warn() -> None:
    result = evaluate_drift_gate("a", DriftGateState.WARN, tripped=True, reason="miss")
    assert result.action == "warn"


def test_evaluate_drift_gate_enforce() -> None:
    result = evaluate_drift_gate(
        "a", DriftGateState.ENFORCE, tripped=True, reason="miss"
    )
    assert result.action == "reject"


def test_gate_state_defaults_warn_when_config_absent() -> None:
    set_runtime(CloseoutRuntime(dispatch=lambda _tool, _args: {"error": "missing"}))
    assert gate_state("a") == DriftGateState.WARN
    assert gate_state("b") == DriftGateState.WARN
    assert gate_state("c") == DriftGateState.WARN


def test_config_absent_env_fallback() -> None:
    os.environ["UA_DRIFT_GATE_A"] = "enforce"
    clear_gate_state_cache()
    set_runtime(CloseoutRuntime(dispatch=lambda _tool, _args: {"error": "missing"}))
    assert gate_state("a") == DriftGateState.ENFORCE


def test_gate_state_reads_config_entity() -> None:
    def fake_dispatch(tool: str, args: dict) -> dict:
        assert tool == "entity_get"
        return {
            "attributes": {
                "gate_a": "off",
                "gate_b": "warn",
                "gate_c": "enforce",
            }
        }

    set_runtime(CloseoutRuntime(dispatch=fake_dispatch))
    assert gate_state("a") == DriftGateState.OFF
    assert gate_state("b") == DriftGateState.WARN
    assert gate_state("c") == DriftGateState.ENFORCE


def test_check_bound_source_ref_enforce_rejects() -> None:
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        result = check_bound_source_ref(source_ref=None)
    assert result.action == "reject"


def test_check_bound_source_ref_present_no_trip() -> None:
    result = check_bound_source_ref(source_ref="todo:foo")
    assert result.action == "noop"


def test_gate_a_param_or_frontmatter() -> None:
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        assert check_bound_source_ref(source_ref=None).action == "reject"
        assert check_bound_source_ref(source_ref="todo:foo").action == "noop"
        assert (
            check_bound_source_ref(
                source_ref=None,
                packet_frontmatter_source_ref="todo:bar",
            ).action
            == "noop"
        )
        assert (
            check_bound_source_ref(
                source_ref="todo:foo",
                packet_frontmatter_source_ref="todo:bar",
            ).action
            == "noop"
        )


def test_gate_a2_tri_state() -> None:
    with patch(
        "implement_admission.drift_gates.gate_state",
        side_effect=lambda gate_id: {
            "a2": DriftGateState.ENFORCE,
        }.get(gate_id, DriftGateState.WARN),
    ):
        reject = check_frontmatter_source_ref(packet_frontmatter_source_ref=None)
        assert reject.action == "reject"
    ok = check_frontmatter_source_ref(packet_frontmatter_source_ref="todo:foo")
    assert ok.action == "noop"


def test_hash_drift_and_rematerialize_clears() -> None:
    spec = _ready_spec()
    drifted = spec.model_copy(update={"intent": Intent(summary="changed after stamp")})
    result = check_packet_hash_drift(drifted)
    assert result.tripped is True

    rematerialized = finalize_spec(drifted)
    ok = check_packet_hash_drift(rematerialized)
    assert ok.tripped is False


def test_packet_sha_drift() -> None:
    spec = _ready_spec()
    result = check_packet_hash_drift(
        spec,
        on_disk_sha256="sha256:0000000000000000000000000000000000000000000000000000000000000000",
    )
    assert result.tripped is True


def test_gate_c_primary_missing() -> None:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
        evidence_uris=EvidenceUris(cortex_assertions=["assertion:1"]),
    )
    source = Source(
        source_ref="todo:foo",
        canonical_ref="todo:foo",
        source_kind=SourceKind.TODO,
    )
    results = [
        AdapterResult(
            adapter="agent-bus",
            status="complete",
            mutation="bus artifact",
        )
    ]
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        result = check_closeout_evidence(results, source=source, closeout=closeout)
    assert result.action == "reject"
    assert result.reason == "primary_missing"


def test_gate_c_primary_partial_ok() -> None:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
        evidence_uris=EvidenceUris(cortex_assertions=["assertion:1"]),
    )
    source = Source(
        source_ref="todo:foo",
        canonical_ref="todo:foo",
        source_kind=SourceKind.TODO,
    )
    results = [
        AdapterResult(
            adapter="todo",
            status="partial",
            mutation="workflow_state=done",
        )
    ]
    result = check_closeout_evidence(results, source=source, closeout=closeout)
    assert result.action == "noop"


def test_apply_gate_c_forces_failed() -> None:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
    )
    source = Source(
        source_ref="todo:foo",
        canonical_ref="todo:foo",
        source_kind=SourceKind.TODO,
    )
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        out = apply_closeout_gate_c(closeout, [], source=source)
    assert out.status == CloseoutStatus.FAILED


def test_gates_do_not_mutate_payload_fields() -> None:
    spec = _ready_spec()
    before_ref = spec.source.source_ref
    before_hash = spec.provenance.implement_spec_hash
    check_packet_hash_drift(spec)
    assert spec.source.source_ref == before_ref
    assert spec.provenance.implement_spec_hash == before_hash


def test_check_closeout_hash_drift_skips_packet_lane() -> None:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="packet:universal-llm-gateway/tmp/reviews/foo.md",
    )
    result = check_closeout_hash_drift(closeout)
    assert result.action == "noop"
    assert result.tripped is False
    assert "pass-through" in (result.detail or "")


def test_apply_gate_b_complete_to_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
    )

    def _reject(*_args, **_kwargs):
        from implement_admission.drift_gates import DriftGateResult

        return DriftGateResult(
            gate_id="b",
            tripped=True,
            action="reject",
            detail="drift",
        )

    monkeypatch.setattr(
        "implement_admission.drift_gates.check_closeout_hash_drift", _reject
    )
    out = apply_closeout_gate_b(closeout)
    assert out.status == CloseoutStatus.PARTIAL
    assert out.deviations
