"""Advisory when a continuity tip drops inherited entity refs.

Birth may omit ``## Entity refs``. A later checkpoint that supersedes a tip
which already named refs must carry those rows forward or mark them done.
Charter-runner ticks skip. Advisory only — the projector that would write
the block remains parked.
"""

from __future__ import annotations

import re

from .body_briefing_advisory import BriefingAdvisory
from .checkpoint_projection import is_checkpoint_subject
from .enrollment_guard import ENROLLMENT_TAG

_HEADING = re.compile(r"^## Entity refs\s*$", re.MULTILINE)
_NEXT_HEADING = re.compile(r"^## ", re.MULTILINE)
_NONE = re.compile(r"^_None yet\._$", re.IGNORECASE)

_SUGGESTION = (
    "This checkpoint supersedes a tip that named ## Entity refs. "
    "Carry the rows forward (id · watch: field, never a value) or mark "
    "a finished id done. Dropping the block makes the next resume repeat "
    "stale status."
)


def entity_ref_rows(body: str) -> list[str]:
    """Return non-empty ref rows under ``## Entity refs``, ignoring ``_None yet._``."""
    match = _HEADING.search(body)
    if match is None:
        return []
    rest = body[match.end() :]
    nxt = _NEXT_HEADING.search(rest)
    chunk = rest[: nxt.start()] if nxt else rest
    rows: list[str] = []
    for line in chunk.splitlines():
        text = line.strip()
        if not text or text.startswith("```") or _NONE.match(text):
            continue
        if text.startswith("-") or text.startswith("*"):
            rows.append(text)
    return rows


def entity_ref_drop_advisory(
    *,
    body: str,
    predecessor_body: str | None,
    subject: str | None,
    thread_tags: list[str] | None,
    supersedes_turn: int | None,
) -> BriefingAdvisory | None:
    """Fire when a superseding checkpoint drops a non-empty inherited ref list."""
    if supersedes_turn is None or not predecessor_body:
        return None
    if not is_checkpoint_subject(subject):
        return None
    tags = {t.strip().lower() for t in (thread_tags or []) if t and str(t).strip()}
    if ENROLLMENT_TAG in tags:
        return None
    if not entity_ref_rows(predecessor_body):
        return None
    if entity_ref_rows(body):
        return None
    return BriefingAdvisory(
        body_chars=len(body),
        target_chars=0,
        reason="tip_missing_entity_refs",
        suggestion=_SUGGESTION,
        turn_kind="continuity_entity_refs",
        suppressed_by_profile=False,
    )
