"""Mission-statement capture for hop-cadence watch rows.

The this-hop status card (``claude_bundles.operator_proxy_hop_status``)
renders a ``mission`` bullet first — a one-line "what is this arc for" that
must survive even when the per-hop standing-handoff sidecar goes missing
(the degraded case that originally shipped with no purpose context at all).
This module is the sole writer of that text: captured once, at first watch
enrollment (``hop_cadence_watch.observe_lane_from_enqueue``), from whichever
the enrolling job actually declares. Never fabricated — absence stays absence.
"""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_auto.queue import AutoJob

_VISION_VALUE_RE = re.compile(r"^vision:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
# Subjects Auto itself authors for status/mechanical turns — not intent.
# ``type:`` covers callers that pass the bus TYPE token as subject (no real
# subject set) rather than a descriptive one.
_GENERIC_SUBJECT_PREFIXES = ("status:", "continuity", "cursor-auto", "type:")
_MAX_MISSION_CHARS = 160


def mission_candidate_from_job(job: AutoJob) -> str | None:
    """Return a mission one-liner from *job*, or ``None`` when nothing usable.

    Prefers an explicit ``vision:`` line — the DIRECTIVE admit-gate already
    requires implement/investigate work to state "why this work matters" —
    over the bus ``subject``, since subjects on operator-proxy lanes are
    often mechanical status text rather than a statement of intent.
    """
    match = _VISION_VALUE_RE.search(job.body or "")
    if match:
        value = match.group(1).strip()
        if value:
            return _clip(value)
    subject = (job.subject or "").strip()
    if subject and not subject.lower().startswith(_GENERIC_SUBJECT_PREFIXES):
        return _clip(subject)
    return None


def _clip(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip()
    if len(collapsed) <= _MAX_MISSION_CHARS:
        return collapsed
    return collapsed[: _MAX_MISSION_CHARS - 1].rstrip() + "…"


__all__ = ["mission_candidate_from_job"]
