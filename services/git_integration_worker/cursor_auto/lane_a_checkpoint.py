"""Lane-A authored-path attribution and tree residue derivation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout import (
    capture_wt_baseline,
    changed_paths,
)

_TREE_RESIDUE_RE = re.compile(r"(?im)^tree_residue:\s*(\d+)\b")


@dataclass(frozen=True)
class AuthoredPathProbe:
    """Probe answer: dispatch baseline → exact authored-path set."""

    exact_at_dispatch: bool
    covers_nested_cursor_sdk: bool
    covers_attended_composer: bool
    detail: str


@dataclass(frozen=True)
class TreeResidueSnapshot:
    """Derived dirty-tree residue vs an episode authored-path set."""

    count: int
    authored_paths: tuple[str, ...]


def probe_authored_path_baseline() -> AuthoredPathProbe:
    """Record the code-verified probe answer for lane-A baseline attribution."""
    return AuthoredPathProbe(
        exact_at_dispatch=True,
        covers_nested_cursor_sdk=True,
        covers_attended_composer=False,
        detail=(
            "Per-dispatch admit ``wt_baseline`` (porcelain + content hashes at "
            "``capture_wt_baseline_with_hashes``) plus ``changed_paths`` yields an "
            "exact authored-path set (created/modified/deleted) for that dispatch "
            "episode on the shared checkout. Supersede revert uses the same delta "
            "(``cursor_sdk_revert.revert_dispatch_writes``). Nested cursor-sdk "
            "writes are covered because they land on the same repo between that "
            "dispatch's admit snapshot and closeout. Attended Composer / IDE "
            "writes outside a dispatch window are not registered — they appear "
            "as foreign dirty paths (ambient), not in the dispatch authored set."
        ),
    )


def authored_paths_for_dispatch(
    *,
    source_repo: Path,
    dispatch_id: str,
) -> tuple[str, ...]:
    """Return paths attributed to one dispatch via its admit baseline."""
    baseline = CursorDispatchLedger.instance().read_wt_baseline(
        dispatch_id=dispatch_id
    )
    if baseline is None:
        return ()
    change_set, _deviations = changed_paths(source_repo, baseline)
    return tuple(
        dict.fromkeys(
            (*change_set.created, *change_set.modified, *change_set.deleted)
        )
    )


def derive_tree_residue(
    *,
    source_repo: Path,
    dispatch_id: str,
    baseline: dict[str, Any] | None = None,
) -> TreeResidueSnapshot:
    """Count dirty paths not attributable to the dispatch authored-path set."""
    if baseline is None:
        baseline = CursorDispatchLedger.instance().read_wt_baseline(
            dispatch_id=dispatch_id
        )
    if baseline is None:
        authored: set[str] = set()
    else:
        change_set, _deviations = changed_paths(source_repo, baseline)
        authored = set(
            (*change_set.created, *change_set.modified, *change_set.deleted)
        )
    current = capture_wt_baseline(source_repo) or {}
    dirty_now = set(current.keys())
    residue_count = len(dirty_now - authored)
    return TreeResidueSnapshot(
        count=residue_count,
        authored_paths=tuple(sorted(authored)),
    )


def inject_tree_residue_line(body: str, *, count: int) -> str:
    """Replace or append infrastructure-derived ``tree_residue:`` on a CLOSEOUT."""
    line = f"tree_residue: {count}"
    if _TREE_RESIDUE_RE.search(body):
        return _TREE_RESIDUE_RE.sub(line, body, count=1)
    status_match = re.search(r"(?im)^status:\s*\S+\s*$", body)
    if status_match is None:
        return body.rstrip() + f"\n{line}\n"
    insert_at = status_match.end()
    return f"{body[:insert_at]}\n{line}{body[insert_at:]}"


__all__ = [
    "AuthoredPathProbe",
    "TreeResidueSnapshot",
    "authored_paths_for_dispatch",
    "derive_tree_residue",
    "inject_tree_residue_line",
    "probe_authored_path_baseline",
]
