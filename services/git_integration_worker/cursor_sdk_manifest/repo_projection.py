"""Repo-surface path extraction and op-intent ``ChangeSet`` projection.

Projects manifest repo entries into write-paths, all-file-op paths, and the
``(change_set, outside_repo_paths, dropped_non_file_entries)`` triple closeout
uses for ``files_*`` categories. Ceiling-adjacent (~240 projected SLOC): land
as one file; nested ``repo_projection/entry_classification.py`` only if
``scripts/modularize scan`` reports >300 after docstrings (R6). Invariant:
``_normalize_repo_path`` keeps its function-local ``canonicalize_capture_path``
import even though this module also imports it at top level (R5). Directory
targets classify as non-file (``None``), not repo paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from implement_admission.closeout_models import EffectsManifest

from services.git_integration_worker.cursor_sdk_capture_status import (
    ChangeSet,
    canonicalize_capture_path,
)

from . import mount_resolution
from . import surface_taxonomy

def manifest_repo_write_paths(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
) -> set[str]:
    """Repo paths from write-family manifest ops — runtime surface and write evidence."""
    if manifest is None:
        return set()
    section = manifest.surfaces.get("repo")
    if section is None:
        return set()
    paths: set[str] = set()
    for entry in section.entries:
        if entry.op not in surface_taxonomy._REPO_WRITE_OPS:
            continue
        path = _normalize_repo_path(entry.target, repo_root=source_repo)
        if path:
            paths.add(path)
    return paths


def manifest_repo_paths(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
) -> set[str]:
    """All repo file-op paths including read-family ``observed`` (dedup, branch-adjacent)."""
    if manifest is None:
        return set()
    section = manifest.surfaces.get("repo")
    if section is None:
        return set()
    paths: set[str] = set()
    for entry in section.entries:
        if entry.op not in surface_taxonomy._REPO_FILE_OPS:
            continue
        path = _normalize_repo_path(entry.target, repo_root=source_repo)
        if path:
            paths.add(path)
    return paths


def _dedupe_dropped_non_file_entries(
    entries: list[surface_taxonomy.DroppedNonFileEntry],
) -> list[surface_taxonomy.DroppedNonFileEntry]:
    seen: set[tuple[str, str, str, str]] = set()
    ordered: list[surface_taxonomy.DroppedNonFileEntry] = []
    for entry in entries:
        key = (entry["surface"], entry["op"], entry["target"], entry["reason"])
        if key in seen:
            continue
        seen.add(key)
        ordered.append(entry)
    return ordered


def repo_change_set_from_manifest(
    manifest: EffectsManifest | None,
    *,
    source_repo: Path | None = None,
    mount_root: Path | None = None,
    repo_roots: list[Path] | tuple[Path, ...] | None = None,
) -> tuple[ChangeSet | None, tuple[str, ...], list[surface_taxonomy.DroppedNonFileEntry]]:
    """Manifest op-intent projection — cross-check input for closeout files_* categories.

    Returns ``(change_set, outside_repo_paths, dropped_non_file_entries)``.
    """
    if manifest is None:
        return None, (), []
    section = manifest.surfaces.get("repo")
    if section is None:
        return ChangeSet(created=(), modified=(), deleted=()), (), []
    mount = (
        (mount_root or mount_resolution.resolve_mount_root(source_repo)).resolve()
        if source_repo
        else None
    )
    created: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    outside_repo: list[str] = []
    dropped: list[surface_taxonomy.DroppedNonFileEntry] = []
    roots = list(repo_roots) if repo_roots is not None else None
    for entry in section.entries:
        classification = _classify_manifest_repo_entry(
            entry.target,
            source_repo=source_repo,
            mount_root=mount,
            repo_roots=roots,
        )
        if classification is None:
            raw_target = str(entry.target or "").strip()
            if raw_target and raw_target != ".":
                dropped.append(
                    {
                        "surface": "repo",
                        "op": str(entry.op or ""),
                        "target": raw_target,
                        "reason": "non_file",
                    }
                )
            continue
        kind, path = classification
        if kind == "outside_repo":
            outside_repo.append(path)
            continue
        if entry.op not in surface_taxonomy._REPO_LABEL_OPS:
            continue
        if entry.op == "write":
            created.append(path)
        elif entry.op == "edit":
            modified.append(path)
        elif entry.op == "delete":
            deleted.append(path)
    return (
        ChangeSet(
            created=tuple(dict.fromkeys(created)),
            modified=tuple(dict.fromkeys(modified)),
            deleted=tuple(dict.fromkeys(deleted)),
        ),
        tuple(dict.fromkeys(outside_repo)),
        _dedupe_dropped_non_file_entries(dropped),
    )


def _classify_manifest_repo_entry(
    raw: str | None,
    *,
    source_repo: Path | None,
    mount_root: Path | None,
    repo_roots: list[Path] | None = None,
) -> tuple[Literal["repo", "outside_repo"], str] | None:
    """Return repo/outside_repo classification, or None when the entry is non-file."""
    if not raw or not str(raw).strip() or str(raw).strip() == ".":
        return None
    if source_repo is None:
        path = str(raw).strip().lstrip("/")
        return ("repo", path) if path and path != "." else None

    canon = canonicalize_capture_path(str(raw), source_repo=source_repo)
    if not canon.canonical_path and canon.scope == "external_or_unknown":
        if canon.canonicalization_reason == "empty_path":
            return None
        candidate = Path(str(raw).strip())
        if not candidate.is_absolute():
            return None
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if resolved.is_dir():
            return None
        if mount_root is not None:
            rel = mount_resolution.mount_relative_path(mount_root, resolved)
            if rel is not None:
                return ("outside_repo", rel)
        roots_resolved = {repo.resolve() for repo in (repo_roots or ())}
        if roots_resolved:
            for repo in roots_resolved:
                try:
                    resolved.relative_to(repo)
                    return None
                except ValueError:
                    continue
        return ("outside_repo", resolved.as_posix())

    path = canon.canonical_path
    if not path or path == ".":
        return None
    candidate = source_repo / path
    try:
        if candidate.exists() and candidate.is_dir():
            return None
    except OSError:
        return None
    if canon.scope == "external_or_unknown":
        if mount_root is None:
            return None
        try:
            path_obj = Path(path)
            resolved = (
                path_obj.resolve()
                if path_obj.is_absolute()
                else (source_repo / path).resolve()
            )
        except OSError:
            return None
        rel = mount_resolution.mount_relative_path(mount_root, resolved)
        if rel is not None:
            try:
                abs_path = (mount_root / rel).resolve()
                if abs_path.is_dir():
                    return None
                if abs_path.is_file():
                    return ("outside_repo", rel)
            except OSError:
                return None
        roots_resolved = {repo.resolve() for repo in (repo_roots or ())}
        if roots_resolved:
            for repo in roots_resolved:
                try:
                    resolved.relative_to(repo)
                    return None
                except ValueError:
                    continue
        if resolved.is_file():
            return ("outside_repo", resolved.as_posix())
        return None
    return ("repo", path)
def _normalize_repo_path(
    raw: str | None,
    repo_root: Path | str | None = None,
) -> str | None:
    if not raw:
        return None
    if repo_root is None:
        return raw.strip().lstrip("/")
    # Cycle-breaker: redundant vs top-level import; keep local so capture_status cycle stays deferred (R1/R5). Do not hoist.
    from services.git_integration_worker.cursor_sdk_capture_status import (
        canonicalize_capture_path,
    )

    canon = canonicalize_capture_path(raw, source_repo=Path(repo_root))
    return canon.canonical_path or None
