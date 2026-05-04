"""Path constants, sandbox resolution, and trash-routing utilities."""

from __future__ import annotations

import os
from pathlib import Path

_SANDBOX_ROOT = Path("/data/files")
_TRASH_ROOT = _SANDBOX_ROOT / "trash"
_SHARED_IMAGE_DIR = Path(
    os.environ.get("MCP_SHARED_IMAGE_DIR", str(_SANDBOX_ROOT / ".shared-images"))
)
_SHARED_IMAGE_HOST_ROOT = Path(
    os.environ.get("MCP_SHARED_IMAGE_HOST_ROOT", str(_SHARED_IMAGE_DIR))
)
_ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}
_EDITABLE_SUFFIXES = {
    ".md",
    ".txt",
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".csv",
    ".sh",
    ".bash",
    ".js",
    ".ts",
    ".html",
    ".css",
    ".xml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".log",
}
_BINARY_MAX_BYTES = 20 * 1024 * 1024

_FS_WORKFLOW_HINTS: dict[str, str] = {
    "delete": (
        "file moved to trash/ — restore via fs(op='move', path='trash/<original>', target='<original>') "
        "or purge permanently via remove_directory('trash')"
    ),
    "delete_workspaces": (
        "file moved to cortex trash/ — restore via "
        "fs(sandbox='cortex', op='move', path='trash/<original>', target='<original>') "
        "or purge permanently via fs(sandbox='cortex', op='remove_directory', path='trash')"
    ),
    "move": (
        "next: cortex entity_update or assert with source_uri pointing to the "
        "new permanent path if this file is evidence for an entity"
    ),
    "copy": (
        "next: cortex entity_create or assert with source_uri pointing to the "
        "copy destination if the copy should be tracked as a separate document"
    ),
    "write": (
        "next: cortex entity_create or assert with source_uri pointing to this "
        "path if this is a new document that should be tracked"
    ),
    "write_binary": (
        "next: cortex entity_create or assert with source_uri pointing to this "
        "path; or use document_ocr for PDFs/images requiring text extraction"
    ),
}

_DROPBOX_READ_HINT = (
    "This file is in dropbox/ (temporary staging). After reading: "
    "(1) check if it has a document: entity in Cortex — if not, create one; "
    "(2) move to a permanent path via fs move; "
    "(3) seed cortex assertions with source_uri pointing to the permanent path"
)


def _normalize_files_reference(path: str) -> str:
    """Accept either a relative sandbox path or a `files://` URI."""
    return path.removeprefix("files://")


def _safe_path(relative: str) -> Path:
    """Resolve *relative* inside the sandbox, rejecting traversal attempts.

    Raises ValueError if the resolved path escapes the sandbox root.
    """
    clean = relative.lstrip("/")
    candidate = (_SANDBOX_ROOT / clean).resolve()
    try:
        candidate.relative_to(_SANDBOX_ROOT)
    except ValueError:
        raise ValueError(
            f"Path {relative!r} resolves outside sandbox; traversal rejected"
        )
    return candidate


def _trash_destination(original_rel: str) -> Path:
    """Return a non-colliding path inside trash/ for *original_rel*.

    Layout is path-preserving: trash/<original-rel>.  On collision the stem
    gets a zero-padded numeric suffix: <stem>-01.<ext>, <stem>-02.<ext>, …

    ∀ n ∈ 1..99: candidate is tried in order; first free slot wins.
    Raises FileExistsError if all 99 slots are occupied (practically impossible).
    """
    candidate = _TRASH_ROOT / original_rel.lstrip("/")
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    parent = candidate.parent
    for n in range(1, 100):
        numbered = parent / f"{stem}-{n:02d}{suffix}"
        if not numbered.exists():
            return numbered
    raise FileExistsError(
        f"Trash collision: {original_rel!r} already has 99 copies in trash/"
    )
