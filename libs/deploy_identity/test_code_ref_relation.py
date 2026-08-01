"""Unit tests for code_ref git ancestry classification."""

from __future__ import annotations

import subprocess

from deploy_identity.code_ref_relation import (
    code_ref_relation,
    code_ref_satisfied,
)
from universal_workspace import get_workspace_root


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


def test_code_ref_relation_equal() -> None:
    head = _head()
    assert code_ref_relation(head, head) == "equal"
    assert code_ref_satisfied(head, head)


def test_code_ref_relation_ancestor_pass() -> None:
    ancestor = _ancestor_of_head()
    head = _head()
    assert code_ref_relation(ancestor, head) == "ancestor"
    assert code_ref_satisfied(ancestor, head)


def test_code_ref_relation_unrelated_fail() -> None:
    unrelated = "0" * 40
    head = _head()
    assert code_ref_relation(unrelated, head) == "unrelated"
    assert not code_ref_satisfied(unrelated, head)


def test_code_ref_relation_descendant_of_observed_fail() -> None:
    ancestor = _ancestor_of_head()
    head = _head()
    assert code_ref_relation(head, ancestor) == "descendant-of-observed"
    assert not code_ref_satisfied(head, ancestor)
