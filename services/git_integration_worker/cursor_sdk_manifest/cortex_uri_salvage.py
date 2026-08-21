"""Copy ``cortex:`` host-path leftovers into ``cortex_files_root``.

Detection alone left commissioned bytes on a workspace directory named
``cortex:``. Closeout salvage is the delivery repair: the suffix after that
component is the intended share-relative path. Does not delete the host tree.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from durable_io.atomic import durable_write_bytes, durable_write_text

from .cortex_uri_impersonation import is_cortex_host_path_impersonation

CORTEX_URI_SALVAGED_DEVIATION = "work:cortex_uri_salvaged"


def cortex_impersonation_relpath(raw: str) -> str | None:
    """Share-relative path after the ``cortex:`` component, or None."""
    if not is_cortex_host_path_impersonation(raw):
        return None
    parts = [part for part in raw.replace("\\", "/").split("/") if part != ""]
    try:
        index = parts.index("cortex:")
    except ValueError:
        return None
    rel = "/".join(parts[index + 1 :])
    if not rel or rel.startswith("/") or ".." in parts[index + 1 :]:
        return None
    return rel


def resolve_cortex_impersonation_source(
    raw: str,
    *,
    mount_root: Path | None,
    write_tree: Path | None,
) -> Path | None:
    """Return an existing file for *raw*, or None when nothing is on disk."""
    rel = cortex_impersonation_relpath(raw)
    candidates: list[Path] = []
    stripped = Path(raw.strip())
    if stripped.is_absolute():
        candidates.append(stripped)
    for root in (mount_root, write_tree):
        if root is None:
            continue
        candidates.append(root / raw)
        if rel is not None:
            candidates.append(root / "cortex:" / rel)
            candidates.append(root.parent / raw)
            candidates.append(root.parent / "cortex:" / rel)
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            if resolved.is_file():
                return resolved
        except OSError:
            continue
    return None


def salvage_cortex_host_path_impersonations(
    paths: Iterable[str],
    *,
    cortex_root: Path,
    mount_root: Path | None,
    write_tree: Path | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Copy impersonation files into *cortex_root*.

    Returns ``(salvaged_cortex_uris, remaining_unfulfilled_paths)``.
    Existing dest files count as salvaged and are not overwritten.
    """
    root = cortex_root.resolve()
    salvaged: list[str] = []
    remaining: list[str] = []
    seen_rel: set[str] = set()
    for raw in paths:
        if not is_cortex_host_path_impersonation(raw):
            continue
        rel = cortex_impersonation_relpath(raw)
        if rel is None or rel in seen_rel:
            if rel is None:
                remaining.append(raw)
            continue
        seen_rel.add(rel)
        dest = (root / rel).resolve()
        try:
            dest.relative_to(root)
        except ValueError:
            remaining.append(raw)
            continue
        if dest.is_file():
            salvaged.append(f"cortex://{rel}")
            continue
        source = resolve_cortex_impersonation_source(
            raw, mount_root=mount_root, write_tree=write_tree
        )
        if source is None:
            remaining.append(raw)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            durable_write_bytes(
                dest,
                source.read_bytes(),
                retain_store_root=root,
            )
        else:
            durable_write_text(dest, text, retain_store_root=root)
        if dest.is_file():
            salvaged.append(f"cortex://{rel}")
        else:
            remaining.append(raw)
    return tuple(salvaged), tuple(remaining)
