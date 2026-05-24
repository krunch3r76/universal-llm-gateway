"""``promote_document_to_evidence`` MCP tool — phase-d.

Fresh dispatch surface (not a rename from ``ingest_binary``). Verifies the
source against a co-located extraction sidecar, creates a ``document:``
entity, and atomically moves source + sidecar into an evidence bundle with
``manifest.json``.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record
from universal_logging import get_logger

from . import _promote_document_helpers as _promote_helpers
from ._evidence_entity_ops import ensure_entity
from ._extract_document_helpers import compute_source_sha256
from ._file_helpers import resolve_files_path
from ._promote_document_helpers import (
    PromoteError,
    ResolvedSidecar,
    build_manifest,
    build_sidecar_moved,
    compose_bundle_paths,
    load_and_validate_sidecar,
    move_into_bundle,
    resolve_sidecar_path,
    write_manifest_atomic,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)


def _entity_name_from_id(entity_id: str, fallback: str) -> str:
    if ":" in entity_id:
        return entity_id.split(":", 1)[1].replace("-", " ").replace("_", " ")
    return fallback


def _promote(
    *,
    path: str,
    entity_id: str,
    entity_description: str,
    entity_attributes: dict[str, Any] | None,
    sidecar: str,
) -> dict[str, Any]:
    source_abs = resolve_files_path(path, root=_promote_helpers.FILES_ROOT)
    if not source_abs.exists():
        raise FileNotFoundError(f"File not found: {path!r}")
    if not source_abs.is_file():
        raise ValueError(f"Not a file: {path!r}")

    content_hash, _source_size = compute_source_sha256(source_abs)
    sidecar_abs = resolve_sidecar_path(source_abs, sidecar)

    resolved: ResolvedSidecar | None = None
    if sidecar_abs is not None:
        if not sidecar_abs.is_file():
            raise FileNotFoundError(f"Sidecar not found: {sidecar!r}")
        resolved = load_and_validate_sidecar(
            sidecar_abs,
            source_rel=path,
            source_sha256=content_hash,
        )

    bundle = compose_bundle_paths(
        content_hash=content_hash,
        source_basename=source_abs.name,
        sidecar_basename=resolved.path.name if resolved else None,
    )

    name = _entity_name_from_id(entity_id, Path(path).stem)
    entity_created, _existing = ensure_entity(
        entity_id=entity_id,
        name=name,
        description=entity_description,
        content_hash=content_hash,
        source_uri=bundle.source_uri,
        attributes=entity_attributes,
    )

    promoted_at = datetime.now(UTC)
    manifest_files: list[dict[str, Any]] = [
        {
            "path": bundle.source_rel_in_bundle,
            "sha256": content_hash,
            "role": "source",
        },
    ]

    sidecar_moved = build_sidecar_moved(
        status="absent",
        staging_path=None,
        evidence_uri=None,
        sha256=None,
        source_sha256_verified=False,
        canonical=None,
        partial=None,
    )

    if resolved is not None:
        sidecar_sha, _ = compute_source_sha256(resolved.path)
        sidecar_moved = build_sidecar_moved(
            status="moved",
            staging_path=resolved.rel_path,
            evidence_uri=bundle.source_uri,
            sha256=sidecar_sha,
            source_sha256_verified=True,
            canonical=resolved.canonical,
            partial=resolved.partial,
        )
        manifest_files.append(
            {
                "path": bundle.sidecar_rel_in_bundle,
                "sha256": sidecar_sha,
                "role": "sidecar",
                "canonical": resolved.canonical,
                "partial": resolved.partial,
                "args_hash": resolved.frontmatter.get("args_hash"),
            },
        )

    move_into_bundle(
        source_abs=source_abs,
        sidecar_abs=resolved.path if resolved else None,
        bundle=bundle,
    )

    manifest = build_manifest(
        entity_id=entity_id,
        content_hash=content_hash,
        promoted_at=promoted_at,
        files=manifest_files,
    )
    write_manifest_atomic(bundle.bundle_abs, manifest)

    evidence_path = f"{bundle.bundle_rel}{bundle.source_rel_in_bundle}"
    return {
        "entity_created": entity_created,
        "entity_id": entity_id,
        "content_hash": content_hash,
        "evidence_path": evidence_path,
        "bundle_path": bundle.bundle_rel,
        "source_uri": bundle.source_uri,
        "sidecar_moved": sidecar_moved,
        "canonical": resolved.canonical if resolved else None,
        "partial": resolved.partial if resolved else None,
    }


def register_promote_document_to_evidence_tools(mcp: FastMCP) -> None:
    """Register ``promote_document_to_evidence`` on the MCP surface."""

    @mcp.tool(title="Promote Document To Evidence")
    def promote_document_to_evidence(
        path: str,
        entity_id: str,
        entity_description: str,
        entity_attributes: dict[str, Any] | None = None,
        sidecar: str = "auto",
    ) -> dict[str, Any]:
        """Promote a staged document + sidecar into an evidence bundle.

        Available via:
        ``dispatch(tool="promote_document_to_evidence", arguments='{...}')``

        Re-computes the source SHA-256, discovers a co-located
        ``*.extracted.md`` sidecar (``sidecar="auto"`` default), validates
        frontmatter against ``extraction-sidecar-v1``, creates a
        ``document:`` entity, then atomically moves source + sidecar into
        ``evidence/<date>_<hash>_<name>/`` with ``manifest.json``.

        On entity 409 conflict (same ``entity_id``, different
        ``content_hash``) or duplicate evidence (same hash, different
        ``entity_id``): files remain in staging; response is an error.

        Workflow: ``extract_document`` → read sidecar → this tool →
        ``cortex(tool="assert", ...)``.

        Args:
            path: Source file relative to ``/data/files/``.
            entity_id: Target ``document:`` entity id.
            entity_description: Entity description (required).
            entity_attributes: Optional attributes dict merged into entity.
            sidecar: ``"auto"`` (discover by naming convention), explicit
                path relative to ``/data/files/``, or ``"none"`` to promote
                source only.

        Returns:
            ``{entity_created, entity_id, content_hash, evidence_path,
            bundle_path, source_uri, sidecar_moved, canonical, partial}``.
        """
        t0 = monotonic_now()
        record(
            "mcp.evidence.promote.called",
            path=path,
            entity_id=entity_id,
        )
        try:
            result = _promote(
                path=path,
                entity_id=entity_id,
                entity_description=entity_description,
                entity_attributes=entity_attributes,
                sidecar=sidecar,
            )
        except PromoteError as exc:
            record(
                "mcp.evidence.promote.failed",
                path=path,
                entity_id=entity_id,
                code=exc.code,
            )
            raise ValueError(f"{exc.code}: {exc.message}") from exc

        elapsed = monotonic_now() - t0
        record(
            "mcp.evidence.promote.completed",
            path=path,
            entity_id=entity_id,
            bundle_path=result["bundle_path"],
            entity_created=result["entity_created"],
            duration_s=round(elapsed, 3),
        )
        logger.info(
            "promote_document_to_evidence: %s → %s (entity_created=%s, %.1fs)",
            path,
            result["bundle_path"],
            result["entity_created"],
            elapsed,
        )
        return result
