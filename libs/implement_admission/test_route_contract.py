"""Tests for route_contract resolver, preflight, policy drift, and admission payload."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.normalize import normalize
from implement_admission.preflight import (
    RouteContractContradictionError,
    admission_route_contract_payload,
    lint_prose_route_contradictions,
    run_route_preflight,
    verify_dispatch_transport_coverage,
)
from implement_admission.routing import (
    load_route_policy,
    render_consult_routing_policy_block,
    resolve_route_contract,
    verify_consult_routing_policy_drift,
)
from implement_admission.spec import (
    Acceptance,
    Closeout,
    CloseoutAdapterKind,
    ExecutorStyle,
    ImplementSpec,
    Intent,
    OrchestrationMode,
    Readiness,
    ReadinessState,
    RouteContract,
    Routing,
    RoutingDerivation,
    Scope,
    Source,
    SourceKind,
    finalize_spec,
)


def _ready_spec(**overrides) -> ImplementSpec:  # noqa: ANN003
    base = dict(
        source=Source(
            source_ref="todo:foo",
            canonical_ref="todo:foo",
            source_kind=SourceKind.TODO,
        ),
        intent=Intent(summary="dispatch infra"),
        scope=Scope(files_expected=["libs/implement_admission/routing.py"]),
        readiness=Readiness(state=ReadinessState.READY),
        routing=Routing(
            orchestration_mode=OrchestrationMode.SINGLE,
            executor_style=ExecutorStyle.MECHANICAL,
            derivation=RoutingDerivation(mode_rule="m", style_rule="s"),
        ),
        acceptance=Acceptance(criteria=["wire route_contract"]),
        closeout=Closeout(adapter=CloseoutAdapterKind.TODO),
    )
    base.update(overrides)
    return ImplementSpec(**base)


class _StubCortex:
    def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        return {
            "id": entity_id,
            "name": "dispatch infra",
            "attributes": {
                "files_expected": ["libs/implement_admission/routing.py"],
                "acceptance_criteria": ["wire route_contract"],
            },
        }


def test_resolve_route_contract_implement_cursor_sdk_seven_fields() -> None:
    spec = _ready_spec()
    rc = resolve_route_contract(
        spec,
        spec.routing,
        "material",
        contract="implement",
        role="cursor-sdk",
    )
    assert rc.policy_source == "consult-routing"
    assert rc.policy_version == "2026-07-06"
    assert rc.dispatch_kind == "implement"
    assert rc.transport == "team_dispatch"
    assert rc.autonomy == "auto_executed"
    assert rc.operator_pickup_required is False
    assert rc.lead_claim_authority == "server_contract_overrides_packet_prose"


def test_finalize_spec_attaches_route_contract() -> None:
    spec = finalize_spec(
        _ready_spec(),
        contract="implement",
        role="cursor-sdk",
    )
    assert spec.route_contract is not None
    assert spec.route_contract.autonomy == "auto_executed"


def test_normalize_with_contract_emits_admission_payload() -> None:
    spec = normalize(
        "todo:pre-dispatch-consult-routing-gate",
        cortex=_StubCortex(),
        contract="implement",
        role="cursor-sdk",
    )
    payload = admission_route_contract_payload(spec)
    rc = payload["route_contract"]
    assert set(rc) == {
        "policy_source",
        "policy_version",
        "dispatch_kind",
        "transport",
        "autonomy",
        "operator_pickup_required",
        "lead_claim_authority",
    }
    assert rc["autonomy"] == "auto_executed"


def test_structured_contradiction_operator_pickup_required() -> None:
    spec = finalize_spec(_ready_spec(), contract="implement", role="cursor-sdk")
    with pytest.raises(RouteContractContradictionError) as exc:
        run_route_preflight(spec, operator_pickup_required=True)
    assert exc.value.field == "operator_pickup_required"
    assert exc.value.canonical_value is False


def test_structured_contradiction_autonomy() -> None:
    spec = finalize_spec(_ready_spec(), contract="implement", role="cursor-sdk")
    with pytest.raises(RouteContractContradictionError):
        run_route_preflight(spec, autonomy="manual_pickup")


def test_structured_non_contradicting_or_absent_passes() -> None:
    spec = finalize_spec(_ready_spec(), contract="implement", role="cursor-sdk")
    assert run_route_preflight(spec) == []
    assert run_route_preflight(spec, operator_pickup_required=False) == []


def test_prose_lint_manual_ide_pickup_warns() -> None:
    rc = RouteContract(
        policy_source="consult-routing",
        policy_version="2026-07-06",
        dispatch_kind="implement",
        transport="team_dispatch",
        autonomy="auto_executed",
        operator_pickup_required=False,
        lead_claim_authority="server_contract_overrides_packet_prose",
    )
    warnings = lint_prose_route_contradictions(
        "cursor-sdk implement needs manual IDE pickup",
        rc,
    )
    assert len(warnings) == 1
    assert "manual IDE pickup" in warnings[0]


def test_prose_lint_manual_review_no_false_positive() -> None:
    rc = RouteContract(
        policy_source="consult-routing",
        policy_version="2026-07-06",
        dispatch_kind="implement",
        transport="team_dispatch",
        autonomy="auto_executed",
        operator_pickup_required=False,
        lead_claim_authority="server_contract_overrides_packet_prose",
    )
    assert (
        lint_prose_route_contradictions("requires manual review before merge", rc) == []
    )


def test_policy_drift_check_passes_when_embedded(tmp_path: Path) -> None:
    policy = load_route_policy()
    block = render_consult_routing_policy_block(policy)
    doc = tmp_path / "consult-routing.md"
    doc.write_text(f"# Consult Routing\n\n{block}\n", encoding="utf-8")
    assert verify_consult_routing_policy_drift(doc) is True


def test_policy_drift_check_fails_on_divergence(tmp_path: Path) -> None:
    policy = load_route_policy()
    block = render_consult_routing_policy_block(policy).replace(
        "auto_executed", "manual_pickup"
    )
    doc = tmp_path / "consult-routing.md"
    doc.write_text(block, encoding="utf-8")
    assert verify_consult_routing_policy_drift(doc) is False


def test_dispatch_transport_registry_has_no_gaps() -> None:
    assert verify_dispatch_transport_coverage() == []
