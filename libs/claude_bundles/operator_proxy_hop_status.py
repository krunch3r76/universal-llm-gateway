"""Standing-handoff and bus projection for the hop-successor prompt.

``ensure_operator_proxy_mission_prompt`` fills the template from HOUSE RULES,
a live leg projected from agent-bus state, and pointer lines. This module does
not author the old This-hop card.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from typing import Any

from hop_handoff.body import parse_successor_birth_id
from hop_handoff.standing_handoff import standing_handoff_path

from claude_bundles.operator_proxy_live_leg import (
    POINTERS_BLOCK,
    house_lanes,
    load_maestro_roster_text,
    project_live_leg,
    render_live_leg,
)

# CONSUMERS = import-nomination (GIW). INJECTORS = seat paste (cdp_ask).
CONSUMERS: tuple[str, ...] = ("git_integration_worker",)
INJECTORS: tuple[str, ...] = ("cdp_ask",)

UNKNOWN_FIELD = "unknown"
MISSING_HANDOFF_INSTRUCTION = (
    "The S7 standing-handoff state file is absent.\n"
    "Lane-tip reconstruction is degraded, not equivalent.\n"
    "Author the standing handoff before you leave."
)

_THREAD_RE = re.compile(
    r"^(?:thread_id|parent_thread):\s*(\d+)\s*$",
    re.MULTILINE,
)
_ARC_RE = re.compile(r"^arc:\s*(?:agent-bus:)?(\d+)\s*$", re.MULTILINE)
_LANE_ID_RE = re.compile(r"^lane:\s*(?:agent-bus[:\s]+)?(\d+)", re.MULTILINE)
_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_HOUSE_RULES_HEADING = "HOUSE RULES"


def extract_thread_id(text: str) -> str | None:
    """Return the private-lane thread id when the prompt names one.

    Reads ``thread_id`` / ``parent_thread`` first, then ``lane: agent-bus…``,
    then ``arc:``. Does not invent an id from prose.
    """
    for pattern in (_THREAD_RE, _LANE_ID_RE, _ARC_RE):
        match = pattern.search(text or "")
        if match:
            return match.group(1)
    return None


def standing_handoff_text_for_prompt(
    prompt: str,
    *,
    read_path: Callable[[str], str | None] | None = None,
) -> str | None:
    """Load the standing-handoff sidecar for the thread id named in *prompt*.

    Returns None when no thread id is parseable or the file is absent.
    *read_path* is a test seam; production reads ``standing_handoff_path``.
    """
    thread_id = extract_thread_id(prompt)
    if not thread_id:
        return None
    if read_path is not None:
        return read_path(thread_id)
    path = standing_handoff_path(thread_id)
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        return None
    return None


def _heading_sections(text: str) -> list[tuple[str, str]]:
    """ATX sections as ``(heading, section text including the heading)``."""
    lines = (text or "").splitlines()
    starts: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = _ATX_HEADING_RE.match(line)
        if match is None:
            continue
        starts.append((index, len(match.group(1)), match.group(2).strip()))
    sections: list[tuple[str, str]] = []
    for number, (index, level, heading) in enumerate(starts):
        end = len(lines)
        for later in range(number + 1, len(starts)):
            if starts[later][1] <= level:
                end = starts[later][0]
                break
        sections.append((heading, "\n".join(lines[index:end]).rstrip()))
    return sections


def house_rules_section(text: str) -> str | None:
    """Return the HOUSE RULES block from the standing handoff, verbatim."""
    for heading, body in _heading_sections(text or ""):
        if _HOUSE_RULES_HEADING in heading:
            return body
    return None


def handoff_file_sha256(text: str) -> str:
    """sha256 of the whole sidecar text, not of the extracted section."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def hop_successor_fields(
    prompt: str,
    *,
    standing_handoff_text: str | None = None,
    roster_text: str | None = None,
    fetch_turns: Callable[[str], list[dict[str, Any]]] | None = None,
    execution_id: str = "",
) -> dict[str, str]:
    """Fill the hop-successor template from the prompt, handoff, and bus state."""
    lane = extract_thread_id(prompt or "") or UNKNOWN_FIELD
    birth = parse_successor_birth_id(prompt or "") or UNKNOWN_FIELD
    sidecar = standing_handoff_text or ""
    exec_id = (execution_id or "").strip()
    execution_clause = f", execution_id {exec_id}" if exec_id else ""

    roster = roster_text if roster_text is not None else load_maestro_roster_text()
    lanes = house_lanes(roster)
    if fetch_turns is None:
        from claude_bundles.operator_proxy_bus_fetch import fetch_turns_for_lane

        fetch_turns = fetch_turns_for_lane

    leg = project_live_leg(lanes, fetch_turns)
    live_leg = render_live_leg(leg)
    lane_list = ", ".join(lanes) if lanes else "none"

    if sidecar:
        digest = handoff_file_sha256(sidecar)
        running_now = (
            "## What is running now (computed at render from agent-bus state on "
            f"lanes {lane_list}; handoff file sha256 at render {digest})"
        )
        rules = house_rules_section(sidecar)
        house_rules_verbatim = rules if rules is not None else ""
    else:
        digest = ""
        running_now = (
            "## What is running now (computed at render from agent-bus state on "
            f"lanes {lane_list}; standing handoff file absent)"
        )
        house_rules_verbatim = MISSING_HANDOFF_INSTRUCTION

    return {
        "lane": lane,
        "successor_birth_id": birth,
        "execution_clause": execution_clause,
        "handoff_sha": digest,
        "running_now_line": running_now,
        "house_rules_verbatim": house_rules_verbatim,
        "live_leg": live_leg,
        "pointers": POINTERS_BLOCK,
    }


__all__ = [
    "MISSING_HANDOFF_INSTRUCTION",
    "UNKNOWN_FIELD",
    "extract_thread_id",
    "handoff_file_sha256",
    "hop_successor_fields",
    "house_rules_section",
    "standing_handoff_text_for_prompt",
]
