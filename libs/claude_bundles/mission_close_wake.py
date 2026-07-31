"""Fail-closed wake-path gate for operator-proxy mission closeouts.

A mission may not close over an outstanding commission without a named wake
path (collector, scheduled followup, charter enrollment, or kaywan_gate).
``commissioned, in flight`` alone is an invalid close state — instance 8 of
the success-shaped-return class (agent-bus:6576 t67).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BEYOND_HEADING = "## Work beyond this close"
BEYOND_NOTIFY_PREFIX = "Beyond this close:"

_WAKE_TOKEN_RE = re.compile(
    r"(?i)\b(collector|followup|charter_enrolled|kaywan_gate)\s*:"
)
_OUTSTANDING_RE = re.compile(r"(?i)\b(in[-\s]?flight|commissioned)\b")
_MISSION_CLOSEOUT_TYPE_RE = re.compile(r"(?i)^TYPE:\s*MISSION_CLOSEOUT\b", re.M)
_MISSION_CLOSEOUT_SUBJECT_RE = re.compile(r"(?i)\bMISSION\s+CLOSEOUT\b")
_NONE_VALUES = frozenset(
    {
        "none",
        "none.",
        "(none)",
        "n/a",
        "na",
        "no outstanding work",
        "no outstanding work.",
    }
)

_OFFENDING_ITEM_MAX = 200

MISSION_CLOSE_WAKE_FIX_HINT = (
    "Add a `## Work beyond this close` section. Use `none` only when nothing "
    "will produce a result after close. Otherwise list each residual as a "
    "bullet (`- …`) with a wake token anywhere in that bullet: "
    "`collector: <seat>` · `followup: <how>` · `charter_enrolled: <root>` · "
    "or `kaywan_gate: <reason>`. One bullet = one residual; hard-wrapped "
    "continuation lines fold into the bullet. Non-bullet prose under the "
    "heading is ignored. A prose-only section (no bullets) refuses. "
    "Example: `- D10 spec — collector: web-anthropic · followup: poll 6576`. "
    "`commissioned, in flight` without a wake token is an invalid close state. "
    "Pager compact form: `Beyond this close: <none|item — collector: …>`."
)


@dataclass(frozen=True, slots=True)
class MissionCloseWakeVerdict:
    """Result of validating a mission-close body or notify payload."""

    ok: bool
    reason: str | None = None
    missed_tokens: tuple[str, ...] = ()
    fix_hint: str = MISSION_CLOSE_WAKE_FIX_HINT


def is_mission_closeout(*, subject: str = "", body: str = "") -> bool:
    """True when subject or body declares a mission closeout turn."""
    if _MISSION_CLOSEOUT_TYPE_RE.search(body or ""):
        return True
    return bool(_MISSION_CLOSEOUT_SUBJECT_RE.search(subject or ""))


def is_mission_debrief_notify(*, subject: str = "", tag: str = "") -> bool:
    """True when a pager notify is a mission/episode debrief awareness ping."""
    tag_l = (tag or "").strip().lower()
    if "mission-debrief" in tag_l or "mission_debrief" in tag_l:
        return True
    subj = subject or ""
    return bool(re.search(r"(?i)\b(mission\s+debrief|episode\s+debrief)\b", subj))


def _section_body(text: str, heading: str) -> str | None:
    pattern = re.compile(
        rf"(?im)^{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)",
        re.DOTALL,
    )
    match = pattern.search(text or "")
    if match is None:
        return None
    return match.group(1).strip()


_BULLET_START_RE = re.compile(r"^[-*]\s+")


def _truncate_offending(text: str, *, limit: int = _OFFENDING_ITEM_MAX) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _lines_substantive(section: str) -> list[str]:
    out: list[str] = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line or line.startswith("<!--"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        elif line.startswith("* "):
            line = line[2:].strip()
        if line:
            out.append(line)
    return out


def _parse_bullet_items(section: str) -> tuple[list[str], bool]:
    """Return folded bullet contents and whether non-bullet prose lines exist."""
    items: list[str] = []
    current: list[str] | None = None
    has_prose = False
    for raw in section.splitlines():
        line = raw.strip()
        if not line or line.startswith("<!--"):
            continue
        if _BULLET_START_RE.match(line):
            if current is not None:
                items.append(" ".join(current))
            current = [_BULLET_START_RE.sub("", line, count=1).strip()]
        elif current is not None:
            current.append(line)
        else:
            has_prose = True
    if current is not None:
        items.append(" ".join(current))
    return items, has_prose


def _section_is_none(section: str) -> bool:
    lines = _lines_substantive(section)
    if not lines:
        return True
    if len(lines) == 1 and lines[0].casefold() in _NONE_VALUES:
        return True
    return False


def validate_mission_close_wake(
    *,
    subject: str = "",
    body: str = "",
) -> MissionCloseWakeVerdict:
    """Refuse mission closeouts that lack a named wake path for outstanding work."""
    if not is_mission_closeout(subject=subject, body=body):
        return MissionCloseWakeVerdict(ok=True)
    text = body or ""
    section = _section_body(text, BEYOND_HEADING)
    if section is None:
        return MissionCloseWakeVerdict(
            ok=False,
            reason="mission_close_wake_path_missing",
            missed_tokens=(BEYOND_HEADING,),
        )
    has_outstanding = bool(_OUTSTANDING_RE.search(text))
    if _section_is_none(section):
        if has_outstanding:
            return MissionCloseWakeVerdict(
                ok=False,
                reason="mission_close_uncollected_commission",
                missed_tokens=(
                    "wake_path (collector:|followup:|charter_enrolled:|kaywan_gate:)",
                ),
            )
        return MissionCloseWakeVerdict(ok=True)
    items, has_prose = _parse_bullet_items(section)
    if not items:
        if has_prose:
            prose_sample = _truncate_offending(
                next(
                    ln.strip()
                    for ln in section.splitlines()
                    if ln.strip() and not ln.strip().startswith("<!--")
                ),
            )
            return MissionCloseWakeVerdict(
                ok=False,
                reason="mission_close_wake_path_incomplete",
                missed_tokens=(prose_sample,),
            )
        return MissionCloseWakeVerdict(ok=True)
    bad_items: list[str] = []
    for item in items:
        if not _WAKE_TOKEN_RE.search(item):
            bad_items.append(_truncate_offending(item))
    if bad_items:
        return MissionCloseWakeVerdict(
            ok=False,
            reason="mission_close_wake_path_incomplete",
            missed_tokens=tuple(bad_items),
        )
    return MissionCloseWakeVerdict(ok=True)


def validate_mission_debrief_notify(
    *,
    subject: str = "",
    body: str = "",
    tag: str = "",
) -> MissionCloseWakeVerdict:
    """Refuse mission-debrief pager bodies that omit the beyond-this-close line."""
    if not is_mission_debrief_notify(subject=subject, tag=tag):
        return MissionCloseWakeVerdict(ok=True)
    text = body or ""
    match = re.search(
        rf"(?im)^{re.escape(BEYOND_NOTIFY_PREFIX)}\s*(.+)$",
        text,
    )
    if match is None:
        # Allow the durable heading form inside longer notify bodies.
        section = _section_body(text, BEYOND_HEADING)
        if section is None:
            return MissionCloseWakeVerdict(
                ok=False,
                reason="mission_debrief_beyond_missing",
                missed_tokens=(BEYOND_NOTIFY_PREFIX,),
            )
        payload = section
    else:
        payload = match.group(1).strip()
    if payload.casefold() in _NONE_VALUES:
        if _OUTSTANDING_RE.search(text):
            return MissionCloseWakeVerdict(
                ok=False,
                reason="mission_debrief_uncollected_commission",
                missed_tokens=(
                    "wake_path (collector:|followup:|charter_enrolled:|kaywan_gate:)",
                ),
            )
        return MissionCloseWakeVerdict(ok=True)
    if not _WAKE_TOKEN_RE.search(payload):
        return MissionCloseWakeVerdict(
            ok=False,
            reason="mission_debrief_wake_path_incomplete",
            missed_tokens=(
                "wake_path (collector:|followup:|charter_enrolled:|kaywan_gate:)",
            ),
        )
    return MissionCloseWakeVerdict(ok=True)


def refusal_envelope(verdict: MissionCloseWakeVerdict) -> dict[str, object]:
    """Structured MCP/cursor-auto refusal payload (missed_tokens + fix_hint)."""
    return {
        "error": (
            "Mission close refused — outstanding work has no named wake path "
            f"({verdict.reason})."
        ),
        "reason": verdict.reason or "mission_close_wake_path_missing",
        "missed_tokens": list(verdict.missed_tokens),
        "fix_hint": verdict.fix_hint,
        "status": "blocked",
    }


__all__ = [
    "BEYOND_HEADING",
    "BEYOND_NOTIFY_PREFIX",
    "MISSION_CLOSE_WAKE_FIX_HINT",
    "MissionCloseWakeVerdict",
    "is_mission_closeout",
    "is_mission_debrief_notify",
    "refusal_envelope",
    "validate_mission_close_wake",
    "validate_mission_debrief_notify",
]
