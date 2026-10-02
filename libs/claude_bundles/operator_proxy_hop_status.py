"""Standing-handoff section copy for the hop-successor prompt.

``ensure_operator_proxy_mission_prompt`` fills the template from the newest
consecutive CURRENT sections of the standing-handoff sidecar. This module
does not author the old This-hop card.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable

from hop_handoff.body import parse_successor_birth_id
from hop_handoff.standing_handoff import standing_handoff_path

# CONSUMERS = import-nomination (GIW). INJECTORS = seat paste (cdp_ask).
CONSUMERS: tuple[str, ...] = ("git_integration_worker",)
INJECTORS: tuple[str, ...] = ("cdp_ask",)

UNKNOWN_FIELD = "unknown"
_CURRENT_SECTION_CAP = 4
_CAP_NOTE = "consolidated section not found; read the handoff head"
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


def _section_is_stop(heading: str, body: str) -> bool:
    if "supersedes" in heading:
        return True
    return any(line.strip().startswith("IN FLIGHT") for line in body.splitlines())


def newest_current_section(text: str) -> tuple[str, str] | None:
    """Consecutive CURRENT sections from the newest, through the first stop.

    Standing handoffs prepend, so the first ``CURRENT`` heading is the newest.
    Copy continues through the next CURRENT headings and includes the first
    section whose heading contains ``supersedes`` or whose body has a line
    starting ``IN FLIGHT``. A non-CURRENT heading ends the run. At most four
    sections are copied; when a fifth would have been copied, the verbatim
    ends with ``consolidated section not found; read the handoff head``.
    """
    sections = _heading_sections(text)
    start: int | None = None
    for index, (heading, _body) in enumerate(sections):
        if "CURRENT" in heading:
            start = index
            break
    if start is None:
        return None
    chosen: list[str] = []
    newest_heading = ""
    capped = False
    for heading, body in sections[start:]:
        if "CURRENT" not in heading:
            break
        if len(chosen) >= _CURRENT_SECTION_CAP:
            capped = True
            break
        if not newest_heading:
            newest_heading = heading
        chosen.append(body)
        if _section_is_stop(heading, body):
            break
    if not chosen or not newest_heading:
        return None
    verbatim = "\n\n".join(chosen)
    if capped:
        verbatim = f"{verbatim}\n{_CAP_NOTE}"
    return newest_heading, verbatim


def handoff_file_sha256(text: str) -> str:
    """sha256 of the whole sidecar text, not of the extracted section."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def hop_successor_fields(
    prompt: str,
    *,
    standing_handoff_text: str | None = None,
    execution_id: str = "",
) -> dict[str, str]:
    """Fill the hop-successor template from the prompt and standing handoff.

    ``execution_id`` is the satellite's Stargate id. An empty id omits the
    birth-record clause. ``cdp_dispatch_id`` and ``birth_turn`` are not
    rendered. A missing CURRENT section is the absent-file instruction.
    """
    lane = extract_thread_id(prompt or "") or UNKNOWN_FIELD
    birth = parse_successor_birth_id(prompt or "") or UNKNOWN_FIELD
    sidecar = standing_handoff_text or ""
    section = newest_current_section(sidecar)
    exec_id = (execution_id or "").strip()
    execution_clause = f", execution_id {exec_id}" if exec_id else ""
    if section is None:
        heading = "absent"
        verbatim = MISSING_HANDOFF_INSTRUCTION
        running_now = "## What is running now (standing handoff file absent)"
        digest = ""
    else:
        heading, verbatim = section
        digest = handoff_file_sha256(sidecar)
        running_now = (
            "## What is running now (copied whole from the standing handoff "
            f"head, {heading}, handoff file sha256 at render {digest})"
        )
    return {
        "lane": lane,
        "successor_birth_id": birth,
        "execution_clause": execution_clause,
        "handoff_section_heading": heading,
        "handoff_sha": digest,
        "running_now_line": running_now,
        "handoff_current_section_verbatim": verbatim,
    }


__all__ = [
    "MISSING_HANDOFF_INSTRUCTION",
    "UNKNOWN_FIELD",
    "extract_thread_id",
    "handoff_file_sha256",
    "hop_successor_fields",
    "newest_current_section",
    "standing_handoff_text_for_prompt",
]
