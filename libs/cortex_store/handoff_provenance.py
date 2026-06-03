"""``handoff_provenance`` attribute block construction."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .handoff_paths import normalize_handoff_source_path, sha256_text


def compute_source_file_sha256(
    files_root: Path,
    source_path: str | None,
) -> str | None:
    """Return ``sha256:<hex>`` of the cortex file at *source_path* (graceful None)."""
    rel = normalize_handoff_source_path(source_path)
    if rel is None:
        return None
    try:
        abs_path = (files_root / rel).resolve()
        abs_path.relative_to(files_root.resolve())
        text = abs_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    return sha256_text(text)


def build_handoff_provenance(
    *,
    write_path: str,
    source_path: str | None,
    files_root: Path,
    written_at: str | None = None,
    derivation: str | None = None,
    source_section: str | None = None,
    source_file_sha256: str | None = None,
    derived_handoff_prompt_sha256: str | None = None,
    derived_at: str | None = None,
) -> dict[str, Any]:
    """Build the ``handoff_provenance`` block stamped on the transcript attribute."""
    written = written_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rel = normalize_handoff_source_path(source_path)
    file_hash = source_file_sha256
    if file_hash is None and rel is not None:
        file_hash = compute_source_file_sha256(files_root, rel)
    prov: dict[str, Any] = {
        "write_path": write_path,
        "written_at": written,
        "source_file": rel,
        "source_file_sha256": file_hash,
    }
    if derivation is not None:
        prov["derivation"] = derivation
    if source_section is not None:
        prov["source_section"] = source_section
    if derived_handoff_prompt_sha256 is not None:
        prov["derived_handoff_prompt_sha256"] = derived_handoff_prompt_sha256
    if derived_at is not None:
        prov["derived_at"] = derived_at
    return prov
