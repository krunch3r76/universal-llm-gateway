"""Unit tests for code_ref git ancestry classification."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from universal_workspace import get_workspace_root
from universal_workspace import get_workspace_root as _get_workspace_root

from deploy_identity.code_ref_relation import (
    _resolve_commit_sha,
    code_ref_relation,
    code_ref_satisfied,
)
from deploy_identity.hub_git_repo import git_resolution_roots


@pytest.fixture(autouse=True)
def _clear_workspace_caches() -> None:
    _get_workspace_root.cache_clear()
    git_resolution_roots.cache_clear()
    yield
    _get_workspace_root.cache_clear()
    git_resolution_roots.cache_clear()


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


def _master_tip() -> str:
    root = get_workspace_root()
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "master^{commit}"],
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


def test_code_ref_relation_master_symbol_equal_to_tip() -> None:
    tip = _master_tip()
    assert code_ref_relation("master", tip) == "equal"
    assert code_ref_satisfied("master", tip)


def test_code_ref_relation_master_symbol_descendant_of_observed() -> None:
    root = get_workspace_root()
    _master_tip()
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "master~1"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip("master has no parent in this checkout")
    ancestor = proc.stdout.strip()
    assert code_ref_relation("master", ancestor) == "descendant-of-observed"
    assert not code_ref_satisfied("master", ancestor)


def test_code_ref_relation_unresolvable_ref() -> None:
    head = _head()
    assert code_ref_relation("not-a-real-ref-xyz", head) == "ref_unresolvable"


def test_code_ref_relation_symbolic_ref_without_git_is_ref_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Container-style WORKSPACE_ROOT (no .git) must not classify as unrelated."""
    head = _head()
    (tmp_path / "services").mkdir()
    (tmp_path / "libs").mkdir()
    (tmp_path / "config" / "models").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    _get_workspace_root.cache_clear()
    git_resolution_roots.cache_clear()
    monkeypatch.setattr(
        "deploy_identity.code_ref_relation.git_resolution_roots",
        lambda: (),
    )
    assert code_ref_relation("master", head) == "ref_unresolvable"


def test_code_ref_relation_master_via_hub_git_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hub = _get_workspace_root()
    tip = _master_tip()
    (tmp_path / "services").mkdir()
    (tmp_path / "libs").mkdir()
    (tmp_path / "config" / "models").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    _get_workspace_root.cache_clear()
    git_resolution_roots.cache_clear()
    monkeypatch.setattr(
        "deploy_identity.code_ref_relation.git_resolution_roots",
        lambda: (hub.resolve(),),
    )
    assert code_ref_relation("master", tip) == "equal"


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
