"""Machine-write URI pooling and effects-cell honesty amend for CLOSEOUT relay."""

from __future__ import annotations

import json
import re

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    _DEVIATION_EFFECTS_ENRICHED,
    _VALID_WRAPPER_STATUSES,
    CloseoutRelayPayload,
    _as_str_list,
    _order_preserving_dedup,
    _table_cell,
    is_wrapper_manifest,
    merge_relay_notes,
    relay_parse_failure_detected,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_field_section,
    fenced_spans,
    in_fenced_span,
)
from services.git_integration_worker.cursor_auto.closeout_relay_project import (
    count_unclassified_fields,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    sidecar_workspaces_ref,
)
from services.git_integration_worker.cursor_sdk_manifest.cortex_uri_impersonation import (
    is_cortex_host_path_impersonation,
)

_FS_WRITE_OPS = frozenset(
    {
        "write",
        "append",
        "prepend",
        "insert_at_line",
        "replace",
        "md_replace",
        "md_append",
        "md_insert",
        "write_binary",
        "append_binary",
        "copy",
    }
)
_OOB_DEVIATION_PREFIX = "capture:oob_cortex_write_unobserved:"
_EFFECTS_EMPTY_RE = re.compile(
    r"(?i)(?:^|\b)(?:none|\(none|no repo writes|not reported|unauthored — not reported|none captured)"
)
_EFFECTS_TABLE_ROW_RE = re.compile(r"(?im)^\|\s+effects\s+\|\s+(.*?)\s+\|")
_EFFECTS_INLINE_RE = re.compile(r"(?im)^effects:\s*(.+)$")
_EFFECTS_BOLD_SAME_LINE_RE = re.compile(r"(?im)^\*\*effects:\*\*\s*(.+)$")


def _normalize_offgit_uri(sandbox: str | None, path: str) -> str:
    """Map fs manifest sandbox/path pairs to durable-share URIs."""
    raw = path.strip()
    lower = raw.lower()
    if lower.startswith(("cortex://", "workspaces://")):
        return raw
    if is_cortex_host_path_impersonation(raw):
        return raw
    if lower.startswith("cortex:"):
        return f"cortex://{raw.split(':', 1)[1].lstrip('/')}"
    sandbox_key = (sandbox or "").strip().lower()
    if sandbox_key == "cortex":
        return f"cortex://{raw.lstrip('/')}"
    if sandbox_key == "workspaces":
        return f"workspaces://{raw.lstrip('/')}"
    if ":" in raw and not lower.startswith(("cortex", "workspaces")):
        prefix, _, rest = raw.partition(":")
        if prefix.lower() in {"cortex", "workspaces"} and rest:
            return f"{prefix.lower()}://{rest.lstrip('/')}"
    return raw


def _manifest_fs_write_uris(data: dict[str, object]) -> list[str]:
    """Collect write-op URIs from ``effects_manifest.surfaces.fs`` entries."""
    manifest = data.get("effects_manifest")
    if not isinstance(manifest, dict):
        return []
    surfaces = manifest.get("surfaces")
    if not isinstance(surfaces, dict):
        return []
    fs_section = surfaces.get("fs")
    if not isinstance(fs_section, dict):
        return []
    entries = fs_section.get("entries")
    if not isinstance(entries, list):
        return []
    uris: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        detail = entry.get("detail")
        op = detail.get("op") if isinstance(detail, dict) else None
        if not isinstance(op, str) or op not in _FS_WRITE_OPS:
            continue
        path = None
        if isinstance(detail, dict):
            raw_path = detail.get("path")
            if isinstance(raw_path, str) and raw_path.strip():
                path = raw_path.strip()
        if path is None:
            for key in ("target", "identity"):
                raw = entry.get(key)
                if isinstance(raw, str) and raw.strip():
                    path = raw.strip()
                    break
        if path is None:
            continue
        sandbox = detail.get("sandbox") if isinstance(detail, dict) else None
        sandbox_str = sandbox.strip() if isinstance(sandbox, str) else None
        uris.append(_normalize_offgit_uri(sandbox_str, path))
    return uris


def _oob_deviation_uris(deviations: list[str]) -> list[str]:
    """Parse ``capture:oob_cortex_write_unobserved:<uri>`` deviation tokens."""
    uris: list[str] = []
    for entry in deviations:
        if not entry.startswith(_OOB_DEVIATION_PREFIX):
            continue
        uri = entry[len(_OOB_DEVIATION_PREFIX) :].strip()
        if uri:
            uris.append(uri)
    return uris


def _evidence_uri_pointers(data: dict[str, object]) -> list[str]:
    """Collect artifact paths and bus-thread refs from wrapper evidence_uris."""
    evidence_uris = data.get("evidence_uris")
    if not isinstance(evidence_uris, dict):
        return []
    pointers: list[str] = []
    pointers.extend(_as_str_list(evidence_uris.get("artifact_paths")))
    for thread in _as_str_list(evidence_uris.get("bus_threads")):
        thread_id = thread.strip()
        if thread_id:
            pointers.append(f"agent-bus:{thread_id}")
    return pointers


def machine_write_uris(wrapper_text: str | None) -> list[str]:
    """Order-preserving union of machine-captured write URIs from a wrapper manifest."""
    if not wrapper_text or not is_wrapper_manifest(wrapper_text):
        return []
    try:
        data = json.loads(wrapper_text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return _order_preserving_dedup(
        _as_str_list(data.get("effects")),
        _as_str_list(data.get("files_created")),
        _as_str_list(data.get("files_modified")),
        _as_str_list(data.get("files_deleted")),
        _as_str_list(data.get("files_offgit_produced")),
        _manifest_fs_write_uris(data),
        _oob_deviation_uris(_as_str_list(data.get("deviations"))),
        _evidence_uri_pointers(data),
    )


def _effects_cell_claims_empty(body: str) -> bool:
    """True when the §2 effects cell reads as empty/none/underclaimed."""
    spans = fenced_spans(body)
    for table_match in _EFFECTS_TABLE_ROW_RE.finditer(body):
        if in_fenced_span(spans, table_match.start()):
            continue
        cell = table_match.group(1).strip()
        if not cell or cell.lower() in {"none", "n/a"}:
            return True
        return _EFFECTS_EMPTY_RE.search(cell) is not None
    for pattern in (_EFFECTS_INLINE_RE, _EFFECTS_BOLD_SAME_LINE_RE):
        inline_match = pattern.search(body)
        if inline_match is not None:
            return _EFFECTS_EMPTY_RE.search(inline_match.group(1)) is not None
    effects_section = extract_field_section(body, "effects")
    if effects_section is not None:
        return _EFFECTS_EMPTY_RE.search(effects_section) is not None
    return False


def _format_effects_cell(uris: list[str]) -> str:
    if not uris:
        return "none"
    return "<br>".join(f"- {item}" for item in uris)


def _rewrite_effects_cell(body: str, uris: list[str]) -> str:
    """Replace an underclaiming effects cell with the machine write union."""
    new_cell = _format_effects_cell(uris)
    spans = fenced_spans(body)
    table_match = next(
        (
            match
            for match in _EFFECTS_TABLE_ROW_RE.finditer(body)
            if not in_fenced_span(spans, match.start())
        ),
        None,
    )
    if table_match is not None:
        replacement = f"| effects | {_table_cell(new_cell)} |"
        return body[: table_match.start()] + replacement + body[table_match.end() :]
    if _EFFECTS_INLINE_RE.search(body):
        return _EFFECTS_INLINE_RE.sub(f"effects: {new_cell}", body, count=1)
    if _EFFECTS_BOLD_SAME_LINE_RE.search(body):
        return _EFFECTS_BOLD_SAME_LINE_RE.sub(
            f"**effects:** {new_cell}",
            body,
            count=1,
        )
    if extract_field_section(body, "effects") is not None:
        return re.sub(
            r"(?im)^(\*\*effects\*\*\s*:?\s*\n)(?:(?!\*\*[^*\n]+\*\*).)+",
            rf"\1{new_cell}\n",
            body,
            count=1,
        )
    return body + f"\n\n**effects:**\n{new_cell}\n"


_OVERCLAIM_PARSE_FAILED = "overclaim:parse_failed_field"
_OVERCLAIM_UNCLASSIFIED = "overclaim:unclassified_field"
_OVERCLAIM_FALSE_ABSENCE = "overclaim:false_absence_unread_provenance"
_DEVIATIONS_LINE_RE = re.compile(r"(?im)^deviations:\s*(.*)$")
_TABLE_CELL_ROW_RE = re.compile(
    r"(?im)^\|\s*(?P<field>[^|]+?)\s*\|\s*(?P<value>.*?)\s*\|\s*$"
)
_JUDGMENT_FIELDS: tuple[str, ...] = (
    "ac_verdict",
    "deltas_to_spec",
    "decisions_taken",
    "next",
    "open forks",
)
_FALSE_ABSENCE_MARKERS: tuple[str, ...] = (
    "none — field not authored in §2 sidecar",
    "unknown — executor emitted no §2",
    "unauthored — operator must derive from effects above",
    "unauthored — not reported by executor",
    "none — see machine envelope below",
    "none captured — see machine envelope below",
)


def _extract_table_cell(body: str, field: str) -> str | None:
    spans = fenced_spans(body)
    for match in _TABLE_CELL_ROW_RE.finditer(body):
        if in_fenced_span(spans, match.start()):
            continue
        if match.group("field").strip().casefold() == field.casefold():
            return match.group("value").strip()
    return None


def _replace_table_cell(body: str, field: str, new_value: str) -> str:
    spans = fenced_spans(body)

    def _rewrite(match: re.Match[str]) -> str:
        if in_fenced_span(spans, match.start()):
            return match.group(0)
        if match.group("field").strip().casefold() != field.casefold():
            return match.group(0)
        return f"| {field} | {_table_cell(new_value)} |"

    return _TABLE_CELL_ROW_RE.sub(_rewrite, body, count=0)


def _rewrite_relay_status(body: str, new_status: str) -> str:
    updated = re.sub(
        r"(?im)^status:\s*\S+",
        f"status: {new_status}",
        body,
        count=1,
    )
    if _extract_table_cell(updated, "status") is not None:
        updated = _replace_table_cell(updated, "status", new_status)
    return updated


def _append_deviation_tokens(body: str, tokens: list[str]) -> str:
    if not tokens:
        return body
    existing: list[str] = []
    match = _DEVIATIONS_LINE_RE.search(body)
    if match is not None:
        existing = [part.strip() for part in match.group(1).split(";") if part.strip()]
    merged: list[str] = []
    seen: set[str] = set()
    for token in [*existing, *tokens]:
        if token not in seen:
            seen.add(token)
            merged.append(token)
    line = "deviations: " + "; ".join(merged)
    if match is not None:
        return _DEVIATIONS_LINE_RE.sub(line, body, count=1)
    status_match = re.search(r"(?im)^status:\s*\S+\s*$", body)
    if status_match is not None:
        insert_at = status_match.end()
        return f"{body[:insert_at]}\n{line}{body[insert_at:]}"
    return f"{body.rstrip()}\n{line}\n"


def _cell_claims_false_absence(cell: str) -> bool:
    if "unclassified" in cell.casefold():
        return False
    if cell.casefold().startswith("relay could not locate"):
        return False
    return any(marker in cell for marker in _FALSE_ABSENCE_MARKERS)


def _cell_claims_unclassified_or_hard_unauthored(cell: str) -> bool:
    lowered = cell.casefold().lstrip()
    if lowered.startswith("parse_failed —") or lowered.startswith("parse_failed—"):
        return True
    if "unclassified" in lowered and "relay could not parse" in lowered:
        return True
    return (
        "unauthored — not reported by executor" in cell
        or "unknown — executor emitted no §2" in cell
    )


def _cell_reports_parse_failed(cell: str) -> bool:
    lowered = cell.casefold().lstrip()
    return lowered.startswith("parse_failed —") or lowered.startswith("parse_failed—")


def _rewrite_parse_failed_cells(body: str, *, sidecar_uri: str) -> str:
    """Replace stale unclassified cells with parse_failed + authoritative sidecar URI."""
    amended = body
    for field in _JUDGMENT_FIELDS:
        cell = _extract_table_cell(amended, field)
        if not cell:
            continue
        lowered = cell.casefold()
        if "parse_failed" in lowered:
            continue
        if "unclassified" in lowered and "relay could not parse" in lowered:
            amended = _replace_table_cell(
                amended,
                field,
                f"parse_failed — authoritative sidecar: {sidecar_uri}",
            )
    return amended


def _judgment_cells_overclaim(body: str) -> bool:
    if count_unclassified_fields(body) > 0:
        return True
    for field in _JUDGMENT_FIELDS:
        cell = _extract_table_cell(body, field)
        if cell and _cell_claims_unclassified_or_hard_unauthored(cell):
            return True
    return False


_READ_FAILED_CELL_PREFIX = "read_failed — sidecar unavailable:"


def amend_completion_overclaim(
    body: str,
    *,
    wrapper_text: str | None,
    status: str,
    source: str,
    dispatch_id: str = "",
    sidecar_read_succeeded: bool = False,
    sidecar_read_failed_uri: str | None = None,
) -> CloseoutRelayPayload:
    """Annotate relay overclaim signals without mutating executor-authored status."""
    del wrapper_text  # read state is plumbed explicitly; URIs alone are not read proof
    amended_body = body
    relay_note_parts: list[str] = []

    sidecar_uri = (
        sidecar_workspaces_ref(dispatch_id)
        if dispatch_id
        else (sidecar_read_failed_uri or "")
    )
    if sidecar_uri and "unclassified" in amended_body.casefold():
        amended_body = _rewrite_parse_failed_cells(
            amended_body, sidecar_uri=sidecar_uri
        )

    if sidecar_read_failed_uri and not sidecar_read_succeeded:
        false_absence_hits = False
        for field in _JUDGMENT_FIELDS:
            cell = _extract_table_cell(amended_body, field)
            if not cell or not _cell_claims_false_absence(cell):
                continue
            amended_body = _replace_table_cell(
                amended_body,
                field,
                f"{_READ_FAILED_CELL_PREFIX} {sidecar_read_failed_uri}",
            )
            false_absence_hits = True
        if false_absence_hits:
            relay_note_parts.append(_OVERCLAIM_FALSE_ABSENCE)

    if _judgment_cells_overclaim(amended_body):
        if relay_parse_failure_detected(amended_body):
            relay_note_parts.append("relay:parse_failure_in_cells")
        if _OVERCLAIM_PARSE_FAILED not in relay_note_parts and any(
            _cell_reports_parse_failed(_extract_table_cell(amended_body, f) or "")
            for f in _JUDGMENT_FIELDS
        ):
            relay_note_parts.append(_OVERCLAIM_PARSE_FAILED)
        if _OVERCLAIM_UNCLASSIFIED not in relay_note_parts:
            relay_note_parts.append(_OVERCLAIM_UNCLASSIFIED)

    relay_note = merge_relay_notes(
        "; ".join(relay_note_parts) if relay_note_parts else None
    )

    return CloseoutRelayPayload(
        body=amended_body,
        status=status,
        source=source,
        relay_note=relay_note,
    )


def amend_effects_underclaim(
    body: str,
    *,
    wrapper_text: str | None,
    status: str,
    source: str,
) -> CloseoutRelayPayload:
    """Amend an underclaiming effects cell when machine writes are nonempty."""
    if not wrapper_text or not is_wrapper_manifest(wrapper_text):
        return CloseoutRelayPayload(body=body, status=status, source=source)
    machine_uris = machine_write_uris(wrapper_text)
    if not machine_uris or not _effects_cell_claims_empty(body):
        return CloseoutRelayPayload(body=body, status=status, source=source)
    amended_body = _rewrite_effects_cell(body, machine_uris)
    relay_note = None
    if source == "section2_sidecar" and status == "complete":
        amended_body = _append_deviation_tokens(
            amended_body, [_DEVIATION_EFFECTS_ENRICHED]
        )
        relay_note = _DEVIATION_EFFECTS_ENRICHED
    elif status not in _VALID_WRAPPER_STATUSES:
        relay_note = f"relay:nonstandard_authored_status:{status}"
    return CloseoutRelayPayload(
        body=amended_body,
        status=status,
        source=source,
        relay_note=relay_note,
    )


__all__ = [
    "amend_completion_overclaim",
    "amend_effects_underclaim",
    "machine_write_uris",
]
