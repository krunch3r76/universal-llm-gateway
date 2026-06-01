"""Resolver — assertion.chunk_id → RAG chunk text.

Implements Phase E of plan:cortex-v3-completion: resolves an assertion's
(chunk_id, evidence_uris[0]) pair to RAG chunk text via POST /chunks_by_index.

assertions.chunk_id semantics (post-phase-E): RAG-deterministic ID of the
form ``{content_hash_prefix}-{i}``. The column is kept but redefined from a
cortex-internal chunks-table FK to this RAG-native string ID.

URI normalization covers cortex://, workspaces://, files://, and https://
schemes. All are mapped to the absolute filesystem path or RAG-indexed URL
that RAG stores as ``source``.

Verify-on-fetch: the resolver asserts that the returned chunk's chunk_id
matches the assertion's stored chunk_id. If RAG re-chunked and assigned
different IDs to the same content, this raises ChunkIdMismatch — enforcing
the immutability contract documented in services/rag/README.md
§ Chunker immutability.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_STARGATE_URL, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

_RAG_TIMEOUT = 15.0

# Sandbox root paths — mirrors mcp-tool-awareness routing table.
_WORKSPACES_ROOT = os.environ.get("WORKSPACES_ROOT", "/mnt/torus/projects")
_FILES_ROOT = os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files")


class ChunkIdMismatchError(RuntimeError):
    """Raised when the resolved RAG chunk_id differs from the assertion's chunk_id.

    ∀ mismatch: RAG chunker re-indexed with changed parameters, making the
    stored chunk_id a dangling pointer. Requires a migration pass to re-derive
    chunk_ids from RAG content-match (see phase-E step 2 procedure).
    """

    def __init__(self, assertion_id: int, stored_id: str, returned_id: str) -> None:
        super().__init__(
            f"Assertion {assertion_id}: stored chunk_id={stored_id!r} "
            f"but RAG returned chunk_id={returned_id!r}. "
            "RAG chunker immutability violated — re-derive chunk_ids "
            "(see services/rag/README.md § Chunker immutability)."
        )
        self.assertion_id = assertion_id
        self.stored_id = stored_id
        self.returned_id = returned_id


def _cortex_uri_to_path(uri: str) -> str:
    """Resolve a cortex:// URI to absolute filesystem path via entity source_uri.

    ∀ cortex:// URI: the authoritative path lives on the entity's source_uri
    attribute, not on the URI's type/slug segments. The URI type slug
    (e.g. 'agent_skill') does not match the filesystem folder name
    (e.g. 'agent-skills'), so literal substitution is wrong.

    Requires: entity exists in cortex DB and has a non-null source_uri.
    """
    from urllib.parse import urlparse

    from .db import cortex_conn, query

    parsed = urlparse(uri)
    entity_type = parsed.netloc
    slug = parsed.path.lstrip("/")
    if not entity_type or not slug:
        raise ValueError(f"Invalid cortex:// URI: {uri!r}")
    entity_id = f"{entity_type}:{slug}"

    with cortex_conn() as conn:
        rows = query(conn, "SELECT source_uri FROM entities WHERE id = ?", (entity_id,))

    if not rows:
        raise ValueError(f"cortex:// entity not found: {entity_id!r} (URI: {uri!r})")
    source_uri = rows[0].get("source_uri")
    if not source_uri:
        raise ValueError(
            f"Entity {entity_id!r} has no source_uri — cannot resolve {uri!r} to filesystem path"
        )
    return _source_uri_to_absolute_path(source_uri)


def _canonicalize_filesystem_path(path: str) -> str:
    """Resolve to match RAG's ``Path(...).expanduser().resolve()`` contract."""
    return str(Path(path).expanduser().resolve())


def _source_uri_to_absolute_path(source_uri: str) -> str:
    """Convert an entity's source_uri attribute to an absolute filesystem path.

    source_uri may be a plain relative path (e.g. 'agent-skills/foo.md') or
    itself a URI (e.g. 'files://notes/system/transcripts/foo.md').

    ∀ plain relative path: {_FILES_ROOT}/{source_uri}.
    ∀ files:// with relative body: {_FILES_ROOT}/{body}.
    ∀ files:// with absolute body or workspaces:// → already absolute: return as-is.

    Filesystem paths are canonicalized with ``Path.resolve()`` so the
    cortex-api source-paths producer matches RAG indexing (exact set membership).
    """
    if "://" not in source_uri:
        return _canonicalize_filesystem_path(f"{_FILES_ROOT}/{source_uri}")
    result = normalize_evidence_uri(source_uri)
    if result.startswith("https://") or result.startswith("http://"):
        return result
    if result.startswith("/"):
        return _canonicalize_filesystem_path(result)
    return _canonicalize_filesystem_path(f"{_FILES_ROOT}/{result}")


def normalize_evidence_uri(uri: str) -> str:
    """Map a cortex evidence_uri to the source path as stored in RAG.

    Normalization rules (scheme → RAG ``source`` value):
      cortex://TYPE/SLUG       → {_FILES_ROOT}/{entity.source_uri} (via DB lookup)
      workspaces://REPO/PATH   → {_WORKSPACES_ROOT}/REPO/PATH
      files://PATH             → PATH (absolute filesystem path)
      https://... / http://... → unchanged (RAG-indexed URL)
      /absolute/path           → unchanged

    Raises ValueError for unrecognized schemes, missing entities, or entities
    without source_uri.
    """
    if uri.startswith("cortex://"):
        return _cortex_uri_to_path(uri)
    if uri.startswith("workspaces://"):
        rest = uri[len("workspaces://") :]
        return f"{_WORKSPACES_ROOT}/{rest}"
    if uri.startswith("files://"):
        return uri[len("files://") :]
    if uri.startswith("https://") or uri.startswith("http://"):
        return uri
    if uri.startswith("/"):
        return uri
    raise ValueError(
        f"Unrecognized evidence_uri scheme: {uri!r}. "
        "Supported: cortex://, workspaces://, files://, https://, absolute path."
    )


def _parse_chunk_index(chunk_id: str) -> int:
    """Extract the chunk index from a RAG-deterministic chunk ID.

    Format: ``{content_hash_prefix}-{i}`` where i is a non-negative integer.
    Raises ValueError for malformed IDs.
    """
    parts = chunk_id.rsplit("-", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        raise ValueError(
            f"chunk_id {chunk_id!r} does not match expected format "
            "'{content_hash_prefix}-{i}' (RAG-deterministic ID)"
        )
    return int(parts[1])


def resolve_assertion_chunk(assertion_id: int) -> dict[str, Any]:
    """Resolve an assertion's chunk_id → RAG chunk text.

    Fetches the assertion from DB, normalizes evidence_uris[0] → RAG source
    path, calls POST /chunks_by_index, verifies chunk_id round-trip.

    Returns: ChunkByIndexItem dict with keys chunk_id, source, chunk_index,
    text, metadata.

    Raises:
      ValueError: assertion not found, no chunk_id, no evidence_uris, bad URI
                  schema, or RAG returned no chunks for the request.
      ChunkIdMismatch: RAG returned a chunk whose ID differs from stored.
      httpx.ConnectError / httpx.HTTPStatusError: Stargate/RAG unreachable.
    """
    from .db import cortex_conn, query

    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT id, chunk_id, evidence_uris FROM assertions WHERE id = ?",
            (assertion_id,),
        )

    if not rows:
        raise ValueError(f"Assertion not found: {assertion_id}")

    row = rows[0]
    stored_chunk_id = row.get("chunk_id")
    if not stored_chunk_id:
        raise ValueError(
            f"Assertion {assertion_id} has no chunk_id — not a chunk-linked assertion"
        )
    stored_chunk_id = str(stored_chunk_id)

    raw_uris = row.get("evidence_uris")
    if isinstance(raw_uris, str):
        try:
            evidence_uris: list[str] = json.loads(raw_uris)
        except json.JSONDecodeError:
            evidence_uris = [raw_uris]
    elif isinstance(raw_uris, list):
        evidence_uris = list(raw_uris)
    else:
        evidence_uris = []

    if not evidence_uris:
        raise ValueError(
            f"Assertion {assertion_id} has no evidence_uris — "
            "cannot resolve source for chunk lookup (spec §4.2 URI-pair mandate)"
        )

    rag_source = normalize_evidence_uri(evidence_uris[0])
    chunk_index = _parse_chunk_index(stored_chunk_id)

    body = {"groups": [{"source": rag_source, "chunk_indices": [chunk_index]}]}

    logger.debug(
        "resolve_assertion_chunk: assertion=%d chunk_id=%s source=%s idx=%d",
        assertion_id,
        stored_chunk_id,
        rag_source,
        chunk_index,
    )

    with make_sync_client(DEFAULT_STARGATE_URL, timeout=_RAG_TIMEOUT) as client:
        resp = client.post("/api/v1/rag/chunks_by_index", json=body)
        resp.raise_for_status()

    chunks = resp.json().get("chunks", [])
    if not chunks:
        raise ValueError(
            f"Assertion {assertion_id}: RAG returned no chunks for "
            f"source={rag_source!r} chunk_index={chunk_index} — "
            "source may not be indexed or chunk may have been deleted"
        )

    chunk = chunks[0]
    returned_chunk_id = str(chunk.get("chunk_id", ""))

    if returned_chunk_id != stored_chunk_id:
        raise ChunkIdMismatchError(assertion_id, stored_chunk_id, returned_chunk_id)

    return dict(chunk)
