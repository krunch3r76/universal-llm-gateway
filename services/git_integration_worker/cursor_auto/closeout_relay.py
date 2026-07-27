"""Select operator-facing CLOSEOUT payload for cursor-auto relay.

Proxy §2 wants ``ac_verdict`` / ``deltas_to_spec`` etc. GIW's cursor-sdk bus
turn is a machine capture/manifest that shares the name "closeout". Prefer an
authored §2 body (usually the repo sidecar) when present; otherwise synthesize
§2 from the wrapper manifest so the operator never receives raw JSON alone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_auto.closeout_relay_cortex import (
    _as_str_list,
    _order_preserving_dedup,
    _table_cell,
    apply_write_fence,
    extract_field_section,
    run_cortex_scan,
)
from services.git_integration_worker.cursor_auto.relay_trust import (
    enforce_synthesized_partial,
)

_SIDECAR_REL_DIR = "tmp/reviews/closeouts"

_SECTION2_MARKERS = ("ac_verdict", "deltas_to_spec")
_TAIL_MARKERS = (
    "\n## effects_manifest",
    "\n## structured_closeout_full",
)
_STATUS_RE = re.compile(
    r"(?im)^(?:\*\*)?status(?:\*\*)?\s*[:=]\s*`?(complete|partial|blocked)`?"
)
_VALID_WRAPPER_STATUSES = frozenset({"complete", "partial", "blocked"})
_MAX_EXECUTOR_EXCERPT_CHARS = 1500
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
    r"(?i)(?:^|\b)(?:none|\(none|no repo writes|not reported|unauthored — not reported)"
)
_EFFECTS_TABLE_ROW_RE = re.compile(r"(?im)^\|\s+effects\s+\|\s+(.*?)\s+\|")
_EFFECTS_INLINE_RE = re.compile(r"(?im)^effects:\s*(.+)$")
_EFFECTS_BOLD_SAME_LINE_RE = re.compile(r"(?im)^\*\*effects:\*\*\s*(.+)$")


@dataclass(frozen=True, slots=True)
class CloseoutRelayPayload:
    """Body + status line for ``TYPE: CLOSEOUT`` relay to the operator seat."""

    body: str
    status: str
    source: (
        str  # section2_sidecar | section2_bus | section2_synthesized | wrapper | empty
    )


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


def _normalize_offgit_uri(sandbox: str | None, path: str) -> str:
    """Map fs manifest sandbox/path pairs to durable-share URIs."""
    raw = path.strip()
    lower = raw.lower()
    if lower.startswith(("cortex://", "workspaces://")):
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
    )


def _effects_cell_claims_empty(body: str) -> bool:
    """True when the §2 effects cell reads as empty/none/underclaimed."""
    table_match = _EFFECTS_TABLE_ROW_RE.search(body)
    if table_match is not None:
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
    if _EFFECTS_TABLE_ROW_RE.search(body):
        return _EFFECTS_TABLE_ROW_RE.sub(
            lambda match: f"| effects | {_table_cell(new_cell)} |",
            body,
            count=1,
        )
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


def _clamp_non_complete_status(current: str) -> str:
    if current == "blocked":
        return "blocked"
    return "partial"


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
    amended_status = _clamp_non_complete_status(status)
    if status == "complete":
        amended_status = "partial"
    elif status not in _VALID_WRAPPER_STATUSES:
        amended_status = _clamp_non_complete_status(status)
    return CloseoutRelayPayload(
        body=amended_body,
        status=amended_status,
        source=source,
    )


def _finalize(
    payload: CloseoutRelayPayload,
    *,
    wrapper_text: str | None,
    guard_uris: frozenset[str] | None = None,
) -> CloseoutRelayPayload:
    """Run honesty amend and optional confer write-fence on every selector exit."""
    amended = amend_effects_underclaim(
        payload.body,
        wrapper_text=wrapper_text,
        status=payload.status,
        source=payload.source,
    )
    if not guard_uris:
        return amended
    return apply_write_fence(
        amended,
        wrapper_text=wrapper_text,
        guard_uris=guard_uris,
    )


def synthesize_section2(
    *,
    wrapper_text: str | None,
    sidecar_text: str | None,
    dispatch_id: str,
) -> str | None:
    """Build operator-facing §2 markdown from a machine wrapper manifest."""
    del dispatch_id  # reserved for future trace headers; selection uses sdk metadata
    if not wrapper_text or not is_wrapper_manifest(wrapper_text):
        return None
    try:
        data = json.loads(wrapper_text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

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

    effects_union = machine_write_uris(wrapper_text)
    if not effects_union:
        effects_union = _order_preserving_dedup(
            effects,
            files_created,
            files_modified,
            files_deleted,
            files_offgit_produced,
        )

    ac_verdict = (
        "unauthored — executor emitted no §2 body; machine-derived envelope below. "
        "Not a pass."
    )
    if sidecar_text:
        excerpt = strip_machine_tail(sidecar_text).strip()
        if excerpt:
            if len(excerpt) > _MAX_EXECUTOR_EXCERPT_CHARS:
                excerpt = excerpt[:_MAX_EXECUTOR_EXCERPT_CHARS] + "…"
            ac_verdict = f"{ac_verdict}<br><br>{excerpt}"

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

    rows = (
        ("status", str(status)),
        ("ac_verdict", ac_verdict),
        ("deltas_to_spec", "unauthored — not reported by executor"),
        ("decisions_taken", "unauthored — not reported by executor"),
        ("effects", effects_cell),
        ("evidence", evidence_cell),
        ("next", "unauthored — operator must derive from effects above"),
        ("open forks", "unknown — executor emitted no §2"),
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


def ledger_status_to_closeout(terminal_status: str) -> str:
    """Map ledger terminal status → operator CLOSEOUT status line."""
    if terminal_status == "completed":
        return "complete"
    if terminal_status == "failed":
        return "blocked"
    return "partial"


def read_repo_closeout_sidecar(
    dispatch_id: str,
    *,
    source_repo: Path | None = None,
) -> str | None:
    """Read ``tmp/reviews/closeouts/{dispatch_id}.md`` when present."""
    root = source_repo if source_repo is not None else load_config().source_repo
    path = root / _SIDECAR_REL_DIR / f"{dispatch_id}.md"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text or None


def select_closeout_relay_payload(
    *,
    sdk_body: str | None,
    sidecar_text: str | None,
    ledger_status: str,
    dispatch_id: str = "",
    cortex_root: Path | None = None,
    guard_uris: frozenset[str] | None = None,
) -> CloseoutRelayPayload:
    """Prefer authored §2; synthesize from wrapper manifest when absent.

    Selection order:
    1. Repo sidecar with §2 markers (machine tail stripped)
    2. Bus body when it itself looks like §2 (rare)
    3. Cortex promote or field-fill from wrapper ``cortex://`` URIs
    4. Synthesized §2 from wrapper manifest JSON
    5. Non-manifest bus body (plain prose)
    6. Empty placeholder

    All exits pass through ``_finalize`` for machine-over-prose honesty amend
    and optional confer write-fence when *guard_uris* is supplied.

    ``cortex_root`` defaults to ``cortex_files_root()`` when omitted. URIs with
    absolute paths, ``..`` segments, or targets outside ``cortex_root`` are
    skipped without raising. Cortex promote requires §2 markers and a dispatch
    bind; relayed status is clamped via ``enforce_synthesized_partial`` while
    the synthesized trust gate remains disabled.
    """
    fallback_status = ledger_status_to_closeout(ledger_status)
    wrapper_text = sdk_body

    if sidecar_text:
        prose = strip_machine_tail(sidecar_text)
        if looks_section2(prose):
            return _finalize(
                CloseoutRelayPayload(
                    body=prose,
                    status=status_from_section2(prose) or fallback_status,
                    source="section2_sidecar",
                ),
                wrapper_text=wrapper_text,
                guard_uris=guard_uris,
            )

    if sdk_body and looks_section2(sdk_body) and not is_wrapper_manifest(sdk_body):
        prose = strip_machine_tail(sdk_body)
        return _finalize(
            CloseoutRelayPayload(
                body=prose,
                status=status_from_section2(prose) or fallback_status,
                source="section2_bus",
            ),
            wrapper_text=wrapper_text,
            guard_uris=guard_uris,
        )

    root = cortex_root if cortex_root is not None else cortex_files_root()
    cortex_payload = run_cortex_scan(
        wrapper_text=sdk_body or "",
        dispatch_id=dispatch_id,
        cortex_root=root,
        fallback_status=fallback_status,
    )
    if cortex_payload is not None:
        return _finalize(
            cortex_payload,
            wrapper_text=wrapper_text,
            guard_uris=guard_uris,
        )

    synthesized = synthesize_section2(
        wrapper_text=sdk_body,
        sidecar_text=sidecar_text,
        dispatch_id=dispatch_id,
    )
    if synthesized is not None:
        source = "section2_synthesized"
        raw_status = wrapper_status(sdk_body or "") or fallback_status
        return _finalize(
            CloseoutRelayPayload(
                body=synthesized,
                status=enforce_synthesized_partial(
                    raw_status,
                    closeout_source=source,
                ),
                source=source,
            ),
            wrapper_text=wrapper_text,
            guard_uris=guard_uris,
        )

    if sdk_body and sdk_body.strip():
        return _finalize(
            CloseoutRelayPayload(
                body=sdk_body.strip(),
                status=fallback_status,
                source="wrapper",
            ),
            wrapper_text=wrapper_text,
            guard_uris=guard_uris,
        )

    return _finalize(
        CloseoutRelayPayload(
            body="(no cursor-sdk closeout body captured)",
            status=fallback_status,
            source="empty",
        ),
        wrapper_text=wrapper_text,
        guard_uris=guard_uris,
    )
