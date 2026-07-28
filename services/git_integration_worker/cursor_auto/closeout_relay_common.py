"""Shared CLOSEOUT relay primitives (payload, §2 detection, list helpers)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

_SECTION2_MARKERS = ("ac_verdict", "deltas_to_spec")
_TAIL_MARKERS = (
    "\n## effects_manifest",
    "\n## structured_closeout_full",
)
_STATUS_RE = re.compile(
    r"(?im)^(?:\*\*)?status(?:\*\*)?\s*[:=]\s*`?(complete|partial|blocked)`?"
)
_VALID_WRAPPER_STATUSES = frozenset({"complete", "partial", "blocked"})


@dataclass(frozen=True, slots=True)
class CloseoutRelayPayload:
    """Body + status line for ``TYPE: CLOSEOUT`` relay to the operator seat."""

    body: str
    status: str
    source: (
        str  # section2_sidecar | section2_bus | section2_synthesized | wrapper | empty
    )


def as_str_list(value: object) -> list[str]:
    """Return string entries from a JSON-ish list value."""
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str) and entry]


def order_preserving_dedup(*sequences: Sequence[str]) -> list[str]:
    """Merge sequences keeping first-seen order."""
    seen: set[str] = set()
    merged: list[str] = []
    for sequence in sequences:
        for item in sequence:
            if item not in seen:
                seen.add(item)
                merged.append(item)
    return merged


def table_cell(value: str) -> str:
    """Escape pipe/newline characters for markdown table cells."""
    return value.replace("|", "\\|").replace("\n", "<br>")


# Private aliases kept for call-sites that historically used underscore names.
_as_str_list = as_str_list
_order_preserving_dedup = order_preserving_dedup
_table_cell = table_cell


def is_wrapper_manifest(text: str) -> bool:
    """True when *text* is the machine SDK capture JSON (not §2 prose)."""
    raw = text.strip()
    if not raw.startswith("{"):
        return False
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    return "schema_version" in data and (
        "effects_manifest" in data
        or "files_created" in data
        or "capture_status" in data
    )


def looks_section2(text: str) -> bool:
    """True when *text* carries the load-bearing §2 field markers."""
    low = text.lower()
    return all(marker in low for marker in _SECTION2_MARKERS)


def strip_machine_tail(text: str) -> str:
    """Drop appended GIW machine sections from a repo sidecar body."""
    cut = len(text)
    for marker in _TAIL_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].rstrip()


def status_from_section2(text: str) -> str | None:
    """Extract ``complete|partial|blocked`` from authored §2 prose, if present."""
    match = _STATUS_RE.search(text)
    if match is None:
        return None
    return match.group(1).lower()


def wrapper_status(text: str) -> str | None:
    """Return wrapper manifest ``status`` when it is a known closeout value."""
    if not is_wrapper_manifest(text):
        return None
    try:
        data = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("status")
    if not isinstance(raw, str):
        return None
    normalized = raw.lower()
    if normalized in _VALID_WRAPPER_STATUSES:
        return normalized
    return None


__all__ = [
    "CloseoutRelayPayload",
    "_VALID_WRAPPER_STATUSES",
    "_as_str_list",
    "_order_preserving_dedup",
    "_table_cell",
    "as_str_list",
    "is_wrapper_manifest",
    "looks_section2",
    "order_preserving_dedup",
    "status_from_section2",
    "strip_machine_tail",
    "table_cell",
    "wrapper_status",
]
