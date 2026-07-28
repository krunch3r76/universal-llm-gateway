"""Cortex URI normalize / contain / read helpers for CLOSEOUT relay."""

from __future__ import annotations

import json
from pathlib import Path

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    _as_str_list,
    _order_preserving_dedup,
    is_wrapper_manifest,
)

_CORTEX_SCHEME = "cortex://"
_MAX_RELAYED_CORTEX_CHARS = 8000
_TRUNCATION_MARKER_TEMPLATE = "\n\n… [truncated; full body at {uri}]"


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


__all__ = [
    "_MAX_RELAYED_CORTEX_CHARS",
    "cap_relayed_cortex_text",
    "cortex_body_binds_dispatch",
    "cortex_relpath",
    "extract_cortex_uris_from_wrapper",
    "normalize_cortex_uri",
    "read_cortex_text",
]
