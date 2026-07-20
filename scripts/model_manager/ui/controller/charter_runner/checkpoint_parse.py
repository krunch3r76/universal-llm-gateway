"""Parse a standing-root ``CHECKPOINT`` turn body into structured fields.

The agent-bus store has no CHECKPOINT turn type or body parser — CHECKPOINTs are
markdown turns identified only by a ``CHECKPOINT`` subject prefix. This module is
the sole reader that turns a CHECKPOINT body into the fields the charter runner
gates on (WIP, gated Next-pickup, scoreboard URI, BLOCKED, canonical Steps,
RESUME footer). Parsing is deliberately lenient: an unrecognized shape yields a
conservative "not eligible" result rather than raising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# status glyph -> canonical step status (v0 minimal Steps block)
_STEP_STATUS = {" ": "pending", "~": "in_progress", "x": "done", "!": "blocked"}
_STEP_RE = re.compile(r"^\s*(?:\d+\.|[-*])\s*\[([ x~!])\]\s*(.+?)\s*$")
# Gated deliverable ids: scoreboard G-rows (G2, G3a) and charter R-beats (R1a, R1b).
_GATED_ROW_RE = re.compile(r"\b[GR]\d+[a-z]?\b")
_SCOREBOARD_URI_RE = re.compile(r"(cortex://\S*scoreboard\S*)", re.IGNORECASE)
_CORTEX_URI_RE = re.compile(r"cortex://[^\s)\]]+")
_OPERATOR_FORK_RE = re.compile(r"\boperator\b", re.IGNORECASE)
# Bare "none" or "none (gloss)" — parenthetical notes must not flip WIP-active.
_WIP_NONE_RE = re.compile(
    r"^(?:[-*]\s*)?(?:wip:|in[_-]?flight:)?none(?:\s*\(.*\))?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Step:
    ordinal: int
    title: str
    status: str  # pending | in_progress | done | blocked


@dataclass(frozen=True)
class ParsedCheckpoint:
    wip_is_none: bool
    wip_text: str
    next_pickup: list[str] = field(default_factory=list)
    next_pickup_gated: bool = False
    scoreboard_uri: str | None = None
    blocked: bool = False
    open_operator_fork: bool = False
    steps: list[Step] = field(default_factory=list)
    has_resume_footer: bool = False


def _sections(body: str) -> dict[str, str]:
    """Split a markdown body into ``## heading`` -> section-text (lowercased key)."""
    sections: dict[str, list[str]] = {}
    current = "_preamble"
    sections[current] = []
    for line in body.splitlines():
        heading = re.match(r"^#{2,}\s+(.*?)\s*$", line)
        if heading:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def _find_section(sections: dict[str, str], *needles: str) -> str:
    for key, text in sections.items():
        if any(n in key for n in needles):
            return text
    return ""


def _parse_steps(text: str) -> list[Step]:
    steps: list[Step] = []
    ordinal = 0
    for line in text.splitlines():
        m = _STEP_RE.match(line)
        if not m:
            continue
        ordinal += 1
        glyph, title = m.group(1), m.group(2).strip()
        steps.append(
            Step(
                ordinal=ordinal, title=title, status=_STEP_STATUS.get(glyph, "pending")
            )
        )
    return steps


def _parse_next_pickup(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:\d+\.|[-*])\s+(.+?)\s*$", line)
        if m:
            items.append(m.group(1).strip())
    return items


def _wip_is_none(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    # Single-line "none" (any casing / list glyph), optional parenthetical gloss.
    if "\n" not in stripped and _WIP_NONE_RE.match(stripped):
        return True
    # Legacy normalize path for bare tokens without gloss.
    normalized = re.sub(r"[-*\s`]", "", stripped).lower()
    return normalized in {"none", "wip:none", "inflight:none"}


def parse_checkpoint(body: str) -> ParsedCheckpoint:
    """Parse a CHECKPOINT turn body. Never raises; unknown shape => conservative."""
    body = body or ""
    sections = _sections(body)

    wip_text = _find_section(sections, "in-flight", "wip", "in flight")
    next_text = _find_section(sections, "next pickup", "next-pickup")
    steps_text = _find_section(sections, "steps")

    next_pickup = _parse_next_pickup(next_text)
    next_pickup_gated = any(_GATED_ROW_RE.search(item) for item in next_pickup)
    open_operator_fork = any(_OPERATOR_FORK_RE.search(item) for item in next_pickup)

    scoreboard_uri = None
    m = _SCOREBOARD_URI_RE.search(body)
    if m:
        scoreboard_uri = m.group(1).rstrip(".,)")
    else:
        for line in body.splitlines():
            if "scoreboard" in line.lower():
                u = _CORTEX_URI_RE.search(line)
                if u:
                    scoreboard_uri = u.group(0).rstrip(".,)")
                    break

    steps = _parse_steps(steps_text)
    blocked = _detect_blocked(body, steps)

    return ParsedCheckpoint(
        wip_is_none=_wip_is_none(wip_text),
        wip_text=wip_text.strip(),
        next_pickup=next_pickup,
        next_pickup_gated=next_pickup_gated,
        scoreboard_uri=scoreboard_uri,
        blocked=blocked,
        open_operator_fork=open_operator_fork,
        steps=steps,
        has_resume_footer=_has_resume_footer(body),
    )


def _detect_blocked(body: str, steps: list[Step]) -> bool:
    # Explicit BLOCKED status line, or the first not-done step is blocked.
    for line in body.splitlines():
        if re.match(r"^\s*(?:[-*]\s*)?BLOCKED\b", line):
            return True
    for step in steps:
        if step.status == "done":
            continue
        return step.status == "blocked"
    return False


def _has_resume_footer(body: str) -> bool:
    return "RESUME (any seat" in body or "— RESUME" in body


def first_actionable_step(parsed: ParsedCheckpoint) -> Step | None:
    """First step that is not done (the window's unit of work), if any."""
    for step in parsed.steps:
        if step.status != "done":
            return step
    return None
