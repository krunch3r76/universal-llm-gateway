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

from hop_handoff.body import parse_successor_birth_id, parse_superseded_registration_id
from hop_handoff.standing_handoff import standing_handoff_path

from claude_bundles.journal_hop_projection import hop_journal_projection_fields
from claude_bundles.operator_proxy_live_leg import (
    FoldStateSaver,
    FoldStateStore,
    house_lanes,
    load_maestro_roster_text,
    project_live_leg,
    render_live_leg,
)
from claude_bundles.operator_proxy_standing_render import (
    compute_preamble_over_inflight_flag,
    render_checkpoint_block,
    render_house_rules_block,
    render_inflight_block,
    render_pointers_block,
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
    journal_text: str | None = None,
    register_text: str | None = None,
    read_cortex_uri: Callable[[str], str | None] | None = None,
    inflight_projection_lookup: Callable[[], str | None] | None = None,
    role: str = "maestro",
    house_rules_text: str | None = None,
    lineage_lookup: Callable[[str, str], str | None] | None = None,
    archive_path_exists: Callable[[str], bool] | None = None,
    page_fetch: Callable[..., tuple[list[dict[str, Any]], bool, int | None]]
    | None = None,
    fold_state_load: FoldStateStore | None = None,
    fold_state_save: FoldStateSaver | None = None,
    act5_fetch_last: int | None = None,
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

    superseded_reg = parse_superseded_registration_id(prompt or "")
    inline_rules = house_rules_section(sidecar) if sidecar else None

    checkpoint_block = ""
    inflight_block = ""
    inflight_count = 0
    checkpoint_preamble = ""
    if sidecar:
        checkpoint_block, ck_meta = render_checkpoint_block(
            sidecar,
            lane=lane,
            inline_house_rules=inline_rules,
            path_exists=archive_path_exists,
            superseded_registration_id=superseded_reg,
        )
        inflight_rows = ck_meta.get("inflight_rows") or []
        inflight_count = len(inflight_rows)
        if inflight_rows:
            inflight_block = render_inflight_block(
                inflight_rows,
                lineage_lookup=lineage_lookup,
            )
        checkpoint_preamble = checkpoint_block
        if ck_meta.get("identity") == "stale":
            act5_fetch_last = 5

    if not sidecar:
        house_rules_verbatim = MISSING_HANDOFF_INSTRUCTION
    else:
        house_rules_verbatim, _rules_budget = render_house_rules_block(
            lane=lane,
            inline_rules=inline_rules,
            house_rules_text=house_rules_text,
            read_cortex_uri=read_cortex_uri,
        )

    leg = project_live_leg(
        lanes,
        fetch_turns,
        page_fetch=page_fetch,
        fold_state_load=fold_state_load,
        fold_state_save=fold_state_save,
    )
    live_leg = render_live_leg(leg, declared_in_flight=inflight_count)
    lane_list = ", ".join(lanes) if lanes else "none"

    if sidecar:
        digest = handoff_file_sha256(sidecar)
        running_now = (
            "## What is running now (computed at render from agent-bus state on "
            f"lanes {lane_list}; handoff file sha256 at render {digest})"
        )
    else:
        digest = ""
        running_now = (
            "## What is running now (computed at render from agent-bus state on "
            f"lanes {lane_list}; standing handoff file absent)"
        )

    pointers = render_pointers_block(
        checkpoint_preamble=checkpoint_preamble,
        rules_text=house_rules_verbatim,
    )

    preamble_parts = [
        running_now,
        house_rules_verbatim,
        checkpoint_block,
    ]
    preamble_staged = "\n\n".join(p for p in preamble_parts if p)
    inflight_flag = compute_preamble_over_inflight_flag(preamble_staged, inflight_count)
    if inflight_flag:
        checkpoint_block = (checkpoint_block + "\n" + inflight_flag).strip()

    journal_fields = hop_journal_projection_fields(
        journal_text=journal_text,
        register_text=register_text,
        read_uri=read_cortex_uri,
        role=role,
        inflight_projection_lookup=inflight_projection_lookup,
    )

    fetch_last = act5_fetch_last if act5_fetch_last is not None else 3

    return {
        "lane": lane,
        "successor_birth_id": birth,
        "execution_clause": execution_clause,
        "handoff_sha": digest,
        "running_now_line": running_now,
        "house_rules_verbatim": house_rules_verbatim,
        "checkpoint_block": checkpoint_block,
        "inflight_block": inflight_block,
        "lessons_in_force": journal_fields["lessons_in_force"],
        "register_card": journal_fields["register_card"],
        "live_leg": live_leg,
        "pointers": pointers,
        "fetch_last": str(fetch_last),
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
