"""Machine-write URI pooling and effects-cell honesty amend for CLOSEOUT relay."""

from __future__ import annotations

import json
import re

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    _VALID_WRAPPER_STATUSES,
    CloseoutRelayPayload,
    _as_str_list,
    _order_preserving_dedup,
    _table_cell,
    is_wrapper_manifest,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_field_section,
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
    r"(?i)(?:^|\b)(?:none|\(none|no repo writes|not reported|unauthored — not reported)"
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


__all__ = [
    "amend_effects_underclaim",
    "machine_write_uris",
]
