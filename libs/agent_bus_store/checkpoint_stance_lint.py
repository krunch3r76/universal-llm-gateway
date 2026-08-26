"""Continuity stance lint — first-class trait of unenrolled roots.

``orchestrator_continuity`` birth/bootstrap CHECKPOINTs must index the stance
trait: Use ``ulg-for-llms`` and point at ``## Why this house``. Enrolled
``charter-runner`` ticks skip. Advisory-only on post; charter lint runs first.
"""

from __future__ import annotations

import re

from .body_briefing_advisory import BriefingAdvisory
from .checkpoint_charter_lint import requires_orchestration_charter_binding
from .enrollment_guard import ENROLLMENT_TAG

_SKILL = re.compile(r"ulg-for-llms", re.IGNORECASE)
_PREAMBLE = re.compile(r"Why this house", re.IGNORECASE)

_STANCE_SUGGESTION = (
    "Continuity stance is a first-class trait of orchestrator_continuity roots: "
    "index ## Stance with Use the `ulg-for-llms` skill and a Why this house "
    "pointer (speech lives on the continuity-doc, not this turn). "
    "tick_charter skips. Footer stays §3.1.1."
)


def _unenrolled(thread_tags: list[str] | None) -> bool:
    tags = {t.strip().lower() for t in (thread_tags or []) if t and str(t).strip()}
    return ENROLLMENT_TAG not in tags


def requires_continuity_stance(
    *,
    subject: str | None,
    thread_tags: list[str] | None,
    supersedes_turn: int | None,
) -> bool:
    """True on birth/bootstrap CHECKPOINT when the root is not tick-enrolled."""
    if not _unenrolled(thread_tags):
        return False
    return requires_orchestration_charter_binding(
        subject=subject,
        thread_tags=thread_tags,
        supersedes_turn=supersedes_turn,
    )


def lint_continuity_stance(body: str) -> tuple[bool, bool] | None:
    """Return (has_skill, has_preamble) when either half is missing; else None."""
    has_skill = bool(_SKILL.search(body))
    has_preamble = bool(_PREAMBLE.search(body))
    if has_skill and has_preamble:
        return None
    return has_skill, has_preamble


def orchestration_stance_advisory(
    *,
    body: str,
    subject: str | None,
    thread_tags: list[str] | None,
    supersedes_turn: int | None,
) -> BriefingAdvisory | None:
    """Non-blocking advisory when a continuity birth lacks the stance trait."""
    if not requires_continuity_stance(
        subject=subject,
        thread_tags=thread_tags,
        supersedes_turn=supersedes_turn,
    ):
        return None
    lint = lint_continuity_stance(body)
    if lint is None:
        return None
    has_skill, has_preamble = lint
    missing: list[str] = []
    if not has_skill:
        missing.append("Use the `ulg-for-llms` skill")
    if not has_preamble:
        missing.append("`## Why this house` pointer")
    return BriefingAdvisory(
        body_chars=len(body),
        target_chars=0,
        reason="root_missing_stance",
        suggestion=_STANCE_SUGGESTION + " Missing: " + " and ".join(missing) + ".",
        turn_kind="continuity_stance",
        suppressed_by_profile=False,
    )


__all__ = [
    "lint_continuity_stance",
    "orchestration_stance_advisory",
    "requires_continuity_stance",
]
