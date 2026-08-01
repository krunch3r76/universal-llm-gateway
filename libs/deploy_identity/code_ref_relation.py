"""Git ancestry between a propagation ``code_ref`` and an observed ``code_version``."""

from __future__ import annotations

import subprocess
from typing import Literal

from universal_workspace import get_workspace_root

CodeRefRelation = Literal[
    "equal", "ancestor", "descendant-of-observed", "unrelated", "unknown"
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


def code_ref_relation(code_ref: str, observed: str) -> CodeRefRelation:
    """Classify how ``code_ref`` relates to the live ``observed`` SHA."""
    ref = _normalize_sha(code_ref)
    live = _normalize_sha(observed)
    if not _is_sha(ref) or not _is_sha(live):
        return "unrelated"
    if ref == live:
        return "equal"
    if _git_is_ancestor(ref, live):
        return "ancestor"
    if _git_is_ancestor(live, ref):
        return "descendant-of-observed"
    return "unrelated"


def code_ref_satisfied(code_ref: str, observed: str) -> bool:
    """True when at least the requested code is live (equal or ancestor)."""
    return code_ref_relation(code_ref, observed) in {"equal", "ancestor"}


def code_ref_relation_from_observed(
    code_ref: str, observed: str | None,
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
]
