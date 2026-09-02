"""Tests for doc_template and doc_validate cortex ops (AC1–AC7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from implement_admission.dense_spec_schema import (
    dense_spec_hash_uri,
    validate_dense_spec,
)
from implement_admission.gate_distillation import GateDistillationInputs
from implement_admission.implement_ready import (
    ImplementReadyVerdict,
    evaluate_implement_ready,
)
from implement_admission.implement_ready_preflight import preflight_implement_ready
from implement_admission.recon_waiver import build_structured_waiver

from cortex_store.dispatch_ops._session_close_doc_type import (
    session_close_attestation_tokens,
)
from cortex_store.dispatch_ops._todo_gate_distillation_impl import (
    _evaluate_from_persisted,
)
from cortex_store.dispatch_ops.adapters._doc_template import (
    _SUPPORTED_DOC_TYPES,
    _op_doc_template,
)
from cortex_store.dispatch_ops.adapters._doc_validate import _op_doc_validate
from cortex_store.dispatch_ops.ops_review_gate import _PRE_CLOSE_GATE_KINDS

NOW = datetime.now(UTC).isoformat()

SPEC_TEXT_VALID = """\
# Dense test spec

## 1. Problem

A problem exists.

## 2. Non-goals / scope exclusions

Out of scope items.

## 3. Source-of-truth / provenance

| Source | Role |
|---|---|
| spec | authoritative |

## 4. Touch-point inventory

- module.py

## 5. Bound design decisions / fork table

| Fork | Decision |
|---|---|
| 1 | resolved |

## 6. Implementation guidance

Build the validator.

## 7. Acceptance criteria

1. Validator passes dense specs.

## 8. Verification / quality gates

- pytest green

<reasoning_trace>

No fork remains open.

</reasoning_trace>
"""

SPEC_TEXT_INVALID = "# Incomplete\n\n## Problem\n\nOnly one section.\n"


def _ready_evidence() -> list[str]:
    return ["tasks/specs/test-slug.md", dense_spec_hash_uri(SPEC_TEXT_VALID)]


def _stale_evidence() -> list[str]:
    return ["tasks/specs/test-slug.md", "spec_sha256:deadbeef" * 4]


@pytest.mark.offline
def test_ac1_doc_template_round_trip() -> None:
    result = _op_doc_template(doc_type="implement_dense_spec")
    assert result["ok"] is True
    template = result["template"]
    assert "## Problem" in template
    assert "<reasoning_trace>" in template
    assert "dense_spec_hash_uri" in template
    for key in result["required_sections"]:
        heading = key.replace("_", "-")
        assert heading in template.lower() or key.replace("_", " ") in template.lower()
    verdict = validate_dense_spec(template)
    assert verdict.passed, verdict.reason


@pytest.mark.offline
def test_ac1_unknown_doc_type_422() -> None:
    result = _op_doc_template(doc_type="unknown")
    assert result["status_code"] == 422
    assert "implement_dense_spec" in result["error"]


@pytest.mark.offline
def test_session_close_supported_doc_type() -> None:
    assert "session_close" in _SUPPORTED_DOC_TYPES


@pytest.mark.offline
def test_session_close_doc_template_contract() -> None:
    result = _op_doc_template(doc_type="session_close")
    assert result["ok"] is True
    template = result["template"]
    assert "## Required fields" in template
    assert "## Transcript depth rules" in template
    assert "## Pre-close audit gate codes (13)" in template
    assert "## Canonical skill pointers" in template
    for code in _PRE_CLOSE_GATE_KINDS:
        assert f"`{code}`" in template
    assert set(result["required_sections"]) == {
        "session_id",
        "agent",
        "session_summary_md",
        "summary",
    }


@pytest.mark.offline
def test_session_close_variant_overlays() -> None:
    web = _op_doc_template(doc_type="session_close:web")
    cursor = _op_doc_template(doc_type="session_close:cursor")
    assert web["variant"] == "web"
    assert cursor["variant"] == "cursor"
    assert "Web seat" in web["template"]
    assert "Cursor seat" in cursor["template"]
    assert web["metadata"]["platform"] == "web"
    assert cursor["metadata"]["platform"] == "cursor"
    for code in _PRE_CLOSE_GATE_KINDS[:3]:
        assert f"`{code}`" in web["template"]
        assert f"`{code}`" in cursor["template"]


@pytest.mark.offline
def test_session_close_unknown_variant_422() -> None:
    result = _op_doc_template(doc_type="session_close:unknown")
    assert result["status_code"] == 422


@pytest.mark.offline
def test_session_close_doc_validate_requires_payload() -> None:
    result = _op_doc_validate(doc_type="session_close")
    assert result["status_code"] == 422


@pytest.mark.offline
def test_session_close_doc_validate_delegates_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_preflight(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "ok": True,
            "audit": {"warning": {"gap_count": 0}},
            "warnings": [],
            "turn_count": 2,
        }

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_session_close._op_session_close_preflight",
        _fake_preflight,
    )
    result = _op_doc_validate(
        doc_type="session_close",
        session_id="cursor-2026-06-30-1200-abc",
        agent="cursor",
        session_summary_md="## Session Summary\n\nDone.",
        summary="Arc: closed the loop on session close attestation gate.",
        transcript_depth="light",
    )
    assert result["status"] == "pass"
    assert result["attestation_tokens"] == session_close_attestation_tokens(
        session_id="cursor-2026-06-30-1200-abc"
    )
    assert captured["session_id"] == "cursor-2026-06-30-1200-abc"


@pytest.mark.offline
def test_implement_dense_spec_doc_template_regression_snapshot() -> None:
    result = _op_doc_template(doc_type="implement_dense_spec")
    assert result["ok"] is True
    assert result["doc_type"] == "implement_dense_spec"
    assert result["template_version"] == "1.0.0"
    assert result["required_sections"] == [
        "problem",
        "non_goals",
        "provenance",
        "touch_points",
        "forks",
        "implementation",
        "acceptance",
        "verification",
    ]
    assert "## Problem" in result["template"]
    assert "dense_spec_hash_uri" in result["template"]


@pytest.mark.offline
def test_ac2_doc_validate_text_aggregate_pass() -> None:
    result = _op_doc_validate(doc_type="implement_dense_spec", text=SPEC_TEXT_VALID)
    assert result["status"] == "pass"
    assert result["admitted"] is True
    assert result["spec_sha256"] == dense_spec_hash_uri(SPEC_TEXT_VALID)
    assert len(result["gates"]) == 15
    assert {g["status"] for g in result["gates"]} <= {
        "passed",
        "failed",
        "blocked",
        "not_applicable",
    }
    assert result["attested"] is False
    assert result["skeptic"]["deferred_to_stargate"] is True


@pytest.mark.offline
def test_ac3_doc_validate_path_text_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    rel = "tasks/specs/parity-fixture.md"
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.adapters._doc_validate.read_dense_spec_text",
        lambda _path: SPEC_TEXT_VALID,
    )
    by_text = _op_doc_validate(doc_type="implement_dense_spec", text=SPEC_TEXT_VALID)
    by_path = _op_doc_validate(doc_type="implement_dense_spec", path=rel)
    for key in ("status", "spec_sha256", "admitted", "attested", "gates"):
        assert by_text[key] == by_path[key], key


@pytest.mark.offline
def test_ac4_doc_validate_drift_via_source_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    stale_hash = (
        "spec_sha256:0000000000000000000000000000000000000000000000000000000000000000"
    )
    kwargs = {
        "todo_id": "todo:drift-slug",
        "density_triage": "judgment_required",
        "source_uri": "tasks/specs/drift-slug.md",
        "implement_ready_assertion_id": 99,
        "assertion": {
            "id": 99,
            "entity_id": "todo:drift-slug",
            "superseded_by": None,
            "valid_until": None,
            "evidence_uris": ["tasks/specs/drift-slug.md", stale_hash],
        },
        "dense_spec_uri": "tasks/specs/drift-slug.md",
        "dense_spec_text": SPEC_TEXT_VALID,
        "files_expected": ["libs/a.py"],
        "acceptance_criteria": ["AC passes"],
        "entity_name": "Drift todo",
        "resolution": None,
        "skeptic_ratified": True,
        "recon_waived": False,
    }

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.adapters._doc_validate.resolve_todo_preflight_kwargs",
        lambda _ref, *, now_iso: kwargs,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.adapters._doc_validate.find_skeptic_assertion",
        lambda **_: None,
    )
    result = _op_doc_validate(
        doc_type="implement_dense_spec",
        source_ref="todo:drift-slug",
    )
    live_hash = dense_spec_hash_uri(SPEC_TEXT_VALID)
    assert result["status"] == "drifted_since_ready"
    assert result["spec_sha256"] == live_hash
    assert result["pinned_sha256"] == stale_hash
    assert result["attested"] is False


@pytest.mark.offline
def test_ac5_aggregate_lists_all_failures_vs_fail_fast() -> None:
    args = dict(
        todo_id="todo:multi-fail",
        density_triage="judgment_required",
        source_uri="tasks/specs/multi-fail.md",
        implement_ready_assertion_id=None,
        assertion=None,
        now_iso=NOW,
        dense_spec_uri="tasks/specs/multi-fail.md",
        dense_spec_text=SPEC_TEXT_VALID,
        files_expected=[],
        acceptance_criteria=[],
        entity_name="Multi fail",
        skeptic_ratified=False,
        recon_waived=False,
    )
    report = preflight_implement_ready(**args)
    verdict = evaluate_implement_ready(**args)
    failed_gates = [g for g in report.gates if g.status.value == "failed"]
    assert len(failed_gates) >= 2
    assert verdict.admitted is False
    assert verdict.code == failed_gates[0].code


@pytest.mark.offline
def test_ac6_not_attested_missing_spec_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = {
        "todo_id": "todo:no-hash",
        "density_triage": "judgment_required",
        "source_uri": "tasks/specs/no-hash.md",
        "implement_ready_assertion_id": 7,
        "assertion": {
            "id": 7,
            "entity_id": "todo:no-hash",
            "superseded_by": None,
            "valid_until": None,
            "evidence_uris": ["tasks/specs/no-hash.md"],
        },
        "dense_spec_uri": "tasks/specs/no-hash.md",
        "dense_spec_text": SPEC_TEXT_VALID,
        "files_expected": ["libs/a.py"],
        "acceptance_criteria": ["AC passes"],
        "entity_name": "No hash todo",
        "resolution": None,
        "skeptic_ratified": True,
        "recon_waived": False,
    }
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.adapters._doc_validate.resolve_todo_preflight_kwargs",
        lambda _ref, *, now_iso: kwargs,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.adapters._doc_validate.find_skeptic_assertion",
        lambda **_: None,
    )
    result = _op_doc_validate(
        doc_type="implement_dense_spec",
        source_ref="todo:no-hash",
    )
    assert result["status"] == "not_attested"
    assert result["attested"] is False
    assert result["pinned_sha256"] is None


@pytest.mark.offline
def test_ac7_skeptic_grounding_deferred_without_todo_context() -> None:
    result = _op_doc_validate(doc_type="implement_dense_spec", text=SPEC_TEXT_VALID)
    assert "deferred_to_stargate" in result["skeptic"]
    assert result["skeptic"]["deferred_to_stargate"] is True


@pytest.mark.offline
def test_ac7_skeptic_grounding_runs_when_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs = {
        "todo_id": "todo:skeptic",
        "density_triage": "judgment_required",
        "source_uri": "tasks/specs/skeptic.md",
        "implement_ready_assertion_id": 3,
        "assertion": {
            "id": 3,
            "entity_id": "todo:skeptic",
            "superseded_by": None,
            "valid_until": None,
            "evidence_uris": _ready_evidence(),
        },
        "dense_spec_uri": "tasks/specs/skeptic.md",
        "dense_spec_text": SPEC_TEXT_VALID,
        "files_expected": ["libs/a.py"],
        "acceptance_criteria": ["AC passes"],
        "entity_name": "Skeptic todo",
        "resolution": None,
        "skeptic_ratified": True,
        "recon_waived": False,
    }
    skeptic_assertion = {
        "entity_id": "todo:skeptic",
        "evidence_uris": ["agent-bus:1234", dense_spec_hash_uri(SPEC_TEXT_VALID)],
        "observed_at": "2026-06-29T12:00:00+00:00",
    }

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.adapters._doc_validate.resolve_todo_preflight_kwargs",
        lambda _ref, *, now_iso: kwargs,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.adapters._doc_validate.find_skeptic_assertion",
        lambda **_: skeptic_assertion,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.adapters._doc_validate.evaluate_skeptic_grounding",
        lambda **_: {
            "ratified": True,
            "evidence_grounded": True,
            "evidence_unresolved": None,
            "evidence_mode": None,
            "deferred_to_stargate": False,
        },
    )
    result = _op_doc_validate(
        doc_type="implement_dense_spec",
        source_ref="todo:skeptic",
    )
    assert result["skeptic"]["deferred_to_stargate"] is False
    assert result["skeptic"]["evidence_grounded"] is True


@pytest.mark.offline
def test_schema_failed_status() -> None:
    result = _op_doc_validate(doc_type="implement_dense_spec", text=SPEC_TEXT_INVALID)
    assert result["status"] == "schema_failed"
    assert result["admitted"] is False
    gate9 = next(g for g in result["gates"] if g["gate"] == 9)
    assert gate9["status"] == "failed"
    assert "section_hints" in gate9


@pytest.mark.offline
def test_input_validation_exactly_one_source() -> None:
    result = _op_doc_validate(doc_type="implement_dense_spec")
    assert result["status_code"] == 422
    result = _op_doc_validate(
        doc_type="implement_dense_spec",
        text=SPEC_TEXT_VALID,
        path="tasks/specs/x.md",
    )
    assert result["status_code"] == 422


_CURRENT_SPEC_HASH = dense_spec_hash_uri(SPEC_TEXT_VALID)
_STALE_SPEC_HASH = "spec_sha256:" + ("a" * 64)


def _gate_distill_prepared() -> GateDistillationInputs:
    return GateDistillationInputs(
        todo_id="todo:waiver-test",
        spec_path="tasks/specs/waiver-test.md",
        spec_text=SPEC_TEXT_VALID,
        evidence_uris=["tasks/specs/waiver-test.md", _CURRENT_SPEC_HASH],
        schema=validate_dense_spec(SPEC_TEXT_VALID),
    )


_CONSULT_PROVENANCE = {
    "consult_thread": "agent-bus:8801",
    "verdict": "proceed_with_amendments",
    "consultant_model": "claude-fable-5-1",
    "consultant_effort": "high",
    "consultant_substrate": "web-anthropic",
}


def _entity_with_recon_waiver(
    *,
    spec_sha256: str,
    include_consult: bool = True,
) -> dict:
    waiver = build_structured_waiver(
        reason_code="operator_directive",
        reason="distill waiver test",
        waived_by="test-agent",
        spec_sha256=spec_sha256,
    )
    attrs: dict = {
        "density_triage": "judgment_required",
        "implement_ready_assertion_id": 1,
        "files_expected": ["libs/a.py"],
        "acceptance_criteria": ["AC passes"],
        "recon_waived": waiver.to_attr_json(),
        # Axis-2 opt-in so stale waiver → skeptic_pass_missing (not silent admit).
        "check_requested": True,
    }
    if include_consult:
        attrs.update(_CONSULT_PROVENANCE)
    return {
        "id": "todo:waiver-test",
        "name": "waiver test",
        "source_uri": "tasks/specs/waiver-test.md",
        "attributes": attrs,
    }


def _ready_assertion() -> dict:
    return {
        "id": 1,
        "entity_id": "todo:waiver-test",
        "superseded_by": None,
        "valid_until": None,
        "evidence_uris": [
            "tasks/specs/waiver-test.md",
            _CURRENT_SPEC_HASH,
        ],
    }


@pytest.mark.offline
def test_evaluate_from_persisted_honors_matching_recon_waiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from implement_admission.implement_ready_gate_resolve import (
        SkepticRatificationOutcome,
    )

    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_entity_get",
        lambda **_: _entity_with_recon_waiver(spec_sha256=_CURRENT_SPEC_HASH),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_assertion_get",
        lambda **_: _ready_assertion(),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.read_dense_spec_text",
        lambda _: SPEC_TEXT_VALID,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.resolve_skeptic_ratification",
        lambda **_: SkepticRatificationOutcome(ratified=False),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.load_todo_consult_provenance",
        lambda todo_id, **_: {
            "todo": todo_id,
            "consult_thread": "agent-bus:8801#12",
            "verdict": "ADMIT",
            "adjudication_assertion_id": 1,
            "consultant_model": "claude-fable-5-1",
    "consultant_effort": "high",
            "consultant_substrate": "cdp",
            "archive_uri": "cortex://notes/system/threads/archives/x.md",
            "archive_sha256": "deadbeef",
            "satellite_execution_id": "sat-1",
            "stargate_execution_id": "sg-1",
            "written_by": "test",
            "written_at": "2026-08-14T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        "implement_admission.implement_ready.structural_gaps",
        lambda *a, **k: [],
    )

    verdict = _evaluate_from_persisted(
        entity_id="todo:waiver-test",
        prepared=_gate_distill_prepared(),
    )
    assert verdict.admitted is True


@pytest.mark.offline
def test_evaluate_from_persisted_forwards_consult_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """a:26245 — post-check must pass stamped consult attrs into evaluate."""
    from implement_admission.implement_ready_gate_resolve import (
        SkepticRatificationOutcome,
    )

    captured: dict[str, object] = {}

    def _capture_evaluate(**kwargs: object) -> object:
        captured["consult_provenance_record"] = kwargs.get("consult_provenance_record")
        return ImplementReadyVerdict(admitted=True, assertion_id=1)

    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_entity_get",
        lambda **_: _entity_with_recon_waiver(spec_sha256=_CURRENT_SPEC_HASH),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_assertion_get",
        lambda **_: _ready_assertion(),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.read_dense_spec_text",
        lambda _: SPEC_TEXT_VALID,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.resolve_skeptic_ratification",
        lambda **_: SkepticRatificationOutcome(ratified=False),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.evaluate_implement_ready",
        _capture_evaluate,
    )
    record = {"todo": "todo:waiver-test", "verdict": "ADMIT"}
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.load_todo_consult_provenance",
        lambda todo_id, **_: record if todo_id == "todo:waiver-test" else None,
    )

    verdict = _evaluate_from_persisted(
        entity_id="todo:waiver-test",
        prepared=_gate_distill_prepared(),
    )
    assert verdict.admitted is True
    assert captured == {"consult_provenance_record": record}


@pytest.mark.offline
def test_evaluate_from_persisted_rejects_missing_consult_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from implement_admission.implement_ready_gate_resolve import (
        SkepticRatificationOutcome,
    )

    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_entity_get",
        lambda **_: _entity_with_recon_waiver(
            spec_sha256=_CURRENT_SPEC_HASH,
            include_consult=False,
        ),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_assertion_get",
        lambda **_: _ready_assertion(),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.read_dense_spec_text",
        lambda _: SPEC_TEXT_VALID,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.resolve_skeptic_ratification",
        lambda **_: SkepticRatificationOutcome(ratified=False),
    )

    verdict = _evaluate_from_persisted(
        entity_id="todo:waiver-test",
        prepared=_gate_distill_prepared(),
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_consult_provenance_missing"


@pytest.mark.offline
def test_evaluate_from_persisted_rejects_stale_recon_waiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from implement_admission.implement_ready_gate_resolve import (
        SkepticRatificationOutcome,
    )

    emitted: list[dict] = []

    def _capture_stale_event(**kwargs: object) -> None:
        emitted.append(dict(kwargs))

    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_entity_get",
        lambda **_: _entity_with_recon_waiver(spec_sha256=_STALE_SPEC_HASH),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_assertion_get",
        lambda **_: _ready_assertion(),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.read_dense_spec_text",
        lambda _: SPEC_TEXT_VALID,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.resolve_skeptic_ratification",
        lambda **_: SkepticRatificationOutcome(ratified=False),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.cortex_implement_recon_waived",
        _capture_stale_event,
    )

    verdict = _evaluate_from_persisted(
        entity_id="todo:waiver-test",
        prepared=_gate_distill_prepared(),
    )
    assert verdict.admitted is False
    assert verdict.code == "skeptic_pass_missing"
    assert len(emitted) == 1
    assert emitted[0]["stale"] is True
    assert emitted[0]["stale_reason"] == "spec_sha256_mismatch"


@pytest.mark.offline
def test_evaluate_from_persisted_fail_closed_when_spec_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from implement_admission.implement_ready_gate_resolve import (
        SkepticRatificationOutcome,
    )

    captured: dict[str, object] = {}
    original_evaluate = __import__(
        "implement_admission.implement_ready",
        fromlist=["evaluate_implement_ready"],
    ).evaluate_implement_ready

    def _capture_evaluate(**kwargs: object) -> object:
        captured["recon_waived"] = kwargs.get("recon_waived")
        return original_evaluate(**kwargs)

    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_entity_get",
        lambda **_: _entity_with_recon_waiver(spec_sha256=_CURRENT_SPEC_HASH),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl._op_assertion_get",
        lambda **_: _ready_assertion(),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.read_dense_spec_text",
        lambda _: None,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.resolve_skeptic_ratification",
        lambda **_: SkepticRatificationOutcome(ratified=False),
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._todo_gate_distillation_impl.evaluate_implement_ready",
        _capture_evaluate,
    )

    verdict = _evaluate_from_persisted(
        entity_id="todo:waiver-test",
        prepared=_gate_distill_prepared(),
    )
    assert verdict.admitted is False
    assert captured["recon_waived"] is False
