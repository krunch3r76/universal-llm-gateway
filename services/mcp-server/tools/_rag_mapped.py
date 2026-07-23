"""Mapped rag-context packs — exact (scope, query) → durable body lookup.

When ``rag(op=search, mapped=true)`` hits an index entry, serve the pack body
through the identical search envelope (status / pipeline / context / retrieval)
so agents apply it as retrieved evidence. Miss → caller falls through to live
``rag-context``. Provenance rides ``mcp.rag.mapped.hit`` / ``.miss`` events,
not the envelope.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from implement_admission.closeout_helpers import cortex_files_root
from mcp_events import monotonic_now, record

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_INDEX = _REPO_ROOT / "config" / "mcp" / "rag_mapped_index.yaml"
_WS_RE = re.compile(r"\s+")
_CORTEX_PREFIX = "cortex://"
LIST_MAPPED_ACTIVATION_NOTE = (
    "Activation = rag(op=search, mapped=true) only; do not fs-read pack bodies."
)


def normalize_key(scope: str, query: str) -> tuple[str, str]:
    """Normalize (scope, query) for exact index lookup."""
    return (
        _WS_RE.sub(" ", scope.strip().lower()),
        _WS_RE.sub(" ", query.strip().lower()),
    )


def _scope_as_key(scope: str | list[str] | None) -> str | None:
    """Return a single scope string suitable for keyed lookup, else None."""
    if isinstance(scope, str) and scope.strip():
        parts = [p.strip() for p in scope.split(",") if p.strip()]
        if len(parts) == 1:
            return parts[0]
        return None
    if isinstance(scope, list) and len(scope) == 1:
        only = scope[0]
        if isinstance(only, str) and only.strip():
            return only.strip()
    return None


@lru_cache(maxsize=1)
def _load_index(path_str: str) -> dict[tuple[str, str], dict[str, Any]]:
    path = Path(path_str)
    if not path.is_file():
        logger.warning("rag mapped index missing: %s", path)
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("entries") or []
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        scope = entry.get("scope")
        query = entry.get("query")
        uri = entry.get("uri")
        if not (isinstance(scope, str) and isinstance(query, str) and isinstance(uri, str)):
            logger.warning("skipping malformed mapped index entry: %r", entry)
            continue
        key = normalize_key(scope, query)
        if key in out:
            logger.warning("duplicate mapped index key %s; last wins", key)
        out[key] = entry
    return out


def _uri_to_path(uri: str) -> Path:
    if uri.startswith(_CORTEX_PREFIX):
        rel = uri[len(_CORTEX_PREFIX) :].lstrip("/")
        return cortex_files_root() / rel
    return Path(uri)


def _read_pack_body(uri: str) -> str:
    path = _uri_to_path(uri)
    body = path.read_text(encoding="utf-8")
    return body.strip() + ("\n" if body.strip() else "")


def resolve(
    query: str,
    scope: str | list[str] | None,
    *,
    index_path: Path | None = None,
) -> dict[str, Any] | None:
    """Return a search envelope for a keyed pack hit, or None to fall through.

    Envelope keys match live ``rag_search`` success:
    ``status``, ``pipeline``, ``content_length``, ``duration_s``, ``context``,
    ``retrieval``.
    """
    t0 = monotonic_now()
    path = index_path or _DEFAULT_INDEX
    scope_key = _scope_as_key(scope)
    if scope_key is None:
        record(
            "mcp.rag.mapped.miss",
            scope=str(scope) if scope is not None else "",
            query_norm=_WS_RE.sub(" ", query.strip().lower()),
            reason="scope_not_single",
        )
        return None

    key = normalize_key(scope_key, query)
    index = _load_index(str(path.resolve()))
    entry = index.get(key)
    if entry is None:
        record(
            "mcp.rag.mapped.miss",
            scope=key[0],
            query_norm=key[1],
            reason="no_index_entry",
        )
        return None

    uri = entry["uri"]
    try:
        body = _read_pack_body(uri)
    except OSError as exc:
        logger.error("mapped pack read failed uri=%s: %s", uri, exc)
        record(
            "mcp.rag.mapped.miss",
            scope=key[0],
            query_norm=key[1],
            reason="pack_read_failed",
            uri=uri,
        )
        return None

    if not body.strip():
        record(
            "mcp.rag.mapped.miss",
            scope=key[0],
            query_norm=key[1],
            reason="empty_pack",
            uri=uri,
        )
        return None

    resolved_scope = entry.get("resolved_scope") or scope_key
    chunks_found = int(entry.get("chunks_found") or 0)
    scope_source = entry.get("scope_source") or "user_override"
    duration = monotonic_now() - t0
    record(
        "mcp.rag.mapped.hit",
        scope=key[0],
        query_norm=key[1],
        uri=uri,
        content_length=len(body),
    )
    return {
        "status": "ok",
        "pipeline": "rag-context",
        "content_length": len(body),
        "duration_s": round(duration, 3),
        "context": body,
        "retrieval": {
            "resolved_scope": resolved_scope,
            "chunks_found": chunks_found,
            "scope_rejected": False,
            "scope_source": scope_source,
            "auto_classified": scope_source == "classifier",
            "scope_confidence": float(entry.get("scope_confidence") or 1.0),
        },
    }


def list_mapped_entries(*, index_path: Path | None = None) -> list[dict[str, Any]]:
    """Return URI-safe catalog rows for each indexed mapped pack.

    Each row includes ``scope``, ``query``, and an ``activate`` recipe for
    ``rag(op=search, mapped=true)``. Pack ``uri`` values are omitted. Optional
    ``label`` / ``genre`` keys appear only when explicitly present on the index
    entry — never synthesized from scope.
    """
    path = index_path or _DEFAULT_INDEX
    index = _load_index(str(path.resolve()))
    entries: list[dict[str, Any]] = []
    for entry in index.values():
        scope = entry["scope"]
        query = entry["query"]
        item: dict[str, Any] = {
            "scope": scope,
            "query": query,
            "activate": {
                "op": "search",
                "mapped": True,
                "scope": scope,
                "query": query,
            },
        }
        if "label" in entry:
            item["label"] = entry["label"]
        if "genre" in entry:
            item["genre"] = entry["genre"]
        entries.append(item)
    return entries


def clear_index_cache() -> None:
    """Drop the cached index (tests / post-edit reload)."""
    _load_index.cache_clear()
