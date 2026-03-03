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

    Args:
        source_dir: Pipeline directory to copy.
        name: Sandbox name. Defaults to last two path components
              joined by ``-`` (e.g. ``consensus-v8.0``).

    Returns:
        Path to the created sandbox directory.

    Raises:
        FileNotFoundError: If *source_dir* does not exist.
        FileExistsError: If a sandbox with *name* already exists.
    """
    source = Path(source_dir).resolve()
    if not source.is_dir():
        msg = f"Source directory not found: {source}"
        raise FileNotFoundError(msg)
    if name is None:
        name = f"{source.parent.name}-{source.name}"
    target = SANDBOX_ROOT / name
    if target.exists():
        msg = f"Sandbox '{name}' already exists at {target}. Use 'clean' first."
        raise FileExistsError(msg)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


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
    """Yield all relative file paths under *root*."""
    for path in root.rglob("*"):
        if path.is_file():
            yield path.relative_to(root)
