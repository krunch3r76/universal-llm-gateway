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
_LAST_FOLD_RE = re.compile(
    r"\s*Last fold\s+\S+\s+at\s+\S+:\s+no grounded mission/resume\.?",
    re.IGNORECASE,
)
_MISSION_CLAUSE_RE = re.compile(
    r"Mission:\s*(?P<text>.+?)\.(?=\s|$)",
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
    """True when the whole ``text`` appears in the CLOSEOUT/tip sources.

    A shared prefix is not enough: a line can open with CLOSEOUT wording and
    finish with a hub row the trigger never stated (friction:37643 R1).
    """
    needle = norm(text or "")
    if len(needle) < _MIN_GROUND:
        return False
    return needle in norm(sources)


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


def resume_evidence_uris(
    *,
    resume: dict[str, str],
    trigger_ref: str,
    trigger_body: str,
    tip_ref: str | None,
    tip_residue: str,
) -> list[str]:
    """Union per-field citations. A joined resume string is not one source."""
    uris: list[str] = []
    for value in resume.values():
        for uri in fold_evidence_uris(
            text=value,
            trigger_ref=trigger_ref,
            trigger_body=trigger_body,
            tip_ref=tip_ref,
            tip_residue=tip_residue,
            quoted=False,
        ):
            if uri not in uris:
                uris.append(uri)
    return uris


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
    """Note an ungrounded fold without retargeting the last grounded watermark.

    The card's ``Consolidated through`` clause stays on the prior ref. Pairing
    that older mission text with the new trigger is the 37643 mis-cite.
    """
    note = f"Last fold {trigger_ref} at {stamp}: no grounded mission/resume."
    text = _LAST_FOLD_RE.sub("", prior or "").strip()
    if not text:
        return note[:1200]
    return f"{text.rstrip('. ')}. {note}"[:1200]


def partial_card_description(
    prior: str,
    resume: dict[str, str],
    trigger_ref: str,
    stamp: str,
) -> str:
    """Write grounded resume fields without blanking a prior mission sentence.

    An empty distill mission is not evidence that the house has none. The prior
    ``Consolidated through`` clause stays; this fold is a resume note only.
    """
    text = (prior or "").strip()
    parts: list[str] = []
    mission = _MISSION_CLAUSE_RE.search(text)
    if mission:
        parts.append(f"Mission: {mission.group('text').strip().rstrip('.')}.")
    elif not text:
        parts.append("Mission: (none folded yet).")
    for key in ("settled", "live", "next"):
        value = str(resume.get(key) or "").strip()
        if value:
            parts.append(f"{key.capitalize()}: {value.rstrip('.')}.")
    through = _THROUGH_RE.search(text)
    if through:
        parts.append(through.group(0).strip().rstrip(".") + ".")
    parts.append(f"Resume fold {trigger_ref} at {stamp}.")
    return " ".join(parts)[:1200]
