"""Fail-closed wake-path gate for operator-proxy mission closeouts.

A mission may not close over an outstanding commission without a named wake
path (collector, scheduled followup, charter enrollment, or operator_gate).
``commissioned, in flight`` alone is an invalid close state — instance 8 of
the success-shaped-return class (agent-bus:6576 t67).

Wake tokens establish that *someone* is named. For the auto-runnable class
(plugin install, Customize sync, propagate/restart, continuity hop) a name
proved insufficient — see :mod:`claude_bundles.mission_close_auto_runnable`,
which additionally requires a fired commission and refuses ``operator_gate:``
on work cursor-auto can reach. Only the closeout body is gated on that class;
the compact debrief payload truncates items and would drop the reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from claude_bundles.mission_close_auto_runnable import check_auto_runnable_items

BEYOND_HEADING = "## Work beyond this close"
BEYOND_NOTIFY_PREFIX = "Beyond this close:"

_WAKE_TOKEN_RE = re.compile(
    r"(?i)\b(collector|followup|charter_enrolled|operator_gate|pickup)\s*:"
)
_LAND_CLASS_RE = re.compile(
    r"(?i)\b(land|merge|uncommitted|worktree|hook-blocked|commits_ahead|unlanded|fast-?forward)\b"
)
_IDE_COLLECTOR_RE = re.compile(
    r"(?i)collector:\s*(cursor\s*lead|ide(\s*lead)?|monitor\b|human\b)"
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
    "`operator_gate: <reason>` · or `pickup: <seat>` (mid-episode awaits; "
    "see pickup_awaits). One bullet = one residual; hard-wrapped "
    "continuation lines fold into the bullet. Non-bullet prose under the "
    "heading is ignored. A prose-only section (no bullets) refuses. "
    "Example: `- D10 spec — collector: web-anthropic · followup: poll 6576`. "
    "`commissioned, in flight` without a wake token is an invalid close state. "
    "Pager compact form: `Beyond this close: <none|item — collector: …>`."
)

LAND_IDE_COLLECTOR_FIX_HINT = (
    "Auto-runnable land/merge must use `collector: cursor-auto` "
    "(or `followup:` that is an `agent_bus.request` to cursor-auto / "
    "`contract: implement|propagate`) — never park on IDE/cursor lead/MONITOR."
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
                    "wake_path (collector:|followup:|charter_enrolled:|operator_gate:|pickup:)",
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
    for item in items:
        if _LAND_CLASS_RE.search(item) and _IDE_COLLECTOR_RE.search(item):
            return MissionCloseWakeVerdict(
                ok=False,
                reason="mission_close_ide_collector_for_land",
                missed_tokens=(_truncate_offending(item),),
                fix_hint=LAND_IDE_COLLECTOR_FIX_HINT,
            )
    refusal = check_auto_runnable_items(items)
    if refusal is not None:
        return MissionCloseWakeVerdict(
            ok=False,
            reason=refusal.reason,
            missed_tokens=(_truncate_offending(refusal.item),),
            fix_hint=refusal.fix_hint,
        )
    return MissionCloseWakeVerdict(ok=True)


def format_beyond_notify_line(body: str) -> str | None:
    """Compact ``## Work beyond this close`` into a notify-line payload (no prefix)."""
    section = _section_body(body, BEYOND_HEADING)
    if section is None:
        return None
    if _section_is_none(section):
        return "none"
    items, _ = _parse_bullet_items(section)
    if not items:
        compact = " ".join(_lines_substantive(section))
        return _truncate_offending(compact) if compact else "none"
    parts = [_truncate_offending(item, limit=120) for item in items[:3]]
    joined = " · ".join(parts)
    if len(items) > 3:
        joined = f"{joined} · +{len(items) - 3} more"
    return joined


def validate_mission_debrief_notify(
    *,
    subject: str = "",
    body: str = "",
    tag: str = "",
) -> MissionCloseWakeVerdict:
    """Refuse mission-debrief pages missing Beyond, Architecture, or named systems.

    Growth-map bar (operator 2026-08-04): the phone must show vision + architecture
    with specific ULG systems named — ¬ a bus/status telegram.
    """
    if not is_mission_debrief_notify(subject=subject, tag=tag):
        return MissionCloseWakeVerdict(ok=True)
    text = body or ""

    from pager_notify.mission_page import extract_awareness_slots, named_ulg_systems

    slots = extract_awareness_slots(text)
    # Composed bodies always stamp ``Architecture: …``; also accept ATX.
    has_architecture_label = bool(
        re.search(r"(?im)^(?:architecture|##\s*architecture)\s*:", text)
    ) or bool(slots.architecture)
    if not has_architecture_label:
        return MissionCloseWakeVerdict(
            ok=False,
            reason="mission_debrief_architecture_missing",
            missed_tokens=("Architecture: <named ULG systems>",),
        )
    arch_text = slots.architecture or text
    vision_text = slots.vision or ""
    if not vision_text:
        # Opening paragraph before Looking back / Architecture counts as vision.
        opener = re.split(
            r"(?im)^(?:Looking back:|Architecture:|Looking ahead:|Beyond this close:)",
            text,
            maxsplit=1,
        )[0].strip()
        vision_text = " ".join(opener.split())
    if len(vision_text) < 40:
        return MissionCloseWakeVerdict(
            ok=False,
            reason="mission_debrief_vision_missing",
            missed_tokens=("Vision: <fleet gap this work closes>",),
        )
    systems = named_ulg_systems(f"{arch_text}\n{vision_text}")
    if not systems:
        return MissionCloseWakeVerdict(
            ok=False,
            reason="mission_debrief_systems_unnamed",
            missed_tokens=(
                "name concrete ULG systems (e.g. CSE Session Registry, "
                "project_ask, cdp-registry, agent-bus, cortex)",
            ),
        )

    payload = _beyond_notify_payload(text)
    if payload is None:
        return MissionCloseWakeVerdict(
            ok=False,
            reason="mission_debrief_beyond_missing",
            missed_tokens=(BEYOND_NOTIFY_PREFIX,),
        )
    if payload.casefold() in _NONE_VALUES:
        if _OUTSTANDING_RE.search(text):
            return MissionCloseWakeVerdict(
                ok=False,
                reason="mission_debrief_uncollected_commission",
                missed_tokens=(
                    "wake_path (collector:|followup:|charter_enrolled:|operator_gate:)",
                ),
            )
        return MissionCloseWakeVerdict(ok=True)
    if not _WAKE_TOKEN_RE.search(payload):
        return MissionCloseWakeVerdict(
            ok=False,
            reason="mission_debrief_wake_path_incomplete",
            missed_tokens=(
                "wake_path (collector:|followup:|charter_enrolled:|operator_gate:)",
            ),
        )
    return MissionCloseWakeVerdict(ok=True)


def _beyond_notify_payload(text: str) -> str | None:
    """Extract Beyond payload from same-line or multi-line notify forms."""
    # Compact same-line only (do not let \\s eat the newline into the next bullet):
    # ``Beyond this close: none`` / ``Beyond this close: D10 — collector: …``
    match = re.search(
        rf"(?im)^{re.escape(BEYOND_NOTIFY_PREFIX)}[ \t]+(.+)$",
        text or "",
    )
    if match and match.group(1).strip():
        return match.group(1).strip()
    # Multi-line block from format_mission_awareness_page:
    # Beyond this close:\n- bullet\n- bullet
    block = re.search(
        rf"(?im)^{re.escape(BEYOND_NOTIFY_PREFIX)}\s*\n(.*?)(?=^##\s|\Z)",
        text or "",
        re.DOTALL,
    )
    if block is not None:
        items, _ = _parse_bullet_items(block.group(1))
        if items:
            return " · ".join(items)
        substantive = _lines_substantive(block.group(1))
        if substantive:
            return " ".join(substantive)
        return "none"
    # Durable closeout heading inside a longer notify body.
    section = _section_body(text or "", BEYOND_HEADING)
    if section is None:
        return None
    if _section_is_none(section):
        return "none"
    items, _ = _parse_bullet_items(section)
    if items:
        return " · ".join(items)
    compact = " ".join(_lines_substantive(section))
    return compact if compact else "none"


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
    "format_beyond_notify_line",
    "is_mission_closeout",
    "is_mission_debrief_notify",
    "refusal_envelope",
    "validate_mission_close_wake",
    "validate_mission_debrief_notify",
]
