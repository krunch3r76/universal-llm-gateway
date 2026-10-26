"""Git ancestry between a propagation ``code_ref`` and an observed ``code_version``."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

from universal_workspace import get_workspace_root

from deploy_identity.hub_git_repo import git_resolution_roots

CodeRefRelation = Literal[
    "equal",
    "ancestor",
    "descendant-of-observed",
    "unrelated",
    "ref_unresolvable",
    "unknown",
]

_SHA40_MIN = 7


def _normalize_sha(value: str) -> str:
    return str(value or "").strip().lower()


def _is_sha(value: str) -> bool:
    normalized = _normalize_sha(value)
    return len(normalized) >= _SHA40_MIN and all(
        char in "0123456789abcdef" for char in normalized
    )


def _git_is_ancestor(ancestor: str, descendant: str) -> bool:
    try:
        root = get_workspace_root()
    except RuntimeError:
        return False
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    return proc.returncode == 0


def _resolve_commit_sha_at_root(root: Path, value: str) -> str | None:
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "rev-parse",
            "--verify",
            f"{value}^{{commit}}",
        ],
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    if proc.returncode != 0:
        return None
    resolved = _normalize_sha(proc.stdout.strip())
    if len(resolved) != 40 or not all(char in "0123456789abcdef" for char in resolved):
        return None
    return resolved


def resolve_commit_sha(value: str) -> str | None:
    """Resolve a ref to a full 40-char commit SHA, or None if ambiguous/missing."""
    roots = list(git_resolution_roots())
    if not roots:
        try:
            roots = [get_workspace_root()]
        except RuntimeError:
            return None
    for root in roots:
        resolved = _resolve_commit_sha_at_root(root, value)
        if resolved is not None:
            return resolved
    return None


# Compat alias — call sites historically used the private name.
_resolve_commit_sha = resolve_commit_sha


def code_ref_relation(code_ref: str, observed: str) -> CodeRefRelation:
    """Classify how ``code_ref`` relates to the live ``observed`` SHA."""
    ref_in = str(code_ref or "").strip()
    live_in = str(observed or "").strip()
    ref = _normalize_sha(ref_in)
    live = _normalize_sha(live_in)
    if not _is_sha(ref):
        resolved = resolve_commit_sha(ref_in)
        if resolved is None:
            return "ref_unresolvable"
        ref = resolved
    if not _is_sha(live):
        resolved = resolve_commit_sha(live_in)
        if resolved is None:
            return "ref_unresolvable"
        live = resolved
    resolved_ref = resolve_commit_sha(ref)
    resolved_live = resolve_commit_sha(live)
    if resolved_ref is not None and resolved_live is not None:
        if resolved_ref == resolved_live:
            return "equal"
        if _git_is_ancestor(resolved_ref, resolved_live):
            return "ancestor"
        if _git_is_ancestor(resolved_live, resolved_ref):
            return "descendant-of-observed"
        return "unrelated"
    if ref == live:
        return "equal"
    return "unrelated"


def code_ref_satisfied(code_ref: str, observed: str) -> bool:
    """True when at least the requested code is live (equal or ancestor)."""
    return code_ref_relation(code_ref, observed) in {"equal", "ancestor"}


def code_ref_relation_from_observed(
    code_ref: str,
    observed: str | None,
) -> CodeRefRelation:
    """Classify relation, or ``unknown`` when no observed version was returned."""
    if not isinstance(observed, str) or not observed.strip():
        return "unknown"
    return code_ref_relation(code_ref, observed)


__all__ = [
    "CodeRefRelation",
    "code_ref_relation",
    "code_ref_relation_from_observed",
    "code_ref_satisfied",
    "resolve_commit_sha",
]
