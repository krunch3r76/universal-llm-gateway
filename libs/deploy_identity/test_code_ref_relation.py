"""Unit tests for code_ref git ancestry classification."""

from __future__ import annotations

import subprocess

import pytest

from deploy_identity.code_ref_relation import (
    _resolve_commit_sha,
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


def test_code_ref_relation_short_ref_equal_to_full_observed() -> None:
    """Unambiguous abbreviated ref of observed SHA must classify as equal, not ancestor."""
    head = _head()
    short = head[:8]
    assert _resolve_commit_sha(short) == head
    assert code_ref_relation(short, head) == "equal"
    assert code_ref_satisfied(short, head)


def test_code_ref_relation_ambiguous_prefix_not_equal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefix that does not resolve unambiguously must not classify as equal."""
    head = _head()
    short = head[:8]

    from deploy_identity.code_ref_relation import resolve_commit_sha as real_resolve

    def _ambiguous_ref(value: str) -> str | None:
        if value == short:
            return None
        return real_resolve(value)

    monkeypatch.setattr(
        "deploy_identity.code_ref_relation.resolve_commit_sha",
        _ambiguous_ref,
    )
    assert code_ref_relation(short, head) == "unrelated"
