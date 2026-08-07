"""Unit tests for propagation version-satisfaction case machine."""

from __future__ import annotations

import subprocess

import pytest
from universal_workspace import get_workspace_root

from charter_runner_store.propagation_version_satisfaction import (
    DEFER_ANCESTRY_SATISFIED,
    DEFER_UNRELATED,
    DEFER_UNRELATED_OR_UNRESOLVABLE,
    classify_version_satisfaction,
)


def _head() -> str:
    root = get_workspace_root()
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _ancestor_of_head() -> str:
    root = get_workspace_root()
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def test_case_i_exact_match() -> None:
    head = _head()
    result = classify_version_satisfaction(head, head)
    assert result.case == "exact_match"
    assert result.relation == "equal"
    assert "proven" in result.reader_entitlement


def test_case_i_short_ref_exact_match() -> None:
    """Short code_ref against its own full observed SHA must be case (i), not (ii)."""
    head = _head()
    short = head[:8]
    result = classify_version_satisfaction(short, head)
    assert result.case == "exact_match"
    assert result.relation == "equal"
    assert result.case != "ancestry_satisfied"


def test_case_ii_ancestry_satisfied() -> None:
    ancestor = _ancestor_of_head()
    head = _head()
    result = classify_version_satisfaction(ancestor, head)
    assert result.case == "ancestry_satisfied"
    assert result.relation == "ancestor"
    assert result.case != "exact_match"
    assert "transitively satisfied" in result.reader_entitlement
    assert "READ-CAVEAT" in result.reader_entitlement
    assert "observe_code_ref_live" in result.reader_entitlement


@pytest.mark.real_git_resolve
def test_case_unresolvable_working_token() -> None:
    head = _head()
    result = classify_version_satisfaction("working", head)
    assert result.case == "unresolvable"
    assert "not a git commit object" in result.reader_entitlement


@pytest.mark.real_git_resolve
def test_case_unresolvable_mangled_hex() -> None:
    head = _head()
    mangled = "1812220dff49a0e0a5b1f36cad0bca1ad1fd8"
    assert len(mangled) == 37
    result = classify_version_satisfaction(mangled, head)
    assert result.case == "unresolvable"


def test_case_unrelated_valid_off_line() -> None:
    unrelated = "0" * 40
    head = _head()
    result = classify_version_satisfaction(unrelated, head)
    assert result.case == "unrelated"
    assert result.relation == "unrelated"
    assert "no git ancestry" in result.reader_entitlement


def test_case_unrelated_unknown_observed() -> None:
    head = _head()
    result = classify_version_satisfaction(head, None)
    assert result.case == "unrelated"
    assert result.relation == "unknown"


def test_stale_code_is_distinct_from_unrelated() -> None:
    ancestor = _ancestor_of_head()
    head = _head()
    result = classify_version_satisfaction(head, ancestor)
    assert result.case == "stale_code"
    assert result.relation == "descendant-of-observed"
    assert result.case != "unrelated"
    assert result.case != "unresolvable"


def test_defer_tokens_are_stable() -> None:
    assert DEFER_ANCESTRY_SATISFIED == "version_superseded_by_newer_code"
    assert DEFER_UNRELATED == "version_relation_unrelated"
    assert DEFER_UNRELATED_OR_UNRESOLVABLE == "version_relation_unrelated_or_unresolvable"
