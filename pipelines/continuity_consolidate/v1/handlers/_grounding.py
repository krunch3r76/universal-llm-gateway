"""Closeout-grounding for consolidate-continuity apply (friction:37643).

The distill payload includes *prior hub assertions*. A model that copies those
rows into mission/resume will cite the trigger CLOSEOUT as evidence even when
that turn never mentioned them. Ground house-level writes in the trigger body
and tip CHECKPOINT residue only — never in the hub assertion list.
"""

from __future__ import annotations

import re
from typing import Any

from ._plan import norm, quoted_mission

# Shorter than this is too easy to match by accident in a large closeout.
_MIN_GROUND = 24

_THROUGH_RE = re.compile(
    r"Consolidated through\s+\S+"
    r"(?:\s+at\s+\S+)?"
    r"(?:\s+\(consolidate-continuity v1\))?"
    r"\.?",
    re.IGNORECASE,
)


def closeout_source_text(trigger: dict[str, Any], tip: dict[str, Any]) -> str:
    """Bus text this fold may quote — CLOSEOUT + tip residue, not hub claims."""
    return "\n".join(
        (
            str(trigger.get("subject") or ""),
            str(trigger.get("body") or ""),
            str(tip.get("residue") or ""),
        )
    )


def grounded_in_closeout(text: str | None, sources: str) -> bool:
    """True when ``text`` appears in the CLOSEOUT/tip sources (normalized)."""
    needle = norm(text or "")
    if len(needle) < _MIN_GROUND:
        return False
    hay = norm(sources)
    if needle in hay:
        return True
    return needle[:_MIN_GROUND] in hay


def select_mission(
    *,
    tip_residue: str | None,
    fold_mission: str | None,
    sources: str,
) -> tuple[str, str]:
    """Return ``(mission_text, source)`` with ``source`` in checkpoint|closeout|none.

    Checkpoint quote wins. The distill fallback is kept only when the CLOSEOUT
    or tip residue actually contains the line — not when it only lives on the hub.
    """
    quoted = quoted_mission(tip_residue)
    if quoted:
        return quoted, "checkpoint"
    fold = (fold_mission or "").strip()
    if fold and grounded_in_closeout(fold, sources):
        return fold, "closeout"
    return "", "none"


def select_resume(resume: Any, sources: str) -> dict[str, str]:
    """Keep only resume fields whose text is supported by CLOSEOUT/tip."""
    if not isinstance(resume, dict):
        return {}
    kept: dict[str, str] = {}
    for key in ("settled", "live", "next"):
        value = str(resume.get(key) or "").strip()
        if value and grounded_in_closeout(value, sources):
            kept[key] = value
    return kept


def fold_evidence_uris(
    *,
    text: str,
    trigger_ref: str,
    trigger_body: str,
    tip_ref: str | None,
    tip_residue: str,
    quoted: bool,
) -> list[str]:
    """Cite the tip when quoted from it; cite the trigger only if the body holds the text."""
    if quoted and tip_ref:
        return [tip_ref]
    uris: list[str] = []
    if grounded_in_closeout(text, trigger_body):
        uris.append(trigger_ref)
    if tip_ref and grounded_in_closeout(text, tip_residue):
        uris.append(tip_ref)
    return uris


def stamp_watermark_on_description(prior: str, trigger_ref: str, stamp: str) -> str:
    """Refresh the watermark clause without rewriting an ungrounded mission/resume."""
    clause = (
        f"Consolidated through {trigger_ref} at {stamp} (consolidate-continuity v1)."
    )
    prior = (prior or "").strip()
    if _THROUGH_RE.search(prior):
        return _THROUGH_RE.sub(clause, prior, count=1)[:1200]
    if not prior:
        return clause[:1200]
    return f"{prior.rstrip('. ')}. {clause}"[:1200]
