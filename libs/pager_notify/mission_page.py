"""Operator-facing pager body shapes — vision/architecture first, not status dumps.

Mission-debrief SOT remains ``cdp-operator-proxy`` § Mission-debrief format.
This module is the mechanical composer for host-side scripts (summons watchdog,
watch scripts) that cannot load that skill at page time.

Growth-map so-what (operator 2026-08-04): awareness pages must answer (1) future
of ULG capabilities, (2) improved since before, (3) effect on repo consumers
(humans *and agents*); name **vision** (ULG vision statements, ¬ mission
narrative), **architecture**, and **specific ULG systems** — ¬ a bus/status
telegram.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pager_notify.so_what import (
    SMS_BODY_MAX,
    SMS_SUBJECT_MAX,
    clip,
    extract_so_what_from_body,
)

__all__ = [
    "SUMMONS_ARCH_ONE_LINER",
    "ULG_SYSTEM_LEXICON",
    "AwarenessSlots",
    "extract_awareness_slots",
    "format_mission_awareness_page",
    "format_summons_stop_page",
    "named_ulg_systems",
    "summons_look_ahead",
    "summons_look_back",
]

# Concrete ULG surfaces the phone should be able to recognize as "what grew".
# Matching is case-insensitive substring; keep entries specific enough that
# generic words ("bus", "code") do not false-pass alone.
ULG_SYSTEM_LEXICON: tuple[str, ...] = (
    "agent-bus",
    "agent_bus",
    "cdp-registry",
    "cdp_ask",
    "cdp-ask",
    "charter-runner",
    "closeout relay",
    "consult_queue",
    "cortex",
    "cortex_api",
    "cse session registry",
    "cse_session",
    "cursor-auto",
    "cursor-sdk",
    "drain supervisor",
    "email-bridge",
    "git_integration_worker",
    "giw",
    "pager",
    "project_ask",
    "stargate",
    "sms-bridge",
)

SUMMONS_ARCH_ONE_LINER = (
    "The summons loop only keeps continuity; the commission's finish line is "
    "what defines mission done — wake without that bind is a false green light."
)

_STOP_LOOK_BACK = {
    "arc_complete": (
        "A commission flag said the episode loop was finished. That flag has been "
        "wrong before when it closed a cleanup slice while the real mission "
        "(SDK align, git branches, borrowed multi-agent flows) was still open."
    ),
    "deadline": (
        "The wall-clock budget for unattended episodes ran out before the "
        "commission declared the three-fold mission finished."
    ),
    "consecutive_failures": (
        "Two launches in a row admitted but never showed a live Cowork session — "
        "the wake path fired, the seat never arrived."
    ),
    "episode_budget": (
        "The episode budget capped further summons before the mission folds "
        "were marked terminal."
    ),
    "commission_seq_not_incremented": (
        "An episode exited without rewriting the next-commission pointer — "
        "the replay guard stopped rather than re-running the same window."
    ),
    "commission_unreadable": (
        "The commission file that tells the loop what 'done' means could not "
        "be read, so the loop refused to guess."
    ),
}

_STOP_LOOK_AHEAD = {
    "arc_complete": (
        "Treat arc_complete as real only if the three folds are terminal in the "
        "commission. Otherwise reopen the mission commission and re-arm the loop — "
        "do not celebrate a sub-arc."
    ),
    "deadline": (
        "Extend the deadline or finish the open folds in an attended window; "
        "the unfinished work is still the mission."
    ),
    "consecutive_failures": (
        "Fix Cowork Auto/approval so the next summon attaches, then re-arm — "
        "the mission did not complete."
    ),
    "episode_budget": (
        "Raise the budget or land the remaining folds in fewer, fatter episodes; "
        "re-arm against the same mission finish line."
    ),
    "commission_seq_not_incremented": (
        "Repair the departing seat's continuity rewrite, then re-arm — "
        "missing pointer is a continuity defect, not mission done."
    ),
    "commission_unreadable": (
        "Restore the commission file, then re-arm. Without it the loop has no "
        "honest finish line."
    ),
}


def summons_look_back(reason: str) -> str:
    return _STOP_LOOK_BACK.get(
        reason,
        f"The overnight episode loop stopped ({reason}) before the mission finish line.",
    )


def summons_look_ahead(reason: str) -> str:
    return _STOP_LOOK_AHEAD.get(
        reason,
        "Read the commission, restore the finish line, and re-arm the loop.",
    )


@dataclass(frozen=True, slots=True)
class AwarenessSlots:
    """Parsed growth-map slots from a closeout or hand-authored notify body."""

    vision: str = ""
    looking_back: str = ""
    architecture: str = ""
    looking_ahead: str = ""
    so_what: str = ""


_LABEL_RE = {
    "vision": re.compile(r"(?im)^(?:vision|##\s*vision)\s*[:\-]?\s*(.*)$"),
    "looking_back": re.compile(
        r"(?im)^(?:looking back|##\s*looking back)\s*[:\-]?\s*(.*)$"
    ),
    "architecture": re.compile(
        r"(?im)^(?:architecture|##\s*architecture)\s*[:\-]?\s*(.*)$"
    ),
    "looking_ahead": re.compile(
        r"(?im)^(?:looking ahead|##\s*looking ahead)\s*[:\-]?\s*(.*)$"
    ),
}
_ATX_SECTION_RE = re.compile(
    r"(?im)^##\s+(Vision|Looking back|Architecture|Looking ahead)\s*\n(.*?)(?=^##\s|\Z)",
    re.DOTALL,
)


def named_ulg_systems(text: str) -> list[str]:
    """Return lexicon hits present in ``text`` (deduped, longest-first preference)."""
    hay = (text or "").casefold()
    hits: list[str] = []
    for token in sorted(ULG_SYSTEM_LEXICON, key=len, reverse=True):
        if token.casefold() in hay and token not in hits:
            # Prefer canonical display form for compound names.
            hits.append(token)
    return hits


def extract_awareness_slots(body: str) -> AwarenessSlots:
    """Pull vision/architecture slots from labeled lines or ATX sections."""
    text = body or ""
    found: dict[str, str] = {}
    for match in _ATX_SECTION_RE.finditer(text):
        key = match.group(1).strip().casefold().replace(" ", "_")
        if key == "looking_back" or key == "looking_ahead" or key in {
            "vision",
            "architecture",
        }:
            prose = " ".join(match.group(2).split()).strip()
            if prose:
                found[key] = prose
    for key, pattern in _LABEL_RE.items():
        if key in found:
            continue
        match = pattern.search(text)
        if not match:
            continue
        remainder = (match.group(1) or "").strip()
        if remainder:
            found[key] = " ".join(remainder.split())
            continue
        # Label-only line — take following non-empty paragraph.
        after = text[match.end() :]
        for raw in after.splitlines():
            line = raw.strip()
            if not line:
                if found.get(key):
                    break
                continue
            if line.startswith("#") or line.lower().startswith(
                ("vision", "looking back", "architecture", "looking ahead", "beyond")
            ):
                break
            found[key] = " ".join(line.split())
            break
    so_what = extract_so_what_from_body(text) or ""
    return AwarenessSlots(
        vision=found.get("vision", ""),
        looking_back=found.get("looking_back", ""),
        architecture=found.get("architecture", ""),
        looking_ahead=found.get("looking_ahead", ""),
        so_what=so_what,
    )


def format_mission_awareness_page(
    *,
    subject: str,
    vision: str,
    looking_back: str,
    architecture: str,
    looking_ahead: str,
    beyond_bullets: list[str],
    tag: str = "mission-debrief",
) -> tuple[str, str, str]:
    """Compose a mission-class pager body.

    Order is binding: vision → look-back → architecture → look-ahead → Beyond.
    Layman prose — caller owns that; this only structures the slots.
    Architecture must name concrete ULG systems (growth map); validation lives
    in ``validate_mission_debrief_notify``.
    """
    beyond = _beyond_block(beyond_bullets)
    body = "\n\n".join(
        [
            " ".join(vision.strip().split()),
            f"Looking back: {' '.join(looking_back.strip().split())}",
            f"Architecture: {' '.join(architecture.strip().split())}",
            f"Looking ahead: {' '.join(looking_ahead.strip().split())}",
            beyond,
        ]
    )
    if len(body) > SMS_BODY_MAX:
        body = body[: SMS_BODY_MAX - 1] + "…"
    return clip(subject, SMS_SUBJECT_MAX), body, tag[:40]


def format_summons_stop_page(
    *,
    reason: str,
    mission: str,
    looking_back: str,
    architecture: str,
    looking_ahead: str,
    beyond_bullets: list[str],
) -> tuple[str, str, str]:
    """Stop/page for the overnight summons loop — always vision-framed."""
    vision = (
        f"ULG's agent fleet is supposed to keep improving how it knows what "
        f"happened and what to do next — without you babysitting the pane. "
        f"This stop is about that mission ({mission.strip() or 'operator-proxy arc'}), "
        f"not a green status light."
    )
    tag = "mission-debrief" if reason == "arc_complete" else "summons-stop"
    subject = clip(f"ULG mission loop stopped — {reason}", SMS_SUBJECT_MAX)
    return format_mission_awareness_page(
        subject=subject,
        vision=vision,
        looking_back=looking_back,
        architecture=architecture,
        looking_ahead=looking_ahead,
        beyond_bullets=beyond_bullets,
        tag=tag,
    )


def _beyond_block(bullets: list[str]) -> str:
    lines = ["Beyond this close:"]
    cleaned = [b.strip().lstrip("- ").strip() for b in bullets if b and b.strip()]
    if not cleaned:
        lines.append("- none")
    else:
        for b in cleaned:
            lines.append(f"- {b}")
    return "\n".join(lines)
