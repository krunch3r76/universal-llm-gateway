"""Cortex URI scan for §2 CLOSEOUT relay promote and field-fill.

Reads dispatch-bound ``cortex://`` bodies in-process under ``cortex_root`` with
path containment (no ``..`` or absolute escapes). Promote-first when the body
passes ``looks_section2`` and names the dispatch; otherwise field-fill into a
synthesized envelope so judgment cells carry substance instead of bare
``unauthored`` literals.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from services.git_integration_worker.cursor_auto.relay_trust import (
    enforce_synthesized_partial,
)

if TYPE_CHECKING:
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        CloseoutRelayPayload,
    )

_CORTEX_SCHEME = "cortex://"
_MAX_RELAYED_CORTEX_CHARS = 8000
_MAX_EXECUTOR_EXCERPT_CHARS = 1500
_TRUNCATION_MARKER_TEMPLATE = "\n\n… [truncated; full body at {uri}]"

_FIELD_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "deltas_to_spec": ("deltas_to_spec", "deltas to spec"),
    "decisions_taken": ("decisions_taken", "decisions taken"),
    "next": ("next", "next steps"),
    "open forks": ("open_forks", "open forks"),
    "effects": ("effects",),
}

_FENCE_EXCEPTION_RE = re.compile(
    r"(?im)^fence_exception:\s*(?P<uri>cortex://.+?)\s*(?:—|–)\s*(?P<reason>.+)$"
)

_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_BOLD_FIELD_RE = re.compile(
    r"(?im)^\*\*(?P<heading>[^*\n]+?)\*\*\s*:?\s*\n(?P<body>(?:(?!\*\*[^*\n]+\*\*).)+)"
)


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str) and entry]


def _order_preserving_dedup(*sequences: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for sequence in sequences:
        for item in sequence:
            if item not in seen:
                seen.add(item)
                merged.append(item)
    return merged


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def normalize_cortex_uri(raw: str) -> str | None:
    """Return a canonical ``cortex://`` URI when *raw* is cortex-shaped."""
    text = raw.strip()
    if not text.startswith(_CORTEX_SCHEME):
        return None
    path = text[len(_CORTEX_SCHEME) :].strip()
    if not path:
        return None
    return f"{_CORTEX_SCHEME}{path.lstrip('/')}"


def cortex_relpath(uri: str) -> str | None:
    """Strip ``cortex://`` to a sandbox-relative path, rejecting escapes."""
    normalized = normalize_cortex_uri(uri)
    if normalized is None:
        return None
    rel = normalized[len(_CORTEX_SCHEME) :]
    if rel.startswith("/"):
        return None
    parts = Path(rel).parts
    if any(part == ".." for part in parts):
        return None
    return rel


def cortex_body_binds_dispatch(body: str, dispatch_id: str) -> bool:
    """True when *body* (including machine tail) names *dispatch_id*."""
    if not dispatch_id:
        return False
    return dispatch_id in body


def extract_cortex_uris_from_wrapper(wrapper_text: str) -> list[str]:
    """Collect order-preserving ``cortex://`` URIs from a wrapper manifest."""
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        is_wrapper_manifest,
    )

    if not is_wrapper_manifest(wrapper_text):
        return []
    try:
        data = json.loads(wrapper_text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []

    effects = _as_str_list(data.get("effects"))
    files_offgit = _as_str_list(data.get("files_offgit_produced"))
    artifact_paths: list[str] = []
    evidence_uris = data.get("evidence_uris")
    if isinstance(evidence_uris, dict):
        artifact_paths = _as_str_list(evidence_uris.get("artifact_paths"))

    pool = _order_preserving_dedup(effects, files_offgit, artifact_paths)
    uris: list[str] = []
    for entry in pool:
        normalized = normalize_cortex_uri(entry)
        if normalized is not None:
            uris.append(normalized)
    return uris


def read_cortex_text(uri: str, *, cortex_root: Path) -> str | None:
    """Read a cortex file under *cortex_root*; skip unsafe paths without raising."""
    rel = cortex_relpath(uri)
    if rel is None:
        return None
    root = cortex_root.resolve()
    full = (root / rel).resolve()
    try:
        full.relative_to(root)
    except ValueError:
        return None
    if not full.is_file():
        return None
    try:
        text = full.read_text(encoding="utf-8")
    except OSError:
        return None
    return text or None


def _normalize_heading_key(text: str) -> str:
    return re.sub(r"[_\-\s]+", "", text.casefold())


def _heading_matches_field(heading: str, field: str) -> bool:
    normalized_heading = _normalize_heading_key(heading)
    for alias in _FIELD_HEADING_ALIASES[field]:
        normalized_alias = _normalize_heading_key(alias)
        if normalized_heading == normalized_alias or normalized_heading.startswith(
            normalized_alias
        ):
            return True
    return False


def _extract_atx_section(body: str, field: str) -> str | None:
    matches = list(_ATX_HEADING_RE.finditer(body))
    for index, match in enumerate(matches):
        heading = match.group(2).strip()
        if not _heading_matches_field(heading, field):
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        section = body[start:end].strip()
        return section or None
    return None


def _extract_bold_section(body: str, field: str) -> str | None:
    for match in _BOLD_FIELD_RE.finditer(body):
        heading = match.group("heading").strip()
        if not _heading_matches_field(heading, field):
            continue
        section = match.group("body").strip()
        return section or None
    return None


def extract_field_section(body: str, field: str) -> str | None:
    """Extract judgment-cell text for *field* from cortex prose headings."""
    for extractor in (_extract_bold_section, _extract_atx_section):
        section = extractor(body, field)
        if section:
            return section
    return None


def guard_matches_write(guard_uri: str, write_uri: str) -> bool:
    """True when normalized guard relpath equals or prefixes the write relpath."""
    guard_norm = normalize_cortex_uri(guard_uri)
    write_norm = normalize_cortex_uri(write_uri)
    guard_rel = cortex_relpath(guard_norm or "")
    write_rel = cortex_relpath(write_norm or "")
    if guard_rel is None or write_rel is None:
        return False
    if guard_rel == write_rel:
        return True
    prefix = guard_rel.rstrip("/") + "/"
    return write_rel.startswith(prefix)


def _announced_fence_exceptions(body: str) -> set[str]:
    """Normalized cortex URIs explicitly exempted via ``fence_exception:`` lines."""
    announced: set[str] = set()
    for match in _FENCE_EXCEPTION_RE.finditer(body):
        normalized = normalize_cortex_uri(match.group("uri"))
        if normalized is not None:
            announced.add(normalized)
    return announced


def _write_announced_in_body(body: str, write_uri: str) -> bool:
    """True when *write_uri* is named in deltas_to_spec or fence_exception."""
    normalized = normalize_cortex_uri(write_uri)
    if normalized is None:
        return False
    rel = cortex_relpath(normalized) or ""
    if normalized in _announced_fence_exceptions(body):
        return True
    deltas = extract_field_section(body, "deltas_to_spec") or ""
    if normalized in deltas or rel in deltas:
        return True
    return False


def guarded_write_violations(
    *,
    wrapper_text: str | None,
    guard_uris: frozenset[str],
    body: str,
) -> list[str]:
    """Return guarded cortex writes lacking deltas_to_spec / fence_exception."""
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        machine_write_uris,
    )

    if not guard_uris:
        return []
    violations: list[str] = []
    seen: set[str] = set()
    for write_uri in machine_write_uris(wrapper_text):
        write_norm = normalize_cortex_uri(write_uri)
        if write_norm is None:
            continue
        matched_guard = any(
            guard_matches_write(guard, write_norm) for guard in guard_uris
        )
        if not matched_guard:
            continue
        if _write_announced_in_body(body, write_norm):
            continue
        if write_norm not in seen:
            seen.add(write_norm)
            violations.append(write_norm)
    return violations


def apply_write_fence(
    payload: CloseoutRelayPayload,
    *,
    wrapper_text: str | None,
    guard_uris: frozenset[str],
) -> CloseoutRelayPayload:
    """Fail-closed overlay when guarded corpus paths were written without announce."""
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        CloseoutRelayPayload as RelayPayload,
    )

    violations = guarded_write_violations(
        wrapper_text=wrapper_text,
        guard_uris=guard_uris,
        body=payload.body,
    )
    if not violations:
        return payload
    violation_list = ", ".join(violations)
    body = payload.body.rstrip()
    if "fence_violation:" not in body.lower():
        body = (
            f"{body}\n\nfence_violation: true\n"
            f"guarded_writes_without_announce: {violation_list}\n"
        )
    status = "blocked" if payload.status != "partial" else "partial"
    if payload.status == "complete":
        status = "blocked"
    return RelayPayload(body=body, status=status, source=payload.source)


def cap_relayed_cortex_text(
    text: str,
    uri: str,
    *,
    max_chars: int = _MAX_RELAYED_CORTEX_CHARS,
) -> str:
    """Cap relayed cortex prose and append a truncation marker naming *uri*."""
    marker = _TRUNCATION_MARKER_TEMPLATE.format(uri=uri)
    if len(text) <= max_chars:
        return text
    budget = max(0, max_chars - len(marker))
    return text[:budget] + marker


def _build_wrapper_effect_rows(wrapper_text: str) -> tuple[dict[str, str], object]:
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        is_wrapper_manifest,
    )

    if not is_wrapper_manifest(wrapper_text):
        return {}, None
    try:
        data = json.loads(wrapper_text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}, None
    if not isinstance(data, dict):
        return {}, None

    status = data.get("status", "partial")
    files_created = _as_str_list(data.get("files_created"))
    files_modified = _as_str_list(data.get("files_modified"))
    files_deleted = _as_str_list(data.get("files_deleted"))
    files_offgit_produced = _as_str_list(data.get("files_offgit_produced"))
    effects = _as_str_list(data.get("effects"))
    deviations = _as_str_list(data.get("deviations"))
    capture_status = data.get("capture_status")
    evidence_uris = data.get("evidence_uris")
    artifact_paths: list[str] = []
    if isinstance(evidence_uris, dict):
        artifact_paths = _as_str_list(evidence_uris.get("artifact_paths"))

    effects_union = _order_preserving_dedup(
        effects,
        files_created,
        files_modified,
        files_deleted,
        files_offgit_produced,
    )

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

    excerpt = cortex_body.strip()
    if len(excerpt) > _MAX_EXECUTOR_EXCERPT_CHARS:
        excerpt = excerpt[:_MAX_EXECUTOR_EXCERPT_CHARS] + "…"
    ac_verdict = (
        f"cortex substance found at {cortex_uri} but body failed looks_section2; "
        f"not a pass.<br><br>{excerpt}"
    )
    ac_verdict = cap_relayed_cortex_text(ac_verdict, cortex_uri)

    judgment_fields = ("deltas_to_spec", "decisions_taken", "next", "open forks")
    cells: dict[str, str] = {}
    for field in judgment_fields:
        extracted = extract_field_section(cortex_body, field)
        if extracted:
            cells[field] = cap_relayed_cortex_text(extracted, cortex_uri)
        else:
            cells[field] = f"see {cortex_uri}"

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
    from services.git_integration_worker.cursor_auto.closeout_relay import (
        CloseoutRelayPayload,
        looks_section2,
        status_from_section2,
        strip_machine_tail,
        wrapper_status,
    )

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
            clamped = enforce_synthesized_partial(
                raw_status,
                closeout_source="section2_synthesized",
            )
            return CloseoutRelayPayload(
                body=cap_relayed_cortex_text(prose, uri),
                status=clamped,
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
    "apply_write_fence",
    "cap_relayed_cortex_text",
    "cortex_body_binds_dispatch",
    "cortex_relpath",
    "extract_cortex_uris_from_wrapper",
    "extract_field_section",
    "field_fill_from_cortex",
    "guard_matches_write",
    "guarded_write_violations",
    "normalize_cortex_uri",
    "read_cortex_text",
    "run_cortex_scan",
]
