"""Lane selection at cursor-sdk admit (S2 + row-10 default-routing).

Nested omitted-lane inherits parent isolation, not the regime switch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

Lane = Literal["A", "B"]
LaneSelectionReason = Literal[
    "explicit",
    "regime",
    "contract_regime",
    "opt_out",
    "scope_veto",
    "read_only",
    "nest_inherit",
    "lane_bound",
    "auto_regime",
]

_IMPLEMENT_CLASS_CONTRACTS = frozenset({"implement", "light-bounded"})


class LaneScopeRefused(Exception):  # noqa: N818
    """Raised when explicit ``lane='B'`` is incompatible with ``files_expected`` scope."""


def wire_lane_explicit(req: CursorDispatchRequest) -> Lane | None:
    """Explicit lane from wire, including deprecated isolation aliases (LB-4)."""
    if req.lane is not None:
        return req.lane
    if req.worktree_isolated or req.worktree_path:
        return "B"
    return None


def _scope_violation_reason(path: str, repo: Path) -> str | None:
    """Classify why ``path`` fails single-repo scope, or None when it passes."""
    if path.startswith(("cortex://", "cortex:")):
        return "cortex:// reference, not a repo-relative path"
    if path.startswith("workspaces://"):
        return "workspaces:// reference, not a repo-relative path"
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            candidate.resolve().relative_to(repo)
        except ValueError:
            return "absolute path not under source_repo"
        return None
    if ".." in candidate.parts:
        return "parent-directory traversal (..)"
    return None


def first_scope_violation(
    files_expected: list[str], source_repo: Path
) -> tuple[str, str] | None:
    """First ``(path, reason)`` out of single-repo scope, or None when clean.

    Named-offender counterpart to ``scope_is_single_repo`` (friction a:31774) —
    the bool form collapses which token failed and why, forcing guesswork at
    the ``LaneScopeRefused`` call site.
    """
    if not files_expected:
        return None
    repo = source_repo.resolve()
    for raw in files_expected:
        path = raw.strip()
        if not path:
            continue
        reason = _scope_violation_reason(path, repo)
        if reason is not None:
            return path, reason
    return None


def scope_is_single_repo(files_expected: list[str], source_repo: Path) -> bool:
    """True when every expected path resolves under ``source_repo`` (or is repo-relative)."""
    return first_scope_violation(files_expected, source_repo) is None


def lane_selection_predicate(
    *,
    reason: LaneSelectionReason,
    contract: str | None,
    regime_active: bool,
) -> str:
    """Reconstructable selecting predicate for limb-2 attribution (row-10 D6)."""
    parts = [f"reason={reason}", f"regime_active={regime_active}"]
    if contract:
        parts.append(f"contract={contract}")
    return ";".join(parts)


def select_lane(
    *,
    req: CursorDispatchRequest,
    regime_active: bool,
    source_repo: Path,
    files_expected: list[str],
    contract: str | None = None,
    lane_worktree: Path | None = None,
    parent_isolated: bool | None = None,
) -> tuple[Lane, list[str], LaneSelectionReason]:
    """Choose admit lane; row-10 routes implement-class contracts to Lane-B when regime is on.

    Nested admits with omitted ``lane`` inherit the parent's isolation
    (``parent_isolated``), not the regime switch — regime must not label a
    shared-master nest as Lane-B. The same inherit applies to ``resume_of``.
    Explicit ``lane='B'`` still selects B; admit then refuses if the worktree
    is missing.
    """
    advisories: list[str] = []
    if req.nest_under or req.resume_of:
        explicit = wire_lane_explicit(req)
        if explicit == "B":
            return "B", advisories, "nest_inherit"
        if explicit == "A":
            return "A", advisories, "nest_inherit"
        if parent_isolated is True:
            return "B", advisories, "nest_inherit"
        return "A", advisories, "nest_inherit"

    explicit = wire_lane_explicit(req)

    if req.worktree_isolated and req.lane is None:
        advisories.append("worktree_isolated is deprecated; use lane='B'")
    if req.worktree_path and req.lane is None:
        advisories.append("worktree_path is deprecated; use lane='B'")

    if explicit == "A":
        reason: LaneSelectionReason = "opt_out" if regime_active else "explicit"
        return "A", advisories, reason

    if explicit == "B":
        violation = first_scope_violation(files_expected, source_repo)
        if violation is not None:
            offending_path, violation_reason = violation
            raise LaneScopeRefused(
                f"lane='B' refused: files_expected path {offending_path!r} is out "
                f"of scope ({violation_reason}). Declare an explicit "
                "`files_expected:` YAML front-matter list in the packet to "
                "override packet-scan derivation."
            )
        return "B", advisories, "explicit"

    if not files_expected:
        if lane_worktree is not None:
            return "B", advisories, "lane_bound"
        if (
            req.admitted_via == "cursor-auto"
            and (contract or "").lower() == "pure-mechanical"
        ):
            return "B", advisories, "auto_regime"
        return "A", advisories, "opt_out"

    if not scope_is_single_repo(files_expected, source_repo):
        return "A", advisories, "scope_veto"

    if regime_active:
        normalized = (contract or "").lower()
        if normalized in _IMPLEMENT_CLASS_CONTRACTS:
            return "B", advisories, "contract_regime"
        return "B", advisories, "regime"

    return "A", advisories, "opt_out"


__all__ = [
    "Lane",
    "LaneScopeRefused",
    "LaneSelectionReason",
    "first_scope_violation",
    "lane_selection_predicate",
    "scope_is_single_repo",
    "select_lane",
    "wire_lane_explicit",
]
