"""Unit tests for the mechanical dense-spec schema validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.dense_spec_schema import (
    dense_spec_hash_uri,
    dense_spec_sha256,
    validate_dense_spec,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SELF_SPEC = _REPO_ROOT / "tasks/specs/dense-spec-schema.md"

_VALID_SPEC = """\
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


@pytest.mark.offline
def test_sparse_spec_rejects_with_missing_sections() -> None:
    verdict = validate_dense_spec(_SPARSE_SPEC)
    assert verdict.passed is False
    assert verdict.code == "dense_spec_sections_missing"
    assert len(verdict.missing_sections) >= 1
    assert "non_goals" in verdict.missing_sections


@pytest.mark.offline
@pytest.mark.parametrize(
    "marker",
    ["OPEN:", "Open:", "OPEN :"],
)
def test_live_open_fork_marker_rejects(marker: str) -> None:
    text = _VALID_SPEC.replace(
        "No fork remains OPEN.",
        f"Fork 1 — placement {marker} unresolved\n\nNo fork remains OPEN.",
    )
    verdict = validate_dense_spec(text)
    assert verdict.passed is False
    assert verdict.code == "dense_spec_open_forks"
    assert verdict.open_fork_markers >= 1


@pytest.mark.offline
def test_valid_spec_passes() -> None:
    verdict = validate_dense_spec(_VALID_SPEC)
    assert verdict.passed is True
    assert verdict.code is None
    assert verdict.missing_sections == ()
    assert verdict.open_fork_markers == 0


@pytest.mark.offline
def test_headings_inside_code_fences_do_not_count() -> None:
    text = """\
# Wrapped spec

```markdown
## 1. Problem
## 2. Non-goals
## 3. Source-of-truth
## 4. Touch-point inventory
## 5. Bound design decisions
## 6. Implementation guidance
## 7. Acceptance criteria
## 8. Verification
<reasoning_trace>
No fork remains OPEN.
</reasoning_trace>
```
"""
    verdict = validate_dense_spec(text)
    assert verdict.passed is False
    assert "problem" in verdict.missing_sections


@pytest.mark.offline
def test_marker_in_backticks_does_not_count() -> None:
    text = _VALID_SPEC + "\n\nInline `OPEN:` token in backticks.\n"
    verdict = validate_dense_spec(text)
    assert verdict.open_fork_markers == 0
    assert verdict.passed is True


@pytest.mark.offline
def test_reasoning_trace_inside_fence_does_not_count() -> None:
    text = """\
# Spec

## 1. Problem
Problem.

## 2. Non-goals
None.

## 3. Source-of-truth
Here.

## 4. Touch-point inventory
Here.

## 5. Bound design decisions
Here.

## 6. Implementation guidance
Here.

## 7. Acceptance criteria
Here.

## 8. Verification
Here.

```
<reasoning_trace>
No fork remains OPEN.
</reasoning_trace>
```
"""
    verdict = validate_dense_spec(text)
    assert verdict.passed is False
    assert "reasoning_trace" in verdict.missing_sections


@pytest.mark.offline
def test_dense_spec_hash_helpers() -> None:
    digest = dense_spec_sha256(_VALID_SPEC)
    assert len(digest) == 64
    assert digest == digest.lower()
    assert dense_spec_hash_uri(_VALID_SPEC) == f"spec_sha256:{digest}"


@pytest.mark.offline
def test_self_referential_spec_passes() -> None:
    text = _SELF_SPEC.read_text(encoding="utf-8")
    verdict = validate_dense_spec(text)
    assert verdict.passed is True, verdict.reason
