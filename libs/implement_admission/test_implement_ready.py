"""Unit tests for the pure implement-readiness evaluator."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.implement_ready import (
    evaluate_implement_ready,
    implement_ready_predicate,
)
from implement_admission.implement_ready_pin import (
    REQUESTED,
    RESOLVED_PREDICATE,
    UNRESOLVED,
    implement_ready_pin_reason,
    resolve_implement_ready_pin,
)

_NOW = "2026-06-12T12:00:00+00:00"
_TODO = "todo:densification-implement-admission-gate"
_SPEC = "tasks/specs/densification-implement-admission-gate.md"

_VALID_DENSE_SPEC = """\
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

No fork remains OPEN.

</reasoning_trace>
"""

_SPARSE_SPEC = """\
# Sparse spec

## 1. Problem

Only problem section.
"""


def _valid_assertion(
    spec_text: str = _VALID_DENSE_SPEC,
    **overrides: object,
) -> dict:
    base = {
        "entity_id": _TODO,
        "superseded_by": None,
        "valid_until": None,
        "evidence_uris": [
            f"workspaces://universal-llm-gateway/{_SPEC}",
            dense_spec_hash_uri(spec_text),
        ],
    }
    base.update(overrides)
    return base


def _judgment_ready_kwargs(**overrides: object) -> dict:
    base = {
        "todo_id": _TODO,
        "density_triage": "judgment_required",
        "source_uri": _SPEC,
        "implement_ready_assertion_id": 17295,
        "assertion": _valid_assertion(),
        "now_iso": _NOW,
        "dense_spec_uri": f"workspaces://universal-llm-gateway/{_SPEC}",
        "dense_spec_text": _VALID_DENSE_SPEC,
        "files_expected": ["module.py"],
        "acceptance_criteria": ["Validator passes dense specs."],
        "entity_name": "densification-implement-admission-gate",
        "skeptic_ratified": True,
    }
    base.update(overrides)
    return base


@pytest.mark.offline
def test_mechanical_admits() -> None:
    verdict = evaluate_implement_ready(
        todo_id=_TODO,
        density_triage="mechanical",
        source_uri=_SPEC,
        implement_ready_assertion_id=None,
        assertion=None,
        now_iso=_NOW,
    )
    assert verdict.admitted is True
    assert verdict.code is None


@pytest.mark.offline
@pytest.mark.parametrize("triage", [None, "", "unknown", "bogus"])
def test_unknown_triage_rejects(triage: str | None) -> None:
    verdict = evaluate_implement_ready(
        todo_id=_TODO,
        density_triage=triage,
        source_uri=_SPEC,
        implement_ready_assertion_id=99,
        assertion=_valid_assertion(),
        now_iso=_NOW,
        dense_spec_text=_VALID_DENSE_SPEC,
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_triage_unknown"
    assert verdict.reason is not None
    assert "mechanical (bypass implement-ready gates)" in verdict.reason
    assert "judgment_required" in verdict.reason
    assert "recon_pending" in verdict.reason


@pytest.mark.offline
def test_judgment_required_without_assertion_id_rejects() -> None:
    verdict = evaluate_implement_ready(
        todo_id=_TODO,
        density_triage="judgment_required",
        source_uri=_SPEC,
        implement_ready_assertion_id=None,
        assertion=None,
        now_iso=_NOW,
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_not_ready_judgment_required"


@pytest.mark.offline
def test_judgment_required_missing_consult_fields_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(consult_provenance_record=None),
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_consult_provenance_missing"
    assert "todo-keyed" in (verdict.reason or "")


def _valid_record(tmp_path: Path) -> dict:
    rel = Path("notes/system/threads/archives/ready.md")
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = b"ready archive\n"
    path.write_bytes(body)
    return {
        "todo": _TODO,
        "consult_thread": "agent-bus:8801#12",
        "verdict": "ADMIT",
        "adjudication_assertion_id": 1,
        "consultant_model": "claude-fable-5-1",
        "consultant_effort": "high",
        "consultant_substrate": "cdp",
        "archive_uri": f"cortex://{rel.as_posix()}",
        "archive_sha256": hashlib.sha256(body).hexdigest(),
        "satellite_execution_id": "sat-1",
        "stargate_execution_id": "sg-1",
        "written_by": "test",
        "written_at": _NOW,
    }


@pytest.mark.offline
def test_judgment_required_with_consult_fields_admits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(consult_provenance_record=_valid_record(tmp_path)),
    )
    assert verdict.admitted is True
    assert verdict.assertion_id == 17295


@pytest.mark.offline
def test_judgment_required_valid_assertion_admits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(consult_provenance_record=_valid_record(tmp_path)),
    )
    assert verdict.admitted is True
    assert verdict.assertion_id == 17295


@pytest.mark.offline
def test_missing_assertion_row_rejects() -> None:
    verdict = evaluate_implement_ready(
        todo_id=_TODO,
        density_triage="judgment_required",
        source_uri=_SPEC,
        implement_ready_assertion_id=1,
        assertion=None,
        now_iso=_NOW,
    )
    assert verdict.code == "implement_ready_assertion_missing"


@pytest.mark.offline
def test_entity_mismatch_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(assertion=_valid_assertion(entity_id="todo:other")),
    )
    assert verdict.code == "implement_ready_assertion_entity_mismatch"


@pytest.mark.offline
def test_superseded_assertion_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(assertion=_valid_assertion(superseded_by=2)),
    )
    assert verdict.code == "implement_ready_assertion_inactive"


@pytest.mark.offline
def test_expired_assertion_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(
            assertion=_valid_assertion(valid_until="2020-01-01T00:00:00+00:00"),
        ),
    )
    assert verdict.code == "implement_ready_assertion_inactive"


@pytest.mark.offline
def test_missing_dense_spec_rejects() -> None:
    verdict = evaluate_implement_ready(
        todo_id=_TODO,
        density_triage="judgment_required",
        source_uri=None,
        implement_ready_assertion_id=1,
        assertion=_valid_assertion(),
        now_iso=_NOW,
        dense_spec_text=_VALID_DENSE_SPEC,
    )
    assert verdict.code == "implement_not_ready_no_dense_spec"


@pytest.mark.offline
def test_spec_uncited_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(
            assertion=_valid_assertion(
                evidence_uris=[
                    "workspaces://universal-llm-gateway/tasks/specs/other.md",
                    dense_spec_hash_uri(_VALID_DENSE_SPEC),
                ],
            ),
        ),
    )
    assert verdict.code == "implement_ready_assertion_spec_uncited"


@pytest.mark.offline
def test_sparse_spec_content_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(dense_spec_text=_SPARSE_SPEC),
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_spec_not_dense"


@pytest.mark.offline
def test_unreadable_spec_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(dense_spec_text=None),
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_spec_unreadable"


@pytest.mark.offline
def test_hash_drift_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(
            assertion=_valid_assertion(
                evidence_uris=[f"workspaces://universal-llm-gateway/{_SPEC}"],
            ),
        ),
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_spec_drifted_since_ready"


@pytest.mark.offline
def test_empty_files_expected_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(files_expected=[]),
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_attrs_unpopulated"


@pytest.mark.offline
def test_default_acceptance_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(
            acceptance_criteria=[f"Complete work for {_TODO}"],
        ),
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_attrs_unpopulated"


@pytest.mark.offline
def test_empty_acceptance_rejects() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(acceptance_criteria=[]),
    )
    assert verdict.admitted is False
    assert verdict.code == "implement_attrs_unpopulated"


class _StubCortex:
    """Minimal ImplementReadyCortex over an in-memory assertion table."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def entity_get(self, entity_id: str, **kwargs: object) -> dict:
        return {"id": entity_id}

    def assertion_get(self, assertion_id: int) -> dict:
        for row in self._rows:
            if row.get("id") == assertion_id:
                return row
        return {"error": f"not found: {assertion_id}"}

    def assertions(self, entity_id: str, **kwargs: object) -> dict:
        return {"items": [r for r in self._rows if r.get("entity_id") == entity_id]}


def _row(
    aid: int,
    *,
    predicate: str | None,
    valid_until: str | None = None,
    confidence: str = "confirmed",
    observed_at: str = "2026-06-12T11:00:00+00:00",
    entity_id: str = _TODO,
) -> dict:
    return {
        "id": aid,
        "entity_id": entity_id,
        "predicate_form": predicate,
        "confidence": confidence,
        "superseded_by": None,
        "valid_until": valid_until,
        "observed_at": observed_at,
        "evidence_uris": [_SPEC],
    }


_EXPIRED = "2026-06-12T11:59:00+00:00"


@pytest.mark.offline
def test_pin_resolves_newest_active_predicate_row_when_pin_is_inactive() -> None:
    # The named regression: a retracted pin must not latch admission shut
    # while an active, correctly-predicated declaration sits on the entity.
    stale = _row(35286, predicate=f"has_attribute({_TODO}, implement_ready)",
                 valid_until=_EXPIRED)
    fresh = _row(35315, predicate=implement_ready_predicate(_TODO),
                 observed_at="2026-06-12T11:30:00+00:00")
    pin = resolve_implement_ready_pin(
        todo_id=_TODO,
        cortex=_StubCortex([stale, fresh]),
        now_iso=_NOW,
        pinned_id=35286,
        pinned_assertion=stale,
    )
    assert pin.assertion_id == 35315
    assert pin.basis == RESOLVED_PREDICATE


@pytest.mark.offline
def test_pin_honors_requested_id_when_confirmed_active_and_on_entity() -> None:
    stale = _row(35286, predicate=f"has_attribute({_TODO}, implement_ready)",
                 valid_until=_EXPIRED)
    requested = _row(35315, predicate=implement_ready_predicate(_TODO))
    pin = resolve_implement_ready_pin(
        todo_id=_TODO,
        cortex=_StubCortex([stale, requested]),
        now_iso=_NOW,
        pinned_id=35286,
        pinned_assertion=stale,
        requested_id=35315,
    )
    assert pin.assertion_id == 35315
    assert pin.basis == REQUESTED
    assert pin.rejected_requested_id is None


@pytest.mark.offline
@pytest.mark.parametrize(
    "bad",
    [
        _row(700, predicate=None, entity_id="todo:other"),
        _row(700, predicate=None, confidence="hypothesized"),
        _row(700, predicate=None, valid_until=_EXPIRED),
    ],
    ids=["other_entity", "not_confirmed", "inactive"],
)
def test_pin_refuses_requested_id_that_is_not_confirmed_active_on_entity(
    bad: dict,
) -> None:
    pin = resolve_implement_ready_pin(
        todo_id=_TODO,
        cortex=_StubCortex([bad]),
        now_iso=_NOW,
        pinned_id=None,
        pinned_assertion=None,
        requested_id=700,
    )
    assert pin.basis == UNRESOLVED
    assert pin.rejected_requested_id == 700
    reason = implement_ready_pin_reason(_TODO, pin=pin)
    assert implement_ready_predicate(_TODO) in reason
    assert "700 was not honoured" in reason


@pytest.mark.offline
def test_pin_reason_names_required_predicate_and_rejects_has_attribute_form() -> None:
    pin = resolve_implement_ready_pin(
        todo_id=_TODO,
        cortex=_StubCortex(
            [_row(35286, predicate=f"has_attribute({_TODO}, implement_ready)")]
        ),
        now_iso=_NOW,
        pinned_id=None,
        pinned_assertion=None,
    )
    assert pin.basis == UNRESOLVED
    reason = implement_ready_pin_reason(_TODO, pin=pin)
    assert f"status({_TODO}, implement_ready, current)" in reason
    assert "is not resolvable" in reason


@pytest.mark.offline
def test_inactive_verdict_reason_names_the_required_predicate() -> None:
    verdict = evaluate_implement_ready(
        **_judgment_ready_kwargs(assertion=_valid_assertion(superseded_by=2)),
    )
    assert verdict.code == "implement_ready_assertion_inactive"
    assert verdict.reason is not None
    assert implement_ready_predicate(_TODO) in verdict.reason
    assert "has_attribute" in verdict.reason
