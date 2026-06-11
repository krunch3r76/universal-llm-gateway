"""Inline handoff auto-persist for write resolution (derivation=auto_persisted)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .handoff_derivation import DERIVATION_AUTO_PERSISTED, DERIVATION_DETACHED_STRING
from .handoff_paths import sha256_bytes, sha256_text
from .handoff_provenance import build_handoff_provenance

_AUTO_PERSIST_REL_DIR = "notes/system/handoffs"


def auto_persist_inline_handoff(
    *,
    files_root: Path,
    session_id: str,
    prompt: str,
) -> tuple[str, str, str]:
    """Write inline prompt to canonical handoff file; return rel path + hashes."""
    rel = f"{_AUTO_PERSIST_REL_DIR}/{session_id}.md"
    abs_path = files_root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(prompt, encoding="utf-8")
    prompt_hash = sha256_text(prompt)
    file_hash = sha256_bytes(prompt.encode("utf-8"))
    return rel, prompt_hash, file_hash


def build_inline_handoff_provenance(
    *,
    files_root: Path,
    session_id: str | None,
    prompt: str,
    write_path: str,
    written_at: str,
) -> dict[str, Any]:
    """Provenance for inline-only handoff (auto_persisted or detached_string)."""
    if session_id:
        rel, prompt_hash, file_hash = auto_persist_inline_handoff(
            files_root=files_root,
            session_id=session_id,
            prompt=prompt,
        )
        return build_handoff_provenance(
            write_path=write_path,
            source_path=rel,
            files_root=files_root,
            written_at=written_at,
            derivation=DERIVATION_AUTO_PERSISTED,
            source_file_sha256=file_hash,
            derived_handoff_prompt_sha256=prompt_hash,
            derived_at=written_at,
        )
    return build_handoff_provenance(
        write_path=write_path,
        source_path=None,
        files_root=files_root,
        written_at=written_at,
        derivation=DERIVATION_DETACHED_STRING,
    )
