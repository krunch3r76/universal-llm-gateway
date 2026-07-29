"""Shared CLOSEOUT relay primitives (payload, §2 detection, list helpers)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_field_section,
    extract_status,
    field_heading_present,
    status_from_section2,
)

_SECTION2_MARKERS = ("ac_verdict", "deltas_to_spec")
_TAIL_MARKERS = (
    "\n## effects_manifest",
    "\n## structured_closeout_full",
)
_STATUS_RE = re.compile(
    r"(?im)^(?:\*\*)?status(?:\*\*)?\s*[:=]\s*`?(complete|partial|blocked)`?"
)
_VALID_WRAPPER_STATUSES = frozenset({"complete", "partial", "blocked"})
RELAY_CELL_CAP_CHARS = 400
RELAY_EXCERPT_FALLBACK_CHARS = 240
RELAY_EFFECTS_MAX_ITEMS = 10


@dataclass(frozen=True, slots=True)
class CloseoutRelayPayload:
    """Body + status line for ``TYPE: CLOSEOUT`` relay to the operator seat."""

    body: str
    status: str
    source: (
        str  # section2_sidecar | section2_bus | section2_synthesized | wrapper | empty
    )
    body_full: str | None = None
    clamped: bool = False


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


def has_closeout_substance(text: str) -> bool:
    """True when *text* carries non-empty executor closeout prose."""
    return bool(strip_machine_tail(text).strip())


def looks_section2(text: str) -> bool:
    """True when *text* carries load-bearing §2 judgment anchors.

    Structural field extraction is authoritative; legacy marker substrings
    remain as a compatibility fast-path for authored inline §2.
    """
    prose = strip_machine_tail(text)
    if not prose.strip():
        return False
    has_ac = extract_field_section(prose, "ac_verdict") is not None
    has_deltas = extract_field_section(prose, "deltas_to_spec") is not None
    if has_ac and has_deltas:
        return True
    if has_ac and extract_status(prose) is not None:
        return True
    low = prose.lower()
    return all(marker in low for marker in _SECTION2_MARKERS)


def unclassified_relay_prefix(*, provenance: str, body: str) -> str:
    """Relay-local uncertainty when substance was obtained but §2 is unparsed."""
    nbytes = len(body.encode("utf-8"))
    return f"unclassified — relay could not parse §2 from {nbytes} bytes at {provenance}"


def default_relay_cell_cap(value: str, provenance: str) -> str:
    """Cap extracted relay cell values — degrade to pointer, never mid-token cut."""
    if len(value) <= RELAY_CELL_CAP_CHARS:
        return value
    if "://" in provenance:
        return f"(full text: {provenance})"
    return f"{value[:RELAY_CELL_CAP_CHARS].rsplit(' ', 1)[0]}… (full: {provenance})"


def fill_judgment_cell(
    body: str,
    field: str,
    *,
    provenance: str,
    cap: Callable[[str, str], str] | None = default_relay_cell_cap,
) -> str:
    """Return extracted field text or relay uncertainty — never false absence."""
    extracted = extract_field_section(body, field)
    if extracted:
        return cap(extracted, provenance) if cap is not None else extracted
    if has_closeout_substance(body) and field_heading_present(body, field):
        return (
            f"unclassified — relay could not parse §2 field `{field}` "
            f"from substance at {provenance}"
        )
    if has_closeout_substance(body):
        return "unauthored — not reported by executor"
    return "unauthored — not reported by executor"


def build_ac_verdict_cell(
    body: str,
    *,
    provenance: str,
    cap: Callable[[str, str], str] | None = default_relay_cell_cap,
    max_excerpt_chars: int = RELAY_EXCERPT_FALLBACK_CHARS,
) -> str:
    """Build ``ac_verdict`` without asserting executor absence when substance exists."""
    extracted = extract_field_section(body, "ac_verdict")
    if extracted:
        value = cap(extracted, provenance) if cap is not None else extracted
        return value
    excerpt = strip_machine_tail(body).strip()
    if not excerpt:
        return (
            "unauthored — executor emitted no §2 body; machine-derived envelope below. "
            "Not a pass."
        )
    prefix = unclassified_relay_prefix(provenance=provenance, body=body)
    if "://" in provenance:
        combined = prefix
    else:
        if len(excerpt) > max_excerpt_chars:
            excerpt = excerpt[:max_excerpt_chars] + "…"
        combined = f"{prefix}<br><br>{excerpt}"
    return cap(combined, provenance) if cap is not None else combined


def strip_machine_tail(text: str) -> str:
    """Drop appended GIW machine sections from a repo sidecar body."""
    cut = len(text)
    for marker in _TAIL_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].rstrip()


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
    "RELAY_CELL_CAP_CHARS",
    "RELAY_EFFECTS_MAX_ITEMS",
    "RELAY_EXCERPT_FALLBACK_CHARS",
    "_VALID_WRAPPER_STATUSES",
    "_as_str_list",
    "_order_preserving_dedup",
    "_table_cell",
    "as_str_list",
    "build_ac_verdict_cell",
    "default_relay_cell_cap",
    "fill_judgment_cell",
    "has_closeout_substance",
    "is_wrapper_manifest",
    "looks_section2",
    "order_preserving_dedup",
    "status_from_section2",
    "strip_machine_tail",
    "table_cell",
    "unclassified_relay_prefix",
    "wrapper_status",
]
