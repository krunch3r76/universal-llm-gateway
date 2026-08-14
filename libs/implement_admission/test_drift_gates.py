"""Tests for unified-admission warn→enforce drift gates (Step 5)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
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
    check_review_attestation,
    clear_gate_state_cache,
    evaluate_drift_gate,
    gate_state,
)
from implement_admission.review_attestation import (
    ReviewAttestationCode,
    review_attestation_findings,
    review_attestation_warnings,
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
    Provenance,
    Readiness,
    ReadinessState,
    ReviewAttestation,
    Routing,
    RoutingDerivation,
    Scope,
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
        "UA_DRIFT_GATE_RA",
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
    assert gate_state("ra") == DriftGateState.WARN


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


def test_gate_c_primary_partial_ok(tmp_path: Path) -> None:
    artifact = tmp_path / "evidence.md"
    artifact.write_text("ok\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
        evidence_uris=EvidenceUris(
            artifact_paths=[str(artifact)],
            artifact_digests={str(artifact): digest},
        ),
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


def _material_claude_spec(**att_kwargs) -> ImplementSpec:  # noqa: ANN003
    att_defaults = {
        "risk_tier": "material",
        "author_family": "claude",
        "disposition": "missing",
    }
    att_defaults.update(att_kwargs)
    att = ReviewAttestation(**att_defaults)
    return finalize_spec(
        ImplementSpec(
            source=Source(
                source_ref="todo:mat",
                canonical_ref="todo:mat",
                source_kind=SourceKind.TODO,
            ),
            intent=Intent(summary="dispatch admission wiring"),
            scope=Scope(files_expected=["libs/implement_admission/routing.py"]),
            readiness=Readiness(state=ReadinessState.READY),
            routing=Routing(
                orchestration_mode=OrchestrationMode.SINGLE,
                executor_style=ExecutorStyle.MECHANICAL,
                derivation=RoutingDerivation(mode_rule="m", style_rule="s"),
            ),
            acceptance=Acceptance(criteria=["wire handoff warnings"]),
            closeout=Closeout(adapter=CloseoutAdapterKind.TODO),
            provenance=Provenance(review_attestation=att),
        )
    )


def test_review_attestation_warnings_missing_disposition() -> None:
    spec = _material_claude_spec(required=True, disposition="missing")
    warnings = review_attestation_warnings(spec)
    assert any("no passing cross-family review" in w for w in warnings)


def test_review_attestation_warnings_hand_set_required_false() -> None:
    spec = _material_claude_spec(required=False, disposition="missing")
    warnings = review_attestation_warnings(spec)
    assert any("under-classifies risk" in w for w in warnings)


def test_review_attestation_warnings_unbound_pass() -> None:
    spec = _material_claude_spec(
        required=True,
        disposition="pass",
        spec_hash=None,
    )
    warnings = review_attestation_warnings(spec)
    assert any("UNBOUND pass" in w for w in warnings)


def test_review_attestation_warnings_stale_hash() -> None:
    spec = _material_claude_spec(
        required=True,
        disposition="pass",
        spec_hash="sha256:old",
    )
    warnings = review_attestation_warnings(spec)
    assert any("STALE" in w for w in warnings)


def test_review_attestation_warnings_unresolved_blockers() -> None:
    spec = _material_claude_spec(
        required=True,
        disposition="pass",
        spec_hash=implement_spec_hash(_material_claude_spec()),
        unresolved_blocker_ids=["b1", "b2"],
    )
    warnings = review_attestation_warnings(spec)
    assert any("2 unresolved blocker(s)" in w for w in warnings)


def test_review_attestation_warnings_none_attestation() -> None:
    spec = _material_claude_spec().model_copy(
        update={"provenance": Provenance(review_attestation=None)}
    )
    warnings = review_attestation_warnings(spec)
    assert any("no review_attestation present" in w for w in warnings)


def test_review_attestation_warnings_empty_for_mechanical() -> None:
    spec = finalize_spec(
        ImplementSpec(
            source=Source(
                source_ref="todo:mech",
                canonical_ref="todo:mech",
                source_kind=SourceKind.TODO,
            ),
            intent=Intent(summary="rename a local var"),
            readiness=Readiness(state=ReadinessState.READY),
            routing=Routing(
                orchestration_mode=OrchestrationMode.SINGLE,
                executor_style=ExecutorStyle.MECHANICAL,
                derivation=RoutingDerivation(mode_rule="m", style_rule="s"),
            ),
            acceptance=Acceptance(criteria=["done"]),
            closeout=Closeout(adapter=CloseoutAdapterKind.TODO),
            provenance=Provenance(
                review_attestation=ReviewAttestation(
                    risk_tier="mechanical",
                    author_family="claude",
                    disposition="missing",
                )
            ),
        )
    )
    assert review_attestation_warnings(spec) == []


def test_review_attestation_warnings_empty_for_gated() -> None:
    spec = _material_claude_spec().model_copy(
        update={
            "readiness": Readiness(
                state=ReadinessState.GATED,
                gated_reason="blocked",
            ),
            "routing": None,
        }
    )
    assert review_attestation_warnings(spec) == []


def test_review_attestation_warnings_empty_for_valid_bound_pass() -> None:
    base = _material_claude_spec()
    bound_hash = implement_spec_hash(base)
    spec = base.model_copy(
        update={
            "provenance": base.provenance.model_copy(
                update={
                    "review_attestation": ReviewAttestation(
                        required=True,
                        risk_tier="material",
                        author_family="claude",
                        disposition="pass",
                        spec_hash=bound_hash,
                    )
                }
            )
        }
    )
    assert review_attestation_warnings(spec) == []


def test_review_attestation_warnings_surfaced_via_bridge(tmp_path: Path) -> None:
    from systems.frontier_consult.implement_admission_bridge import (
        resolve_source_ref_to_packet,
    )

    class Reader:
        def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003
            return {
                "id": entity_id,
                "name": "dispatch infra",
                "attributes": {
                    "acceptance_criteria": ["wire admission handoff executor"],
                    "files_expected": ["libs/implement_admission/routing.py"],
                },
            }

    ulg = tmp_path / "universal-llm-gateway"
    ulg.mkdir(parents=True)
    clear_gate_state_cache()
    result = resolve_source_ref_to_packet(
        "todo:mat",
        cortex=Reader(),
        workspaces_root=tmp_path,
        author_family="claude-cursor",
    )
    assert not result.gated
    assert any("no passing cross-family review" in w for w in result.warnings)


def test_material_missing_attestation_warn_only_not_reject() -> None:
    spec = _material_claude_spec(required=True, disposition="missing")
    gate_b = check_packet_hash_drift(
        spec, on_disk_sha256=spec.source.source_version.packet_sha256
    )
    assert review_attestation_warnings(spec)
    assert gate_b.action != "reject"


def test_drift_gate_b_still_rejects_genuine_mismatch() -> None:
    spec = _ready_spec()
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        result = check_packet_hash_drift(
            spec,
            on_disk_sha256="sha256:0000000000000000000000000000000000000000000000000000000000000000",
        )
    assert result.action == "reject"


# --- Gate ra (review attestation) ---


def test_review_attestation_findings_per_code() -> None:
    spec = _material_claude_spec(required=True, disposition="missing")
    codes = {f.code for f in review_attestation_findings(spec)}
    assert ReviewAttestationCode.NO_PASSING_REVIEW in codes

    under = _material_claude_spec(required=False, disposition="missing")
    codes = {f.code for f in review_attestation_findings(under)}
    assert ReviewAttestationCode.RISK_UNDERCLASSIFIED in codes
    assert ReviewAttestationCode.NO_PASSING_REVIEW in codes

    unbound = _material_claude_spec(required=True, disposition="pass", spec_hash=None)
    assert ReviewAttestationCode.UNBOUND_REVIEW in {
        f.code for f in review_attestation_findings(unbound)
    }

    stale = _material_claude_spec(
        required=True, disposition="pass", spec_hash="sha256:old"
    )
    assert ReviewAttestationCode.STALE_REVIEW in {
        f.code for f in review_attestation_findings(stale)
    }

    blockers = _material_claude_spec(
        required=True,
        disposition="pass_with_conditions",
        spec_hash=implement_spec_hash(_material_claude_spec()),
        unresolved_blocker_ids=["b1"],
    )
    assert ReviewAttestationCode.UNRESOLVED_BLOCKERS in {
        f.code for f in review_attestation_findings(blockers)
    }

    none_att = _material_claude_spec().model_copy(
        update={"provenance": Provenance(review_attestation=None)}
    )
    none_findings = review_attestation_findings(none_att)
    assert len(none_findings) == 1
    assert none_findings[0].code == ReviewAttestationCode.MISSING_ATTESTATION


def test_review_attestation_warnings_is_message_projection() -> None:
    spec = _material_claude_spec(required=True, disposition="missing")
    findings = review_attestation_findings(spec)
    assert review_attestation_warnings(spec) == [f.message for f in findings]


def test_gate_ra_config_absent_defaults_warn() -> None:
    set_runtime(CloseoutRuntime(dispatch=lambda _tool, _args: {"error": "missing"}))
    clear_gate_state_cache()
    assert gate_state("ra") == DriftGateState.WARN


def test_gate_ra_env_fallback() -> None:
    os.environ["UA_DRIFT_GATE_RA"] = "enforce"
    clear_gate_state_cache()
    set_runtime(CloseoutRuntime(dispatch=lambda _tool, _args: {"error": "missing"}))
    assert gate_state("ra") == DriftGateState.ENFORCE


@pytest.mark.parametrize(
    ("att_kwargs", "expected_reject"),
    [
        ({"required": True, "disposition": "missing"}, True),
        ({"required": True, "disposition": "pass", "spec_hash": None}, True),
        ({"required": True, "disposition": "pass", "spec_hash": "sha256:old"}, True),
    ],
)
def test_gate_ra_enforce_rejects_rejectable_codes(
    att_kwargs: dict, expected_reject: bool
) -> None:
    spec = _material_claude_spec(**att_kwargs)
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        result = check_review_attestation(spec)
    if expected_reject:
        assert result.action == "reject"
    else:
        assert result.action == "warn"


def test_gate_ra_enforce_rejects_missing_attestation() -> None:
    spec = _material_claude_spec().model_copy(
        update={"provenance": Provenance(review_attestation=None)}
    )
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        result = check_review_attestation(spec)
    assert result.action == "reject"
    assert result.reason == ReviewAttestationCode.MISSING_ATTESTATION.value


def test_gate_ra_enforce_admits_with_warn_on_underclassified() -> None:
    base = _material_claude_spec()
    bound = implement_spec_hash(base)
    spec = _material_claude_spec(
        required=False,
        disposition="pass",
        spec_hash=bound,
    )
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        result = check_review_attestation(spec)
    assert result.action == "noop"
    assert result.tripped is False
    assert any(
        f.code == ReviewAttestationCode.RISK_UNDERCLASSIFIED
        for f in review_attestation_findings(spec)
    )
    from systems.frontier_consult.implement_admission_bridge import (
        _enforce_gate_ra_or_warn,
    )

    warnings = _enforce_gate_ra_or_warn(
        request_id="req-under",
        spec=spec,
        headless_vs_human="human",
    )
    assert warnings


def test_gate_ra_enforce_admits_with_warn_on_unresolved_blockers() -> None:
    base = _material_claude_spec()
    bound = implement_spec_hash(base)
    spec = _material_claude_spec(
        required=True,
        disposition="pass_with_conditions",
        spec_hash=bound,
        unresolved_blocker_ids=["blocker-1"],
    )
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        result = check_review_attestation(spec)
    assert result.action == "noop"
    assert result.tripped is False
    assert any(
        f.code == ReviewAttestationCode.UNRESOLVED_BLOCKERS
        for f in review_attestation_findings(spec)
    )
    from systems.frontier_consult.implement_admission_bridge import (
        _enforce_gate_ra_or_warn,
    )

    warnings = _enforce_gate_ra_or_warn(
        request_id="req-blockers",
        spec=spec,
        headless_vs_human="human",
    )
    assert any("unresolved blocker" in w for w in warnings)


def test_gate_ra_off_emits_no_warnings() -> None:
    spec = _material_claude_spec(required=True, disposition="missing")
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.OFF,
    ):
        result = check_review_attestation(spec)
    assert result.action == "noop"


def test_gate_ra_enforce_to_warn_rollback_within_ttl() -> None:
    spec = _material_claude_spec(required=True, disposition="missing")

    def enforce_ra(_gate_id: str) -> DriftGateState:
        return DriftGateState.ENFORCE

    with patch("implement_admission.drift_gates.gate_state", side_effect=enforce_ra):
        assert check_review_attestation(spec).action == "reject"

    clear_gate_state_cache()
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.WARN,
    ):
        assert check_review_attestation(spec).action == "warn"


def test_bridge_enforce_gate_ra_reject_reason_format() -> None:
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.implement_admission_bridge import (
        _enforce_gate_ra_or_warn,
    )

    spec = _material_claude_spec(required=True, disposition="missing")
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        with pytest.raises(FrontierEndpointError) as exc:
            _enforce_gate_ra_or_warn(
                request_id="req-1",
                spec=spec,
                headless_vs_human="human",
            )
    err = exc.value
    assert err.code == "handoff_review_attestation_blocked"
    assert err.field == "source_ref"
    assert "NO_PASSING_REVIEW" in err.reason
    assert "attach a non-claude pass/pass_with_conditions review" in err.reason
    assert "implement_spec_hash" in err.reason


def test_bridge_gate_ra_applies_to_packet_lane() -> None:
    from systems.frontier_consult.admission import FrontierEndpointError
    from systems.frontier_consult.implement_admission_bridge import (
        _enforce_gate_ra_or_warn,
    )

    spec = _material_claude_spec(required=True, disposition="missing")
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        with pytest.raises(FrontierEndpointError) as exc:
            _enforce_gate_ra_or_warn(
                request_id="req-packet",
                spec=spec,
                headless_vs_human="headless",
            )
    assert exc.value.code == "handoff_review_attestation_blocked"


def test_gate_c_pointer_only_bus_and_dispatch_trips_no_evidence() -> None:
    """Slice 3 AC-4: self-minted bus_threads + dispatch_ids must not satisfy gate C.

    Against HEAD this test fails (flatten_evidence_uris is non-empty). After
    verifiable_evidence_uris the same closeout trips ``no_evidence``.
    """
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
        evidence_uris=EvidenceUris(
            bus_threads=["7210"],
            dispatch_ids=["auto-pointer-only"],
        ),
    )
    source = Source(
        source_ref="todo:foo",
        canonical_ref="todo:foo",
        source_kind=SourceKind.TODO,
    )
    results = [
        AdapterResult(
            adapter="todo",
            status="complete",
            mutation="workflow_state=done",
        )
    ]
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        result = check_closeout_evidence(results, source=source, closeout=closeout)
    assert result.action == "reject"
    assert result.reason == "no_evidence"


def test_gate_c_digest_match_admits(tmp_path: Path) -> None:
    artifact = tmp_path / "sidecar.md"
    artifact.write_text("published\n", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
        evidence_uris=EvidenceUris(
            artifact_paths=[str(artifact)],
            artifact_digests={str(artifact): digest},
        ),
    )
    source = Source(
        source_ref="todo:foo",
        canonical_ref="todo:foo",
        source_kind=SourceKind.TODO,
    )
    results = [
        AdapterResult(adapter="todo", status="complete", mutation="done")
    ]
    result = check_closeout_evidence(results, source=source, closeout=closeout)
    assert result.action == "noop"


def test_gate_c_digest_mismatch_trips(tmp_path: Path) -> None:
    artifact = tmp_path / "sidecar.md"
    artifact.write_text("published\n", encoding="utf-8")
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
        evidence_uris=EvidenceUris(
            artifact_paths=[str(artifact)],
            artifact_digests={str(artifact): "0" * 64},
        ),
    )
    source = Source(
        source_ref="todo:foo",
        canonical_ref="todo:foo",
        source_kind=SourceKind.TODO,
    )
    results = [
        AdapterResult(adapter="todo", status="complete", mutation="done")
    ]
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        result = check_closeout_evidence(results, source=source, closeout=closeout)
    assert result.action == "reject"
    assert result.reason == "evidence_digest_mismatch"


def test_gate_c_path_without_digest_does_not_admit(tmp_path: Path) -> None:
    artifact = tmp_path / "sidecar.md"
    artifact.write_text("exists\n", encoding="utf-8")
    closeout = ImplementCloseout(
        status=CloseoutStatus.COMPLETE,
        summary="x",
        source_ref="todo:foo",
        evidence_uris=EvidenceUris(artifact_paths=[str(artifact)]),
    )
    source = Source(
        source_ref="todo:foo",
        canonical_ref="todo:foo",
        source_kind=SourceKind.TODO,
    )
    results = [
        AdapterResult(adapter="todo", status="complete", mutation="done")
    ]
    with patch(
        "implement_admission.drift_gates.gate_state",
        return_value=DriftGateState.ENFORCE,
    ):
        result = check_closeout_evidence(results, source=source, closeout=closeout)
    assert result.action == "reject"
    assert result.reason == "no_evidence"
