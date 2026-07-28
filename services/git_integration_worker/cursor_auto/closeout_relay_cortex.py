"""Cortex URI scan for §2 CLOSEOUT relay promote and field-fill.

Reads dispatch-bound ``cortex://`` bodies in-process under ``cortex_root`` with
path containment (no ``..`` or absolute escapes). Promote-first when the body
passes ``looks_section2`` and names the dispatch; otherwise field-fill into a
synthesized envelope so judgment cells carry substance instead of bare
``unauthored`` literals.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
    _as_str_list,
    _order_preserving_dedup,
    _table_cell,
    build_ac_verdict_cell,
    fill_judgment_cell,
    is_wrapper_manifest,
    looks_section2,
    status_from_section2,
    strip_machine_tail,
    wrapper_status,
)
from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
    machine_write_uris,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fence import (
    apply_write_fence,
    guard_matches_write,
    guarded_write_violations,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_uri import (
    _MAX_RELAYED_CORTEX_CHARS,
    cap_relayed_cortex_text,
    cortex_body_binds_dispatch,
    cortex_relpath,
    extract_cortex_uris_from_wrapper,
    normalize_cortex_uri,
    read_cortex_text,
)
from services.git_integration_worker.cursor_auto.relay_trust import (
    enforce_synthesized_partial,
)

_MAX_EXECUTOR_EXCERPT_CHARS = 1500


def _build_wrapper_effect_rows(wrapper_text: str) -> tuple[dict[str, str], object]:
    if not is_wrapper_manifest(wrapper_text):
        return {}, None
    try:
        data = json.loads(wrapper_text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}, None
    if not isinstance(data, dict):
        return {}, None

    status = data.get("status", "partial")
    deviations = _as_str_list(data.get("deviations"))
    capture_status = data.get("capture_status")
    evidence_uris = data.get("evidence_uris")
    artifact_paths: list[str] = []
    if isinstance(evidence_uris, dict):
        artifact_paths = _as_str_list(evidence_uris.get("artifact_paths"))

    effects_union = machine_write_uris(wrapper_text)

    if effects_union:
        effects_cell = "<br>".join(f"- {item}" for item in effects_union)
    else:
        effects_cell = (
            f"none captured — capture_status={capture_status}; "
            'per §4.7 a schema-only read of "none" is not authority'
        )

    evidence_parts: list[str] = []
    if artifact_paths:
        evidence_parts.append("artifact_paths: " + ", ".join(artifact_paths))
    if deviations:
        evidence_parts.append("deviations: " + "; ".join(deviations))
    if capture_status is not None:
        evidence_parts.append(f"capture_status={capture_status}")
    evidence_cell = "; ".join(evidence_parts) if evidence_parts else "none"

    return {"effects": effects_cell, "evidence": evidence_cell}, status


def field_fill_from_cortex(
    *,
    wrapper_text: str,
    cortex_uri: str,
    cortex_body: str,
    dispatch_id: str,
) -> str:
    """Render a synthesized §2 table using cortex substance for judgment cells."""
    del dispatch_id  # dispatch binding is enforced before field-fill is selected
    effect_rows, wrapper_status_value = _build_wrapper_effect_rows(wrapper_text)
    status = str(wrapper_status_value or "partial")

    ac_verdict = build_ac_verdict_cell(
        cortex_body,
        provenance=cortex_uri,
        cap=cap_relayed_cortex_text,
        max_excerpt_chars=_MAX_EXECUTOR_EXCERPT_CHARS,
    )

    judgment_fields = ("deltas_to_spec", "decisions_taken", "next", "open forks")
    cells: dict[str, str] = {}
    for field in judgment_fields:
        cells[field] = fill_judgment_cell(
            cortex_body,
            field,
            provenance=cortex_uri,
            cap=cap_relayed_cortex_text,
        )

    rows = (
        ("status", status),
        ("ac_verdict", ac_verdict),
        ("deltas_to_spec", cells["deltas_to_spec"]),
        ("decisions_taken", cells["decisions_taken"]),
        ("effects", effect_rows.get("effects", "none")),
        ("evidence", effect_rows.get("evidence", "none")),
        ("next", cells["next"]),
        ("open forks", cells["open forks"]),
    )
    lines = [
        "TYPE: CLOSEOUT",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for field, value in rows:
        lines.append(f"| {field} | {_table_cell(value)} |")
    return "\n".join(lines)


def run_cortex_scan(
    *,
    wrapper_text: str,
    dispatch_id: str,
    cortex_root: Path,
    fallback_status: str,
) -> CloseoutRelayPayload | None:
    """Single-pass cortex URI scan: promote, field-fill, or fall through."""
    uris = extract_cortex_uris_from_wrapper(wrapper_text)
    if not uris:
        return None

    readable: list[tuple[str, str]] = []
    for uri in uris:
        raw = read_cortex_text(uri, cortex_root=cortex_root)
        if raw is None:
            continue
        prose = strip_machine_tail(raw)
        if prose.strip():
            readable.append((uri, prose))

    for uri, prose in readable:
        if looks_section2(prose) and cortex_body_binds_dispatch(prose, dispatch_id):
            raw_status = status_from_section2(prose) or fallback_status
            return CloseoutRelayPayload(
                body=cap_relayed_cortex_text(prose, uri),
                status=raw_status,
                source="section2_sidecar",
            )

    if readable:
        uri, prose = readable[0]
        filled = field_fill_from_cortex(
            wrapper_text=wrapper_text,
            cortex_uri=uri,
            cortex_body=prose,
            dispatch_id=dispatch_id,
        )
        raw_status = wrapper_status(wrapper_text) or fallback_status
        return CloseoutRelayPayload(
            body=filled,
            status=enforce_synthesized_partial(
                raw_status,
                closeout_source="section2_synthesized",
            ),
            source="section2_synthesized",
        )

    return None


__all__ = [
    "_MAX_RELAYED_CORTEX_CHARS",
    "_as_str_list",
    "_order_preserving_dedup",
    "_table_cell",
    "apply_write_fence",
    "cap_relayed_cortex_text",
    "cortex_body_binds_dispatch",
    "cortex_relpath",
    "extract_cortex_uris_from_wrapper",
    "field_fill_from_cortex",
    "guard_matches_write",
    "guarded_write_violations",
    "normalize_cortex_uri",
    "read_cortex_text",
    "run_cortex_scan",
]
