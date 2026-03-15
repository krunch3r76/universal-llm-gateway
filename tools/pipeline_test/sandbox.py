"""Sandbox management for pipeline experimentation.

Copies pipeline directories to an ephemeral location for editing
(model assignments, generation parameters, prompts) without touching
the repository. Changes are applied back explicitly via ``apply``.
"""

from __future__ import annotations

import filecmp
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

SANDBOX_ROOT = Path(os.environ.get("PIPELINE_SANDBOX_ROOT", "/tmp/pipeline_sandboxes"))


def create_sandbox(source_dir: str | Path, name: str | None = None) -> Path:
    """Copy a pipeline directory into the sandbox root.

    When *source_dir* is a version subdirectory (its parent contains
    ``models.yaml``), the whole domain directory is copied so that shared
    files (models.yaml, etc.) are available inside the sandbox.  The
    returned path is the version subdir *within* the sandbox so callers can
    pass it directly as ``--pipeline-dir``.

    Args:
        source_dir: Pipeline directory to copy.  May be either a domain
            directory (e.g. ``pipelines/rag``) or a version subdirectory
            (e.g. ``pipelines/rag/rag_context_v1``).
        name: Sandbox name. Defaults to last two path components of the
              copy root joined by ``-`` (e.g. ``rag-rag_context_v1``).

    Returns:
        Path to use as ``--pipeline-dir`` — either the sandbox root (domain
        case) or the version subdir within it (version subdir case).

    Raises:
        FileNotFoundError: If *source_dir* does not exist.
        FileExistsError: If a sandbox with *name* already exists.
    """
    source = Path(source_dir).resolve()
    if not source.is_dir():
        msg = f"Source directory not found: {source}"
        raise FileNotFoundError(msg)

    # If source is a version subdir, copy the whole domain directory so
    # shared files (models.yaml) are included in the sandbox.
    parent_has_models = (source.parent / "models.yaml").is_file()
    copy_root = source.parent if parent_has_models else source
    version_name = source.name if parent_has_models else None

    if name is None:
        name = f"{copy_root.parent.name}-{copy_root.name}"
        if version_name:
            name = f"{copy_root.name}-{version_name}"

    sandbox_root = SANDBOX_ROOT / name
    if sandbox_root.exists():
        msg = f"Sandbox '{name}' already exists at {sandbox_root}. Use 'clean' first."
        raise FileExistsError(msg)
    sandbox_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        copy_root, sandbox_root, ignore=shutil.ignore_patterns("__pycache__")
    )

    # Return the version subdir within the sandbox when applicable.
    return sandbox_root / version_name if version_name else sandbox_root


def apply_sandbox(name: str, target_dir: str | Path) -> list[Path]:
    """Copy only changed files from sandbox back into the repo.

    Returns:
        List of relative paths that were updated.

    Raises:
        FileNotFoundError: If sandbox or target does not exist.
    """
    sandbox = SANDBOX_ROOT / name
    if not sandbox.is_dir():
        msg = f"Sandbox '{name}' does not exist under {SANDBOX_ROOT}"
        raise FileNotFoundError(msg)
    target = Path(target_dir).resolve()
    if not target.is_dir():
        msg = f"Target directory not found: {target}"
        raise FileNotFoundError(msg)
    updated: list[Path] = []
    for rel_path in _iter_files(sandbox):
        src = sandbox / rel_path
        dst = target / rel_path
        if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        updated.append(rel_path)
    return updated


def list_sandboxes() -> list[Path]:
    """Return available sandbox directories."""
    if not SANDBOX_ROOT.exists():
        return []
    return sorted(p for p in SANDBOX_ROOT.iterdir() if p.is_dir())


def clean_sandbox(name: str | None = None) -> None:
    """Delete a specific sandbox, or all sandboxes when *name* is ``None``."""
    if name is None:
        if SANDBOX_ROOT.exists():
            shutil.rmtree(SANDBOX_ROOT)
        return
    sandbox = SANDBOX_ROOT / name
    if sandbox.exists():
        shutil.rmtree(sandbox)


def _iter_files(root: Path) -> Iterator[Path]:
    """Yield all relative file paths under *root*, skipping __pycache__."""
    for path in root.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path.relative_to(root)
