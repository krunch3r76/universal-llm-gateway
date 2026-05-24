"""Helpers for ``promote_document_to_evidence`` (phase-d).

Sidecar discovery, bundle path composition, manifest serialization, and
atomic moves into ``evidence/<date>_<hash>_<name>/``. The MCP handler in
``promote_document_to_evidence.py`` orchestrates these after validation.

Spec: cortex://notes/system/specs/document-ingestion-redesign.md
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from ._extract_document_helpers import compute_source_sha256
from ._file_helpers import FILES_ROOT
from ._sidecar_schema import (
    SIDECAR_SUFFIX,
    _parse_leading_frontmatter,
    validate_sidecar_frontmatter,
)

EVIDENCE_DIR: Final[str] = "evidence"
CONTENT_HASH_PREFIX_LEN: Final[int] = 12
_CORTEX_EVIDENCE_PREFIX: Final[str] = "cortex://evidence/"


@dataclass(frozen=True, slots=True)
class ResolvedSidecar:
    """A sidecar path plus parsed frontmatter (post schema validation)."""

    path: Path
    rel_path: str
    frontmatter: dict[str, Any]
    canonical: bool
    partial: bool


@dataclass(frozen=True, slots=True)
class BundlePaths:
    """Relative and absolute paths for a promotion bundle."""

    bundle_rel: str
    bundle_abs: Path
    source_rel_in_bundle: str
    sidecar_rel_in_bundle: str | None
    source_uri: str | None


class PromoteError(Exception):
    """Terminal promotion failure with a stable machine-readable ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def sanitize_bundle_name(source_basename: str) -> str:
    """Derive ``<name>`` segment for ``evidence/<date>_<hash>_<name>/``."""
    stem = Path(source_basename).stem
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()
    return cleaned or "document"


def build_bundle_dir_name(
    *,
    promoted_date: str,
    content_hash: str,
    sanitized_name: str,
) -> str:
    """Compose ``<date>_<hash12>_<name>`` per spec § Evidence bundle layout."""
    prefix = content_hash[:CONTENT_HASH_PREFIX_LEN]
    return f"{promoted_date}_{prefix}_{sanitized_name}"


def discover_sidecar_auto(source_path: Path) -> Path:
    """Locate a co-located sidecar by naming convention.

    Prefers the canonical ``<basename>.extracted.md``. When only one
    variant exists, returns it. Multiple non-canonical variants without a
    canonical file raise — caller must pass an explicit ``sidecar=`` path.
    """
    parent = source_path.parent
    prefix = source_path.name
    pattern = f"{prefix}*{SIDECAR_SUFFIX}"
    candidates = sorted(parent.glob(pattern))
    if not candidates:
        raise PromoteError(
            "no_sidecar_found",
            f"No sidecar matching {pattern!r} beside {prefix!r}. "
            "Run extract_document first or pass sidecar= with an explicit path.",
        )
    canonical = parent / f"{prefix}{SIDECAR_SUFFIX}"
    if canonical.exists():
        return canonical
    if len(candidates) == 1:
        return candidates[0]
    names = ", ".join(p.name for p in candidates)
    raise PromoteError(
        "ambiguous_sidecar",
        f"Multiple sidecars for {prefix!r} ({names}); pass sidecar= explicitly.",
    )


def resolve_sidecar_path(source_path: Path, sidecar: str) -> Path | None:
    """Resolve the sidecar argument to an absolute path under FILES_ROOT."""
    if sidecar == "auto":
        return discover_sidecar_auto(source_path)
    if sidecar in ("none", ""):
        return None
    sidecar_path = (FILES_ROOT / sidecar.lstrip("/")).resolve()
    try:
        sidecar_path.relative_to(FILES_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(
            f"Sidecar path {sidecar!r} resolves outside /data/files/",
        ) from exc
    return sidecar_path


def load_and_validate_sidecar(
    sidecar_path: Path,
    *,
    source_rel: str,
    source_sha256: str,
) -> ResolvedSidecar:
    """Parse frontmatter, validate schema, verify ``source_sha256`` binding."""
    try:
        content = sidecar_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromoteError(
            "sidecar_unreadable",
            f"Cannot read sidecar at {sidecar_path}: {exc}",
        ) from exc

    frontmatter = _parse_leading_frontmatter(content)
    if frontmatter is None:
        raise PromoteError(
            "sidecar_invalid",
            f"Sidecar at {sidecar_path.name} has no valid YAML frontmatter.",
        )

    validation = validate_sidecar_frontmatter(frontmatter)
    if not validation.ok:
        raise PromoteError(
            "sidecar_schema_invalid",
            f"Sidecar frontmatter failed schema validation: {validation.errors}",
        )

    bound_sha = frontmatter.get("source_sha256")
    if bound_sha != source_sha256:
        raise PromoteError(
            "source_sha_mismatch",
            f"Sidecar source_sha256 {bound_sha!r} does not match current "
            f"source SHA {source_sha256!r}; re-run extract_document.",
        )

    bound_path = frontmatter.get("source_path")
    if bound_path and bound_path != source_rel:
        raise PromoteError(
            "source_path_mismatch",
            f"Sidecar source_path {bound_path!r} does not match promote "
            f"path {source_rel!r}.",
        )

    return ResolvedSidecar(
        path=sidecar_path,
        rel_path=str(sidecar_path.relative_to(FILES_ROOT)),
        frontmatter=frontmatter,
        canonical=bool(frontmatter.get("canonical")),
        partial=bool(frontmatter.get("partial")),
    )


def compose_bundle_paths(
    *,
    content_hash: str,
    source_basename: str,
    sidecar_basename: str | None,
    promoted_at: datetime | None = None,
) -> BundlePaths:
    """Compute bundle directory and ``cortex://evidence/…`` source_uri."""
    now = promoted_at or datetime.now(UTC)
    date_str = now.strftime("%Y-%m-%d")
    name = sanitize_bundle_name(source_basename)
    bundle_dir = build_bundle_dir_name(
        promoted_date=date_str,
        content_hash=content_hash,
        sanitized_name=name,
    )
    bundle_rel = f"{EVIDENCE_DIR}/{bundle_dir}"
    bundle_abs = FILES_ROOT / bundle_rel
    source_rel_in_bundle = source_basename
    sidecar_rel_in_bundle = sidecar_basename
    source_uri = None
    if sidecar_basename is not None:
        source_uri = f"{_CORTEX_EVIDENCE_PREFIX}{bundle_dir}/{sidecar_basename}"
    return BundlePaths(
        bundle_rel=f"{bundle_rel}/",
        bundle_abs=bundle_abs,
        source_rel_in_bundle=source_rel_in_bundle,
        sidecar_rel_in_bundle=sidecar_rel_in_bundle,
        source_uri=source_uri,
    )


def build_manifest(
    *,
    entity_id: str,
    content_hash: str,
    promoted_at: datetime,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """Structured manifest payload written to ``manifest.json``."""
    return {
        "entity_id": entity_id,
        "content_hash": content_hash,
        "promoted_at": promoted_at.isoformat(timespec="seconds").replace(
            "+00:00",
            "Z",
        ),
        "files": files,
    }


def atomic_move_file(src: Path, dest: Path) -> None:
    """Move ``src`` to ``dest`` atomically (same filesystem).

    ``Path.replace`` overwrites the destination atomically on POSIX, so no
    pre-unlink is needed (and a pre-unlink would open a window where dest
    is missing, breaking the atomicity guarantee).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dest)


def write_manifest_atomic(bundle_abs: Path, manifest: dict[str, Any]) -> None:
    """Write ``manifest.json`` via tmp + fsync + rename."""
    target = bundle_abs / "manifest.json"
    tmp = target.with_suffix(".json.tmp")
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(target)


def move_into_bundle(
    *,
    source_abs: Path,
    sidecar_abs: Path | None,
    bundle: BundlePaths,
) -> None:
    """Create bundle directory and move source (+ optional sidecar) into it."""
    bundle.bundle_abs.mkdir(parents=True, exist_ok=True)
    dest_source = bundle.bundle_abs / bundle.source_rel_in_bundle
    atomic_move_file(source_abs, dest_source)
    if sidecar_abs is not None and bundle.sidecar_rel_in_bundle is not None:
        dest_sidecar = bundle.bundle_abs / bundle.sidecar_rel_in_bundle
        atomic_move_file(sidecar_abs, dest_sidecar)


def build_sidecar_moved(
    *,
    status: str,
    staging_path: str | None,
    evidence_uri: str | None,
    sha256: str | None,
    source_sha256_verified: bool,
    canonical: bool | None,
    partial: bool | None,
) -> dict[str, Any]:
    """Structured ``sidecar_moved`` object for the tool response."""
    return {
        "status": status,
        "staging_path": staging_path,
        "evidence_uri": evidence_uri,
        "sha256": sha256,
        "source_sha256_verified": source_sha256_verified,
        "canonical": canonical,
        "partial": partial,
    }


def normalize_entity_content_hash(stored: str | None) -> str | None:
    """Strip optional ``sha256:`` prefix for comparisons."""
    if stored is None:
        return None
    if stored.startswith("sha256:"):
        return stored.removeprefix("sha256:")
    return stored
