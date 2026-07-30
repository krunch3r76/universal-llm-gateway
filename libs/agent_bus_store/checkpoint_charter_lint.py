"""Orchestration birth lint — root spine requires bound charter surfaces.

``spine=root`` (orchestration thread) must ship with a durable charter pointer
(scoreboard and/or continuity doc) and a bound objective. Advisory-only on post;
see ``agent-bus-discipline`` § Orchestration birth gate.
"""

from __future__ import annotations

import re

from .body_briefing_advisory import BriefingAdvisory
from .checkpoint_kind_detector import (
    is_birth_shaped_checkpoint,
    is_bootstrap_structural_checkpoint,
)
from .checkpoint_projection import is_checkpoint_subject
from .enrollment_guard import ENROLLMENT_TAG
from .thread_classification import ROLE_ROOT_TAG

_CHARTER_SCOREBOARD_URI = re.compile(
    r"charter-scoreboard\.md",
    re.IGNORECASE,
)
_SCOREBOARD_LINE = re.compile(
    r"Scoreboard:\s*(?:cortex|workspaces)://\S+",
    re.IGNORECASE,
)
_CHARTER_POINTER = re.compile(
    r"(?:continuity-doc|Charter:\s*(?:cortex|workspaces)://)",
    re.IGNORECASE,
)
_OBJECTIVE_LINE = re.compile(r"(?m)^Objective\s*[:—]", re.IGNORECASE)
_OBJECTIVE_SECTION = re.compile(r"##\s+Objective\b", re.IGNORECASE)
_PRIMARY_OPEN = re.compile(r"Primary OPEN\s*:", re.IGNORECASE)
_ANCHOR_BULLET = re.compile(
    r"##\s+Anchor\s*\n(?:(?![#]).*\n)*?\n\s*[-*]\s+\S",
    re.IGNORECASE,
)

_CHARTER_ADVISORY_SUGGESTION = (
    "Orchestration root birth requires charter surfaces before the thread is "
    "actionable: mint scoreboard (cortex://…/…-charter-scoreboard.md) and/or "
    "continuity-doc, bind Objective (or ## Anchor / Primary OPEN), index both "
    "in the birth CHECKPOINT, then stamp role:root. "
    "¬ advise-close or treat empty Next-pickup as arc complete on a newborn root."
)


def requires_orchestration_charter_binding(
    *,
    subject: str | None,
    thread_tags: list[str] | None,
    supersedes_turn: int | None,
) -> bool:
    """True when this CHECKPOINT promotes or births an orchestration spine."""
    if subject is None or not is_checkpoint_subject(subject):
        return False
    if is_birth_shaped_checkpoint(
        subject=subject,
        supersedes_turn=supersedes_turn,
    ):
        return True
    return is_bootstrap_structural_checkpoint(
        subject=subject,
        thread_tags=thread_tags or [],
        supersedes_turn=supersedes_turn,
    )


def _has_charter_pointer(body: str, *, enrolled: bool) -> bool:
    if _CHARTER_SCOREBOARD_URI.search(body) or _SCOREBOARD_LINE.search(body):
        return True
    if enrolled:
        return False
    return bool(_CHARTER_POINTER.search(body))


def _has_bound_objective(body: str) -> bool:
    if (
        _OBJECTIVE_LINE.search(body)
        or _OBJECTIVE_SECTION.search(body)
        or _PRIMARY_OPEN.search(body)
        or _ANCHOR_BULLET.search(body)
    ):
        return True
    return False


def lint_orchestration_charter_binding(
    body: str,
    *,
    subject: str | None,
    thread_tags: list[str] | None,
    supersedes_turn: int | None,
) -> tuple[bool, bool] | None:
    """Return (has_pointer, has_objective) when binding required; else None."""
    if not requires_orchestration_charter_binding(
        subject=subject,
        thread_tags=thread_tags,
        supersedes_turn=supersedes_turn,
    ):
        return None
    tags = {t.strip().lower() for t in (thread_tags or []) if t and str(t).strip()}
    enrolled = ENROLLMENT_TAG in tags or ROLE_ROOT_TAG in tags
    has_pointer = _has_charter_pointer(body, enrolled=enrolled)
    has_objective = _has_bound_objective(body)
    if has_pointer and has_objective:
        return None
    return has_pointer, has_objective


def orchestration_charter_advisory(
    *,
    body: str,
    subject: str | None,
    thread_tags: list[str] | None,
    supersedes_turn: int | None,
) -> BriefingAdvisory | None:
    """Non-blocking advisory when orchestration birth lacks charter surfaces."""
    lint = lint_orchestration_charter_binding(
        body,
        subject=subject,
        thread_tags=thread_tags,
        supersedes_turn=supersedes_turn,
    )
    if lint is None:
        return None
    has_pointer, has_objective = lint
    missing: list[str] = []
    if not has_pointer:
        missing.append("charter pointer (Scoreboard:/continuity-doc URI)")
    if not has_objective:
        missing.append("bound objective (Objective:/## Anchor/Primary OPEN)")
    reason_detail = "; missing " + " and ".join(missing)
    return BriefingAdvisory(
        body_chars=len(body),
        target_chars=0,
        reason="root_missing_charter",
        suggestion=_CHARTER_ADVISORY_SUGGESTION + reason_detail,
        turn_kind="orchestration_birth",
        suppressed_by_profile=False,
    )


__all__ = [
    "lint_orchestration_charter_binding",
    "orchestration_charter_advisory",
    "requires_orchestration_charter_binding",
]
