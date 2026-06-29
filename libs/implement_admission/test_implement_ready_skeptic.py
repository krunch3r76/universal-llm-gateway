"""Skeptic-pass hard gate for material (judgment_required) implement admission."""

from __future__ import annotations

import pytest

from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.implement_ready import evaluate_implement_ready

_NOW = "2026-06-29T00:00:00+00:00"
_SPEC = "tasks/specs/sample.md"

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

- a.py

## 5. Bound design decisions / fork table

| Fork | Decision |
|---|---|
| 1 | resolved |

## 6. Implementation guidance

Build the gate.

## 7. Acceptance criteria

1. criterion one

## 8. Verification / quality gates

- pytest green

<reasoning_trace>

No fork remains OPEN.

</reasoning_trace>
"""


def _ready_kwargs(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "todo_id": "todo:sample",
        "density_triage": "judgment_required",
        "source_uri": _SPEC,
        "implement_ready_assertion_id": 1,
        "assertion": {
            "entity_id": "todo:sample",
            "evidence_uris": [_SPEC, dense_spec_hash_uri(_VALID_DENSE_SPEC)],
        },
        "now_iso": _NOW,
        "dense_spec_uri": _SPEC,
        "dense_spec_text": _VALID_DENSE_SPEC,
        "files_expected": ["a.py"],
        "acceptance_criteria": ["criterion one"],
        "entity_name": "Sample",
    }
    base.update(over)
    return base


@pytest.mark.offline
def test_mechanical_is_exempt_from_skeptic_gate() -> None:
    verdict = evaluate_implement_ready(
        **_ready_kwargs(density_triage="mechanical", skeptic_ratified=False)
    )
    assert verdict.admitted


@pytest.mark.offline
def test_material_without_skeptic_is_refused() -> None:
    # NOTE: this test exercises the gate ordering; if the spec/assertion checks
    # short-circuit first in your build, stub them as in _ready_kwargs so the
    # skeptic branch is reached. Expect skeptic_pass_missing once all prior
    # readiness holds.
    verdict = evaluate_implement_ready(**_ready_kwargs(skeptic_ratified=False))
    assert not verdict.admitted
    assert verdict.code == "skeptic_pass_missing"


@pytest.mark.offline
def test_material_with_skeptic_is_admitted() -> None:
    verdict = evaluate_implement_ready(**_ready_kwargs(skeptic_ratified=True))
    assert verdict.admitted
