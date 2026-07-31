"""Unit tests for the mechanical dense-spec schema validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.dense_spec_schema import (
    DENSE_SPEC_RE,
    dense_spec_hash_uri,
    dense_spec_sha256,
    spec_basename,
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


@pytest.mark.offline
@pytest.mark.parametrize(
    "uri",
    [
        "tasks/specs/foo-bar.md",
        "notes/system/specs/foo-bar.md",
        "workspaces://universal-llm-gateway/tasks/specs/foo-bar.md",
        "cortex://notes/system/specs/foo-bar.md",
    ],
)
def test_dense_spec_re_dual_accept(uri: str) -> None:
    assert DENSE_SPEC_RE.search(uri) is not None
    assert spec_basename(uri) == "foo-bar.md"


@pytest.mark.offline
def test_dense_spec_re_rejects_non_spec_paths() -> None:
    assert DENSE_SPEC_RE.search("tasks/other/foo.md") is None
    assert spec_basename("packet:tmp/reviews/packet.md") is None


@pytest.mark.offline
def test_missing_section_error_includes_accepted_pattern() -> None:
    """Friction 21176: error reason must name accepted keyword alternation per
    missing section, not just the canonical key (e.g. 'forks' alone is opaque
    — the author needs 'bound design | fork table | …' to write a passing heading).
    """
    verdict = validate_dense_spec(_SPARSE_SPEC)
    assert verdict.passed is False
    assert verdict.code == "dense_spec_sections_missing"
    assert verdict.reason is not None
    # 'forks' key → accepted patterns should appear in the reason
    assert "bound design" in verdict.reason or "fork table" in verdict.reason
    # reasoning_trace key → tag-block note should appear
    assert "<reasoning_trace>" in verdict.reason


@pytest.mark.offline
def test_stray_fence_triggers_diagnostic_hint() -> None:
    """Friction 21176: mid-line triple-backtick (not at line start) toggles the
    DOTALL fence stripper and can silently swallow required headers.  The error
    should include a stray-fence warning so the author knows to look for it.
    """
    text = """\
# Spec with stray fence

Mention of ``` syntax mid-line triggers DOTALL swallow.

## 1. Problem
Problem section.
"""
    verdict = validate_dense_spec(text)
    assert verdict.passed is False
    assert verdict.reason is not None
    assert "stray" in verdict.reason or "non-line-start" in verdict.reason
