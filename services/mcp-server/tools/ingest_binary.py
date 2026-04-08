"""Binary evidence ingestion — persist under /data/files and register a document entity.

Use ``ingest_binary`` when an agent already has binary bytes in memory
(conversation upload, screenshot, generated PDF) and needs one durable write path
that also creates the corresponding Cortex ``document`` entity.

Prefer plain ``files`` operations for text-only artifacts or when no Cortex
entity should be created.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import mimetypes
import os
import re
import secrets
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ._file_helpers import FILES_ROOT, resolve_files_path
from .local_api import _relay

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_MAX_BYTES = 20 * 1024 * 1024
_EVIDENCE_ROOT = FILES_ROOT / "evidence"
_B64_CHUNK_CHARS = 1024 * 1024
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _normalize_binary_path(path: str) -> tuple[str, Path]:
    """Validate a sandboxed evidence path and return (clean relative path, abs path)."""
    cleaned = path.strip()
    if not cleaned:
        raise ValueError("path is required")

    pure = PurePosixPath(cleaned.lstrip("/"))
    if pure.is_absolute():
        raise ValueError("path must be relative to /data/files")
    if ".." in pure.parts:
        raise ValueError("path traversal rejected")
    if pure.parts[:1] != ("evidence",):
        raise ValueError("path must live under evidence/")

    relative_path = pure.as_posix()
    abs_path = resolve_files_path(relative_path)
    try:
        abs_path.relative_to(_EVIDENCE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("path must resolve under /data/files/evidence") from exc
    return relative_path, abs_path


def _default_entity_id(dest: Path) -> str:
    """Build a stable document entity id from the destination filename."""
    slug = _SLUG_RE.sub("-", dest.stem.lower()).strip("-")
    if not slug:
        slug = "binary"
    return f"document:{slug}"


def _default_entity_name(dest: Path) -> str:
    """Humanize the filename stem for the default document name."""
    tokens = re.split(r"[-_]+", dest.stem)
    label = " ".join(token for token in tokens if token).strip()
    return label.title() if label else dest.name


def _decode_base64_to_temp(content_base64: str, temp_path: Path) -> tuple[int, str]:
    """Stream-decode base64 into *temp_path* while enforcing the size limit."""
    sha256 = hashlib.sha256()
    total_bytes = 0
    remainder = ""
    temp_path.parent.mkdir(parents=True, exist_ok=True)

    with temp_path.open("xb") as handle:
        for start in range(0, len(content_base64), _B64_CHUNK_CHARS):
            chunk = remainder + content_base64[start : start + _B64_CHUNK_CHARS]
            if not chunk:
                continue

            decode_upto = len(chunk) - (len(chunk) % 4)
            if decode_upto == 0:
                remainder = chunk
                continue

            to_decode = chunk[:decode_upto]
            remainder = chunk[decode_upto:]
            try:
                decoded = base64.b64decode(to_decode, validate=True)
            except binascii.Error as exc:
                raise ValueError("content_base64 is not valid base64") from exc

            total_bytes += len(decoded)
            if total_bytes > _MAX_BYTES:
                raise ValueError("decoded binary exceeds 20MB limit")
            handle.write(decoded)
            sha256.update(decoded)

        if remainder:
            try:
                decoded = base64.b64decode(remainder, validate=True)
            except binascii.Error as exc:
                raise ValueError("content_base64 is not valid base64") from exc
            total_bytes += len(decoded)
            if total_bytes > _MAX_BYTES:
                raise ValueError("decoded binary exceeds 20MB limit")
            handle.write(decoded)
            sha256.update(decoded)

        handle.flush()
        os.fsync(handle.fileno())

    return total_bytes, sha256.hexdigest()


def _create_document_entity(
    *,
    entity_id: str,
    entity_name: str,
    entity_description: str | None,
    source_uri: str,
    sha256_hex: str,
) -> tuple[dict[str, Any], bool]:
    """Create the Cortex document entity, special-casing 409 conflicts."""
    body: dict[str, Any] = {
        "id": entity_id,
        "type": "document",
        "name": entity_name,
        "source_uri": source_uri,
        "content_hash": f"sha256:{sha256_hex}",
        "status": "confirmed",
    }
    if entity_description:
        body["description"] = entity_description

    result = _relay("cortex-api", "POST", "/entities", body=body)
    if "error" in result:
        if result.get("status_code") == 409:
            return result, False
        detail = result.get("body", "")
        if detail:
            raise RuntimeError(
                f"cortex-api entity create failed: {result['error']} — {detail}"
            )
        raise RuntimeError(f"cortex-api entity create failed: {result['error']}")
    return result, True


def register_ingest_binary_tools(mcp: FastMCP) -> None:
    """Register binary evidence ingestion as a dispatch-only tool."""

    @mcp.tool(title="Ingest Binary")
    def ingest_binary(
        path: str,
        content_base64: str,
        media_type: str | None = None,
        entity_id: str | None = None,
        entity_name: str | None = None,
        entity_description: str | None = None,
    ) -> dict[str, Any]:
        """Persist binary evidence under `evidence/` and create a Cortex document entity.

        Use when an agent already has binary bytes and needs a first-class evidence
        artifact in one step: store the file under `/data/files/evidence/`, compute
        a stable content hash, and register the corresponding `document:*` entity.

        Prefer `files(op="write", ...)` for text artifacts or cases where no Cortex
        entity should be created. This tool is specifically for Cortex-primary
        evidence ingestion.

        Args:
            path: Relative evidence path under `/data/files/`, e.g.
                `evidence/2026-03-26_sherwin-poa-notebook.jpg`.
            content_base64: Base64-encoded binary payload.
            media_type: Optional MIME type. If omitted, inferred from the filename.
            entity_id: Optional `document:*` entity id. Defaults to filename-based slug.
            entity_name: Optional human-readable document name.
            entity_description: Optional document description stored on the entity.

        Returns:
            Metadata including path, byte count, SHA-256, entity id, and whether the
            entity was newly created.
        """
        if not content_base64:
            raise ValueError("content_base64 is required")

        relative_path, dest = _normalize_binary_path(path)
        effective_entity_id = entity_id or _default_entity_id(dest)
        effective_entity_name = entity_name or _default_entity_name(dest)
        effective_media_type = media_type or mimetypes.guess_type(dest.name)[0]
        if effective_media_type is None:
            effective_media_type = "application/octet-stream"

        temp_name = f".{dest.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
        temp_path = dest.parent / temp_name

        try:
            total_bytes, sha256_hex = _decode_base64_to_temp(content_base64, temp_path)
            _, entity_created = _create_document_entity(
                entity_id=effective_entity_id,
                entity_name=effective_entity_name,
                entity_description=entity_description,
                source_uri=relative_path,
                sha256_hex=sha256_hex,
            )

            if dest.exists() and entity_created:
                raise FileExistsError(f"Destination already exists: {relative_path}")

            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, dest)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

        logger.info(
            "ingest_binary: stored %s (%d bytes) as %s",
            relative_path,
            total_bytes,
            effective_entity_id,
        )
        return {
            "path": relative_path,
            "full_path": str(dest),
            "bytes": total_bytes,
            "media_type": effective_media_type,
            "sha256": sha256_hex,
            "entity_id": effective_entity_id,
            "entity_created": entity_created,
        }
