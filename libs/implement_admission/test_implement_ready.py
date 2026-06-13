"""Unit tests for the pure implement-readiness evaluator."""

from __future__ import annotations

import pytest

from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.implement_ready import evaluate_implement_ready

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
def test_judgment_required_valid_assertion_admits() -> None:
    verdict = evaluate_implement_ready(**_judgment_ready_kwargs())
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
