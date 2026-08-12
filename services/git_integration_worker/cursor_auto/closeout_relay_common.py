"""Shared CLOSEOUT relay primitives (payload, §2 detection, list helpers)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_field_section,
    field_heading_present,
    status_from_section2,
)
from services.git_integration_worker.cursor_auto.lane_a_status import (
    extract_status_claim,
)

_SECTION2_MARKERS = ("ac_verdict", "deltas_to_spec")
_TAIL_MARKERS = (
    "\n## effects_manifest",
    "\n## structured_closeout_full",
)
_STATUS_RE = re.compile(
    r"(?im)^(?:\*\*)?(?:status_claim|status)(?:\*\*)?\s*[:=]\s*`?(complete|partial|blocked)`?"
)
_VALID_WRAPPER_STATUSES = frozenset({"complete", "partial", "blocked"})
_QUALIFIED_PARTIAL_STATUSES = frozenset({"partial:work", "partial:capture"})
_RELAY_MEASUREMENT_STATUSES = _VALID_WRAPPER_STATUSES | _QUALIFIED_PARTIAL_STATUSES
RELAY_PARSE_FAILED_STATUS = "relay_parse_failed"
_RELAY_INFRA_STATUSES = frozenset({RELAY_PARSE_FAILED_STATUS})
RELAY_JUDGMENT_CLAMP_FIELDS = frozenset(
    {"ac_verdict", "decisions_taken", "next", "open forks"}
)
_ENVELOPE_TYPE_RE = re.compile(r"(?im)^TYPE:\s*CLOSEOUT\s*$")
_ENVELOPE_STATUS_RE = re.compile(
    r"(?im)^status:\s*(complete|partial(?::(?:work|capture))?|blocked)\s*$"
)
_ENVELOPE_DEVIATIONS_RE = re.compile(r"(?im)^deviations:\s*.+$")
RELAY_CELL_CAP_CHARS = 400
RELAY_EXCERPT_FALLBACK_CHARS = 240
RELAY_EFFECTS_MAX_ITEMS = 10
_FENCE_PAIR_RE = re.compile(r"```[\w-]*\n", re.MULTILINE)
_DEVIATION_EFFECTS_ENRICHED = "deviation:effects_enriched_status_held"


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
    relay_note: str | None = None
    deployment_state: str | None = None


_AUTHORED_STATUS_TOKEN_RE = re.compile(
    r"^(complete|partial|blocked)\b",
    re.IGNORECASE,
)


def normalize_authored_status_value(raw: str) -> str | None:
    """Extract ``complete|partial|blocked`` from a status field value with trailing prose."""
    text = raw.strip().strip("`").strip()
    match = _AUTHORED_STATUS_TOKEN_RE.match(text)
    if match is None:
        normalized = text.casefold()
        if normalized in _VALID_WRAPPER_STATUSES:
            return normalized
        return None
    return match.group(1).lower()


def merge_relay_notes(*parts: str | None) -> str | None:
    """Join non-empty relay-note fragments with ``; ``."""
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        for token in (segment.strip() for segment in part.split(";")):
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
    return "; ".join(tokens) if tokens else None


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
    if has_ac and extract_status_claim(prose) is not None:
        return True
    low = prose.lower()
    return all(marker in low for marker in _SECTION2_MARKERS)


def relay_parse_miss_cell(field: str, provenance: str) -> str:
    """Relay-local voice when a §2 cell could not be populated — never blame the author."""
    return f"relay could not locate `{field}` — see source_ref: {provenance}"


def unclassified_relay_prefix(*, provenance: str, body: str) -> str:
    """Relay-local parse failure when substance was read but §2 fields did not extract."""
    del body  # nbytes retained for backward-compatible call sites; URI is authoritative
    return f"parse_failed — authoritative sidecar: {provenance}"


def looks_fenced(value: str) -> bool:
    """True when *value* carries markdown fence markers unsuitable for table cells."""
    stripped = value.strip()
    if stripped.startswith("```"):
        return True
    return _FENCE_PAIR_RE.search(value) is not None


def is_degenerate_fence_cell(value: str) -> bool:
    """True when a table cell is only an opening fence or truncated fence opener."""
    stripped = value.strip()
    if stripped == "```":
        return True
    lowered = stripped.casefold()
    if "full text:" in lowered or "truncated:" in lowered or "fenced —" in lowered:
        return False
    if stripped.startswith("```") and _FENCE_PAIR_RE.search(stripped) is None:
        return True
    return False


def fenced_cell_pointer(provenance: str) -> str:
    """Honest table-cell substitute when fenced content cannot be inlined."""
    return f"fenced — see source_ref: {provenance}"


def sanitize_relay_cell(value: str, provenance: str) -> str:
    """Ensure relay cell text is never a stray fence opener."""
    if looks_fenced(value):
        return fenced_cell_pointer(provenance)
    if is_degenerate_fence_cell(value):
        if "://" in provenance:
            return f"truncated: … (full text: {provenance})"
        return f"truncated: … (full: {provenance})"
    return value


def default_relay_cell_cap(value: str, provenance: str) -> str:
    """Cap extracted relay cell values — degrade to pointer, never mid-token cut."""
    if looks_fenced(value):
        return fenced_cell_pointer(provenance)
    if len(value) <= RELAY_CELL_CAP_CHARS:
        return sanitize_relay_cell(value, provenance)
    if "://" in provenance:
        return f"truncated: … (full text: {provenance})"
    trimmed = f"{value[:RELAY_CELL_CAP_CHARS].rsplit(' ', 1)[0]}… (full: {provenance})"
    return sanitize_relay_cell(trimmed, provenance)


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
            f"parse_failed — could not extract §2 field `{field}` "
            f"(authoritative sidecar: {provenance})"
        )
    return relay_parse_miss_cell(field, provenance)


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
        return relay_parse_miss_cell("ac_verdict", provenance)
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


_STRUCTURED_CLOSEOUT_FULL_HEADING = _TAIL_MARKERS[1].lstrip("\n")


def extract_structured_closeout_full_text(sidecar_text: str | None) -> str | None:
    """Return raw JSON text from a sidecar ``## structured_closeout_full`` section."""
    if not sidecar_text:
        return None
    marker = _TAIL_MARKERS[1]
    idx = sidecar_text.find(marker)
    if idx == -1:
        if sidecar_text.lstrip().startswith(_STRUCTURED_CLOSEOUT_FULL_HEADING):
            idx = sidecar_text.find(_STRUCTURED_CLOSEOUT_FULL_HEADING)
        else:
            return None
    remainder = sidecar_text[idx + len(marker) :].lstrip("\n")
    if not remainder.strip():
        return None
    cut = len(remainder)
    for tail_marker in _TAIL_MARKERS:
        if tail_marker == marker:
            continue
        pos = remainder.find(tail_marker)
        if pos != -1:
            cut = min(cut, pos)
    json_text = remainder[:cut].strip()
    return json_text or None


def strip_projected_closeout_envelope(body: str) -> str:
    """Drop inner ``TYPE: CLOSEOUT`` header lines — outer relay owns one envelope.

    ``plane:`` / ``plane-discrepancy:`` body lines are never stripped — transport
    preserves unknown and definite plane states verbatim
    (``[universal:executor-rec]`` shape; closeout-plane-legibility).
    """
    lines = body.splitlines()
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            continue
        if (
            _ENVELOPE_TYPE_RE.match(stripped)
            or _ENVELOPE_STATUS_RE.match(stripped)
            or _ENVELOPE_DEVIATIONS_RE.match(stripped)
        ):
            idx += 1
            continue
        break
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    return "\n".join(lines[idx:])


def relay_parse_failure_detected(body: str) -> bool:
    """True when relay cells report §2 extraction failure (not honest field absence)."""
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        count_unclassified_fields,
    )

    if count_unclassified_fields(body) > 0:
        return True
    lowered = body.casefold()
    return "parse_failed —" in lowered or "parse_failed—" in lowered


def structured_status_to_relay(raw: str) -> str:
    """Map structured B3 status tokens to relay envelope vocabulary."""
    normalized = raw.strip().lower()
    if normalized in {"failed", "gated"}:
        return "blocked"
    if normalized in _VALID_WRAPPER_STATUSES:
        return normalized
    return "partial"


def resolve_measurement_status_from_wrapper(wrapper_text: str | None) -> str | None:
    """Return uncapped machine grade from ImplementCloseout JSON (B3 → B2 source)."""
    if not wrapper_text or not is_wrapper_manifest(wrapper_text):
        return None
    try:
        payload = json.loads(wrapper_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    from services.git_integration_worker.cursor_auto.closeout_status_polarity import (
        incomplete_class_from_wrapper,
        resolve_qualified_measurement_status,
    )

    disagreement = payload.get("status_authority_disagreement")
    base: str | None = None
    if isinstance(disagreement, dict):
        machine = disagreement.get("machine_status")
        if isinstance(machine, str) and machine.strip():
            base = structured_status_to_relay(machine)
    if base is None:
        status = payload.get("status")
        if isinstance(status, str) and status.strip():
            base = structured_status_to_relay(status)
    if base is None:
        return None
    incomplete_class = incomplete_class_from_wrapper(payload)
    return resolve_qualified_measurement_status(
        base_status=base,
        wrapper_text=None,
        incomplete_class=incomplete_class,
    )


def resolve_measurement_status(
    *,
    wrapper_text: str | None,
    ledger_fallback: str,
    sidecar_text: str | None = None,
) -> str:
    """Measurement control line for envelope ``status:`` — B3/ledger, not §2 claim."""
    measured = resolve_measurement_status_from_wrapper(wrapper_text)
    if sidecar_text and (measured is None or measured == "partial"):
        structured = extract_structured_closeout_full_text(sidecar_text)
        if structured:
            sidecar_measured = resolve_measurement_status_from_wrapper(structured)
            if sidecar_measured is not None:
                if measured is None:
                    measured = sidecar_measured
                elif sidecar_measured != "partial":
                    measured = sidecar_measured
    if measured is not None:
        return measured
    normalized = ledger_fallback.strip().lower()
    if normalized in _RELAY_MEASUREMENT_STATUSES | _RELAY_INFRA_STATUSES:
        return normalized
    return "partial"


def resolve_relay_status(body: str, measurement_status: str) -> str:
    """Return measurement control-line status for envelope ``status:`` (B2).

    §2 ``status_claim`` is extracted separately for divergence annotation;
    envelope ``status:`` must not prefer the agent claim over measurement.
    """
    del body  # claim lives in §2; envelope reads measurement only (arc 7070).
    normalized = measurement_status.strip().lower()
    if normalized == RELAY_PARSE_FAILED_STATUS:
        return RELAY_PARSE_FAILED_STATUS
    if normalized in _RELAY_MEASUREMENT_STATUSES:
        return normalized
    return "partial"


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
    "_DEVIATION_EFFECTS_ENRICHED",
    "default_relay_cell_cap",
    "extract_structured_closeout_full_text",
    "fenced_cell_pointer",
    "fill_judgment_cell",
    "is_degenerate_fence_cell",
    "looks_fenced",
    "sanitize_relay_cell",
    "has_closeout_substance",
    "is_wrapper_manifest",
    "looks_section2",
    "order_preserving_dedup",
    "RELAY_JUDGMENT_CLAMP_FIELDS",
    "merge_relay_notes",
    "normalize_authored_status_value",
    "relay_parse_failure_detected",
    "relay_parse_miss_cell",
    "RELAY_PARSE_FAILED_STATUS",
    "resolve_measurement_status",
    "resolve_measurement_status_from_wrapper",
    "resolve_relay_status",
    "structured_status_to_relay",
    "status_from_section2",
    "strip_projected_closeout_envelope",
    "strip_machine_tail",
    "table_cell",
    "unclassified_relay_prefix",
    "wrapper_status",
]
