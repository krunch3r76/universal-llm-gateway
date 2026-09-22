"""Plan-mode closeout predicate (R1 §5 / AC5–AC8)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from implement_admission.plan_closeout_fields import (
    authority_fork_from_open_forks,
    merge_open_forks,
    open_fork_entries_from_spec,
)
from implement_admission.spec import CloseoutStatus, WorkOutcome
from implement_admission.test_dense_spec_schema import _VALID_SPEC
from services.git_integration_worker.cursor_sdk_mode import (
    PlanCloseoutPredicate,
    apply_plan_mode_closeout_gate,
    plan_closeout_verdict,
)

pytestmark = pytest.mark.offline

_BASE = PlanCloseoutPredicate(
    has_artifacts=True,
    open_forks_key_present=True,
    open_forks=[],
    spec_sha256="abc123",
    dense_spec_valid=True,
    files_expected=["a.py"],
    acceptance_criteria=["AC1"],
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("has_artifacts", False),
        ("open_forks_key_present", False),
        ("open_forks", [{"id": "f1", "question": "q", "kind": "design"}]),
        ("spec_sha256", None),
        ("dense_spec_valid", False),
        ("files_expected", []),
        ("acceptance_criteria", []),
    ],
)
def test_plan_complete_flips_one_conjunct(field: str, value: object) -> None:
    inputs = replace(_BASE, **{field: value})
    assert plan_closeout_verdict(inputs) == "PARTIAL"


def test_plan_complete_all_conjuncts() -> None:
    assert plan_closeout_verdict(_BASE) == "PLAN_COMPLETE"


def test_open_forks_absent_key_is_partial() -> None:
    status, _, landed, _, verdict = apply_plan_mode_closeout_gate(
        sdk_mode="plan",
        status=CloseoutStatus.COMPLETE,
        work_outcome=WorkOutcome.SHIPPED,
        landed=True,
        deviations=[],
        artifact_paths=["cortex://notes/system/specs/x.md"],
        offgit_deliverable_uris=None,
    )
    assert status == CloseoutStatus.PARTIAL
    assert landed is None
    assert verdict == "PARTIAL"


def test_open_marker_in_spec_yields_design_fork() -> None:
    text = _VALID_SPEC + "\nOPEN: choose storage backend\n"
    forks = merge_open_forks(None, text)
    assert len(forks) == 1
    assert forks[0]["kind"] == "design"


def test_authority_fork_detected() -> None:
    forks = [{"id": "a1", "question": "delete prod?", "kind": "authority"}]
    assert authority_fork_from_open_forks(forks) is True
    assert authority_fork_from_open_forks([]) is False


def test_open_fork_entries_ignore_backticks() -> None:
    text = _VALID_SPEC + "\nInline `OPEN:` token in backticks.\n"
    assert open_fork_entries_from_spec(text) == []


def test_deleted_handoff_modules_raise_module_not_found() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("implement_admission.plan_implement_handoff")
    with pytest.raises(ModuleNotFoundError):
        __import__(
            "services.git_integration_worker.cursor_sdk_plan_handoff_witness",
            fromlist=["x"],
        )
