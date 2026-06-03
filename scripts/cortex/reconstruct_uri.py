"""URI normalization and on-disk resolution for provenance reconstruct."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reconstruct_constants import FILES_ROOT, PATH_RE, WORKSPACES_ROOT
from reconstruct_models import Candidate


def parse_uris(raw: Any) -> list[str]:
    if raw is None or raw == "" or raw == "[]":
        return []
    if isinstance(raw, list):
        return [str(u) for u in raw]
    try:
        parsed = json.loads(raw)
        return [str(u) for u in parsed] if isinstance(parsed, list) else [str(parsed)]
    except json.JSONDecodeError:
        return [str(raw)]


def normalize_uri_typos(uri: str) -> str:
    if uri.startswith("cortex:") and not uri.startswith("cortex://"):
        return "cortex://" + uri[len("cortex:") :].lstrip("/")
    return uri


def path_exists_for_uri(uri: str) -> tuple[bool, str | None]:
    """Return (exists, canonical_uri_for_attach)."""
    uri = normalize_uri_typos(uri)
    if uri.startswith(("agent-bus:", "email-bridge:")):
        return False, None
    if uri.startswith("http://") or uri.startswith("https://"):
        return True, uri
    if uri.startswith("cortex://"):
        rest = uri[len("cortex://") :]
        notes_path = FILES_ROOT / rest
        if notes_path.is_file():
            return True, f"files://{notes_path}"
        try:
            from cortex_store.rag_resolver import normalize_evidence_uri

            p = normalize_evidence_uri(uri)
            return Path(p).is_file(), uri
        except Exception:
            return False, None
    if uri.startswith("workspaces://"):
        rest = uri[len("workspaces://") :]
        p = WORKSPACES_ROOT / rest
        return p.is_file(), uri if p.is_file() else None
    if uri.startswith("files://"):
        p = Path(uri[len("files://") :])
        if not p.is_absolute():
            p = FILES_ROOT / p
        exists = p.is_file()
        return exists, uri if exists else None
    p = FILES_ROOT / uri
    if p.is_file():
        return True, uri
    return False, None


def candidates_from_evidence_text(evidence: str) -> list[str]:
    return list(dict.fromkeys(PATH_RE.findall(evidence or "")))


def locate_source(row: Candidate) -> tuple[str | None, str | None]:
    """Return (resolved_uri, near_miss_reason)."""
    tried: list[str] = []
    for uri in row.evidence_uris + candidates_from_evidence_text(row.evidence):
        tried.append(uri)
        ok, canon = path_exists_for_uri(uri)
        if ok and canon:
            return canon, None
    if row.chunk_id and row.evidence_uris:
        try:
            from cortex_store.rag_resolver import resolve_assertion_chunk

            resolve_assertion_chunk(row.id)
            return row.evidence_uris[0], None
        except Exception as exc:
            return None, f"chunk_id present but RAG resolve failed: {exc}"
    if tried:
        return None, f"uris not on disk: {tried[:3]}"
    return None, "no uris or paths extracted"
