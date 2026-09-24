"""Goal escalation for gear-3 liaison ticker — dispatch vs STAY.

When an open goal needs a repo write or a design/judgment fork the liaison
seat cannot bind, escalate down the ladder instead of STAY or needs-attended.
Ladder: cursor-auto (repo write) → cdp/opus-5.5 (independent check) →
cdp/fable-5.1 (design/judgment last resort). Human operator is not a routine rung.
Only an explicit ``OPERATOR_GATE`` may terminate as needs-attended.
"""

from __future__ import annotations

from typing import Literal

GoalKind = Literal["repo_write", "design"]

_REPO_WRITE_MARKERS = (
    "[implement]",
    "executor_lane: implement",
    "executor_lane=implement",
    "repo write",
    "repo-write",
    "files_expected",
    "contract=implement",
    "contract: implement",
    "pure-mechanical",
)

_DESIGN_MARKERS = (
    "design",
    "judgment",
    "discriminator",
    "[consult:judgment_gap]",
    "architecture",
    "executor_lane: judgment",
    "executor_lane=judgment",
    "consult_role: judgment",
    "consult_role: judgment_gap",
)


def classify_goal(row: str) -> GoalKind | None:
    """Return goal kind when the NOW row needs ladder dispatch, else ``None``."""
    text = (row or "").strip()
    if not text:
        return None
    lower = text.lower()
    if any(marker.lower() in lower for marker in _REPO_WRITE_MARKERS):
        return "repo_write"
    if any(marker.lower() in lower for marker in _DESIGN_MARKERS):
        return "design"
    return None


def escalation_target(kind: GoalKind) -> str:
    """Map a classified goal to its first ladder rung."""
    if kind == "repo_write":
        return "cursor-auto"
    return "cdp/fable-5.1"


def format_dispatch_instruction(kind: GoalKind, row: str, *, root_id: str) -> str:
    """One-line dispatch recipe the successor must fire instead of STAY."""
    if kind == "repo_write":
        return f"Dispatch cursor-auto (parent_thread={root_id})"
    return "Dispatch cdp/fable-5.1 (judgment fork)"


def needs_escalation_dispatch(row: str) -> bool:
    """True when ``row`` must dispatch, not STAY or needs-attended."""
    return classify_goal(row) is not None


__all__ = [
    "GoalKind",
    "classify_goal",
    "escalation_target",
    "format_dispatch_instruction",
    "needs_escalation_dispatch",
]
