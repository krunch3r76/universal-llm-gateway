"""Scope freshness hashing and watch-path overlap for corpus hint refresh."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from services.rag.property_index import PropertyIndex

if TYPE_CHECKING:
    from services.rag.config import RagConfig

__all__ = [
    "compute_scope_files_hash",
    "detect_stale_scopes",
    "scopes_touching_watch_path",
]


def compute_scope_files_hash(
    property_index: PropertyIndex, source_prefixes: list[str]
) -> str:
    """SHA-256 of sorted basenames of distinct indexed sources under any prefix."""
    names: set[str] = set()
    for raw in source_prefixes:
        pfx = str(Path(raw).expanduser().resolve())
        norm = pfx.rstrip("/") + "/"
        for src in property_index.get_sources(prefix=norm):
            names.add(Path(src).name)
    sorted_names = sorted(names)
    payload = "\n".join(sorted_names)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def detect_stale_scopes(
    *,
    property_index: PropertyIndex,
    configured_scopes: dict[str, list[str]],
    scope_filter: set[str] | None = None,
) -> list[str]:
    """Return scope names where the current file-list hash differs from stored."""
    stale: list[str] = []
    for scope_name, source_prefixes in sorted(configured_scopes.items()):
        if scope_filter is not None and scope_name not in scope_filter:
            continue
        current = compute_scope_files_hash(property_index, source_prefixes)
        row = property_index.get_scope_freshness(scope_name)
        if row is None or row[0] != current:
            stale.append(scope_name)
    return stale


def scopes_touching_watch_path(config: RagConfig, watch_path: Path) -> set[str]:
    """Configured scope names whose path prefixes overlap *watch_path* on disk."""
    w = watch_path.expanduser().resolve()
    out: set[str] = set()
    for name, sdef in config.scopes.items():
        for pfx in sdef.prefixes:
            p = Path(pfx).expanduser().resolve()
            try:
                w.relative_to(p)
                out.add(name)
                continue
            except ValueError:
                pass
            try:
                p.relative_to(w)
                out.add(name)
            except ValueError:
                pass
    return out
