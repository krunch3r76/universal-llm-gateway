"""This-hop status block — first visible heading on operator-proxy missions.

Cowork renders the first markdown heading after skill chips as the dispatch
card. Operator-proxy submits used to open on the seat-map briefing, so the
human saw doctrine instead of the job. This module hoists a four-line
settled/live/next/lane block above that briefing.

Callers: ``ensure_operator_proxy_mission_prompt`` (pure string transform) and
``cdp_ask.runner.resolve_prompt`` (optional standing-handoff sidecar fill).
An existing This-hop block already above the seat map is left byte-stable.
``(unspecified)`` is an honest hole, not idle and not invented progress.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from hop_handoff.standing_handoff import standing_handoff_path

# CONSUMERS = import-nomination (GIW). INJECTORS = seat paste (cdp_ask).
CONSUMERS: tuple[str, ...] = ("git_integration_worker",)
INJECTORS: tuple[str, ...] = ("cdp_ask",)

HOP_STATUS_MARKER = "## This hop (read first)"
UNSPECIFIED = "(unspecified)"
_MAX_FIELD = 120
_SEAT_MAP_MARKER = "## Mission seat map (BINDING"

_FIELD_RE = re.compile(
    r"^(settled|live|next|lane):\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_THREAD_RE = re.compile(
    r"^(?:thread_id|parent_thread):\s*(\d+)\s*$",
    re.MULTILINE,
)
_ARC_RE = re.compile(r"^arc:\s*(?:agent-bus:)?(\d+)\s*$", re.MULTILINE)
_LANE_ID_RE = re.compile(r"^lane:\s*(?:agent-bus[:\s]+)?(\d+)", re.MULTILINE)
_TRIGGER_RE = re.compile(r"^trigger:\s*(.+?)\s*$", re.MULTILINE)
_SH_URI_RE = re.compile(r"^standing_handoff:\s*(cortex://\S+)\s*$", re.MULTILINE)
_SH_FRESH_RE = re.compile(
    r"^standing_handoff_freshness:\s*(\S+)\s*$",
    re.MULTILINE,
)
_LIVE_HEADING_RE = re.compile(
    r"^##\s+(?:Live|LIVE)(?:\s*[—–-].*)?$",
    re.MULTILINE,
)
_SETTLED_HEADING_RE = re.compile(
    r"^##\s+Settled\b.*$",
    re.MULTILINE | re.IGNORECASE,
)
_NEXT_HEADING_RE = re.compile(
    r"^##\s+(?:First next act|The work)\b.*$",
    re.MULTILINE | re.IGNORECASE,
)


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
    I/O stays here so ``ensure_hop_status_first`` remains a string transform.
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


def ensure_hop_status_first(
    rest_body: str,
    *,
    standing_handoff_text: str | None = None,
    field_source: str | None = None,
) -> str:
    """Guarantee ``This hop`` sits above the seat map. Idempotent.

    When the marker is already the first heading and precedes the seat map,
    *rest_body* is returned unchanged (no sidecar re-fill). Missing blocks
    are authored from *field_source* (default: the body minus an existing
    hop block), then *standing_handoff_text*, then ``(unspecified)``.
    Pass the pre-briefing caller as *field_source* so seat-map doctrine
    cannot become the ``next:`` line.
    """
    body = rest_body or ""
    existing, remainder = _split_hop_block(body)
    if existing is not None and _hop_already_first(body):
        return body
    if existing is not None:
        return f"{existing.rstrip()}\n\n{remainder.lstrip()}"
    source = remainder if field_source is None else field_source
    block = _format_hop_status(
        _collect_fields(source, standing_handoff_text=standing_handoff_text)
    )
    return f"{block}\n{remainder.lstrip()}"


def _hop_already_first(body: str) -> bool:
    stripped = body.lstrip()
    if not stripped.startswith(HOP_STATUS_MARKER):
        return False
    seat = stripped.find(_SEAT_MAP_MARKER)
    return seat < 0 or stripped.find(HOP_STATUS_MARKER) < seat


def _split_hop_block(text: str) -> tuple[str | None, str]:
    idx = text.find(HOP_STATUS_MARKER)
    if idx < 0:
        return None, text
    after = text[idx + len(HOP_STATUS_MARKER) :]
    next_heading = re.search(r"^## ", after, re.MULTILINE)
    if next_heading:
        end = idx + len(HOP_STATUS_MARKER) + next_heading.start()
        block = text[idx:end]
        rest = text[:idx] + after[next_heading.start() :]
    else:
        block = text[idx:]
        rest = text[:idx]
    return block.strip() + "\n", rest


def _collect_fields(
    text: str,
    *,
    standing_handoff_text: str | None,
) -> dict[str, str]:
    fields = {key: UNSPECIFIED for key in ("settled", "live", "next", "lane")}
    _fill_from_labeled_lines(fields, text)
    _fill_from_continuity_headers(fields, text)
    _fill_next_from_caller_body(fields, text)
    if standing_handoff_text:
        _fill_from_standing_handoff(fields, standing_handoff_text)
    return fields


def _fill_from_labeled_lines(fields: dict[str, str], text: str) -> None:
    for match in _FIELD_RE.finditer(text):
        key = match.group(1).lower()
        value = _clip(match.group(2))
        if value and fields[key] == UNSPECIFIED:
            fields[key] = value


def _fill_from_continuity_headers(fields: dict[str, str], text: str) -> None:
    thread_id = extract_thread_id(text)
    if thread_id and fields["lane"] == UNSPECIFIED:
        fields["lane"] = f"agent-bus:{thread_id}"
    trigger = _TRIGGER_RE.search(text)
    if trigger and fields["live"] == UNSPECIFIED:
        fields["live"] = _clip(f"continuity hop — {trigger.group(1)}")
    uri = _SH_URI_RE.search(text)
    fresh = _SH_FRESH_RE.search(text)
    if uri and fields["next"] == UNSPECIFIED:
        suffix = f" ({fresh.group(1)})" if fresh else ""
        fields["next"] = _clip(f"read {uri.group(1)}{suffix}")


def _fill_next_from_caller_body(fields: dict[str, str], text: str) -> None:
    if fields["next"] != UNSPECIFIED:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("##") or line.startswith("|"):
            continue
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            if title and title.lower() not in {"mission", "handoff"}:
                fields["next"] = _clip(title)
                return
            continue
        if line.startswith("/") or line.startswith("TYPE:"):
            continue
        if re.match(r"^[a-z_][a-z0-9_]*:", line, re.I):
            if line.lower().startswith("subject:"):
                fields["next"] = _clip(line.split(":", 1)[1])
                return
            continue
        fields["next"] = _clip(line)
        return


def _fill_from_standing_handoff(fields: dict[str, str], sidecar: str) -> None:
    if fields["lane"] == UNSPECIFIED:
        lane_line = _LANE_ID_RE.search(sidecar)
        if lane_line:
            fields["lane"] = f"agent-bus:{lane_line.group(1)}"
        else:
            labeled = re.search(r"^lane:\s+(.+)$", sidecar, re.MULTILINE)
            if labeled:
                fields["lane"] = _clip(labeled.group(1).split(" · ", 1)[0])
    mapping = (
        ("settled", _SETTLED_HEADING_RE),
        ("live", _LIVE_HEADING_RE),
        ("next", _NEXT_HEADING_RE),
    )
    for key, heading in mapping:
        if fields[key] != UNSPECIFIED:
            continue
        excerpt = _section_first_line(sidecar, heading)
        if excerpt:
            fields[key] = excerpt


def _section_first_line(text: str, heading: re.Pattern[str]) -> str | None:
    match = heading.search(text)
    if not match:
        return None
    for raw in text[match.end() :].splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            return None
        if line.startswith("|") and set(line.replace("|", "").strip()) <= {"-", ":"}:
            continue
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|") if cell.strip()]
            line = " — ".join(cells)
        return _clip(line)
    return None


def _format_hop_status(fields: dict[str, str]) -> str:
    return (
        f"{HOP_STATUS_MARKER}\n"
        f"- settled: {fields['settled']}\n"
        f"- live: {fields['live']}\n"
        f"- next: {fields['next']}\n"
        f"- lane: {fields['lane']}\n"
    )


def _clip(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", (value or "").strip())
    if len(collapsed) <= _MAX_FIELD:
        return collapsed
    return collapsed[: _MAX_FIELD - 1].rstrip() + "…"


__all__ = [
    "HOP_STATUS_MARKER",
    "UNSPECIFIED",
    "ensure_hop_status_first",
    "extract_thread_id",
    "standing_handoff_text_for_prompt",
]
