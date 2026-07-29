"""Project authored §2 prose to a normalized relay table — zero silent field loss."""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    _table_cell,
    build_ac_verdict_cell,
    fill_judgment_cell,
    status_from_section2,
    strip_machine_tail,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_field_section,
    extract_status,
    extract_table_field,
    field_heading_present,
)

_SECTION2_FIELDS: tuple[tuple[str, str], ...] = (
    ("status", "status"),
    ("ac_verdict", "ac_verdict"),
    ("deltas_to_spec", "deltas_to_spec"),
    ("decisions_taken", "decisions_taken"),
    ("effects", "effects"),
    ("evidence", "evidence"),
    ("next", "next"),
    ("open forks", "open forks"),
)

_UNCLASSIFIED_RE = re.compile(
    r"unclassified\s*[—-]\s*relay could not parse",
    re.IGNORECASE,
)


def count_unclassified_fields(body: str) -> int:
    """Return how many relay cells report §2 parse uncertainty."""
    return len(_UNCLASSIFIED_RE.findall(body))


_RELAY_FIELD_DEFAULTS: dict[str, str] = {
    "next": "unauthored — operator must derive from effects above",
    "open forks": "none — field not authored in §2 sidecar",
    "effects": "none captured — see machine envelope below",
    "evidence": "none — see machine envelope below",
    "deltas_to_spec": "none — field not authored in §2 sidecar",
    "decisions_taken": "none — field not authored in §2 sidecar",
}


def _extract_cell(prose: str, field: str, *, provenance: str) -> str:
    if field == "status":
        value = extract_status(prose)
        return value or ""
    if field == "ac_verdict":
        return build_ac_verdict_cell(prose, provenance=provenance, cap=None)
    direct = extract_field_section(prose, field) or extract_table_field(prose, field)
    if direct:
        return direct
    if not field_heading_present(prose, field) and field in _RELAY_FIELD_DEFAULTS:
        return _RELAY_FIELD_DEFAULTS[field]
    return fill_judgment_cell(prose, field, provenance=provenance, cap=None)


def project_section2_table(
    prose: str,
    *,
    provenance: str,
    fallback_status: str = "partial",
) -> tuple[str, str]:
    """Build normalized §2 table from authored prose.

    Returns ``(body, status)`` with one consistent status across header and table.
    """
    text = strip_machine_tail(prose).strip()
    status = extract_status(text) or status_from_section2(text) or fallback_status

    rows: list[tuple[str, str]] = []
    for field, label in _SECTION2_FIELDS:
        if field == "status":
            rows.append((label, status))
            continue
        rows.append((label, _extract_cell(text, field, provenance=provenance)))

    lines = [
        "TYPE: CLOSEOUT",
        f"status: {status}",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for field, value in rows:
        lines.append(f"| {field} | {_table_cell(value)} |")
    return "\n".join(lines), status


__all__ = ["count_unclassified_fields", "project_section2_table"]
