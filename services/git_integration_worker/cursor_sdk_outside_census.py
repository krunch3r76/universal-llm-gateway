"""Outside-repo workspace census for cursor-sdk closeout.

Walks the workspaces mount for files that are not inside a registered repo.
Directory pruning happens during descent so closeout never stats a whole
checkout (``.git/``, ephemeral worktrees, swamp caches).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from universal_event_bus import Event, event_factory

from services.git_integration_worker.cursor_sdk_capture_status import (
    filter_manifest_swamp,
    is_swamp_excluded_path,
)
from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

_PRUNE_DIR_NAMES = frozenset(
    {
        ".git",
        ".cursor",
        ".pytest_cache",
        "__pycache__",
        "ulg-arc-worktrees",
    }
)


@event_factory
def FrontierSdkOutsideCensus(  # noqa: N802
    duration_ms: int,
    found_count: int,
    walked: bool,
    mount_root: str,
) -> Event:
    return Event(
        signal="frontier.sdk.closeout.outside_census",
        payload={
            "duration_ms": duration_ms,
            "found_count": found_count,
            "walked": walked,
            "mount_root": mount_root,
        },
        scope="node",
        role="observation",
    )


def emit_sdk_outside_census(
    *,
    duration_ms: int,
    found_count: int,
    walked: bool,
    mount_root: str,
) -> None:
    """Duration of one outside-repo census — loop-block regressions show up here."""
    emit_frontier_event(
        FrontierSdkOutsideCensus(
            duration_ms=duration_ms,
            found_count=found_count,
            walked=walked,
            mount_root=mount_root,
        )
    )


def snapshot_outside_repo_paths(
    mount_root: Path,
    repo_roots: list[Path] | None = None,
) -> frozenset[str]:
    """Workspaces-relative paths under *mount_root* but outside every registered repo."""
    from services.git_integration_worker.cursor_sdk_manifest import (
        registered_repo_roots,
    )

    started = time.perf_counter()
    roots = repo_roots or registered_repo_roots(mount_root)
    mount_resolved = mount_root.resolve()
    roots_resolved = {repo.resolve() for repo in roots}
    mount_s = str(mount_resolved)
    if roots_resolved == {mount_resolved} or not mount_resolved.is_dir():
        emit_sdk_outside_census(
            duration_ms=int((time.perf_counter() - started) * 1000),
            found_count=0,
            walked=False,
            mount_root=mount_s,
        )
        return frozenset()

    root_exact = {str(repo) for repo in roots_resolved}
    root_prefixes = tuple(f"{repo}{os.sep}" for repo in root_exact)
    found = _walk_outside(
        mount_resolved,
        root_exact=root_exact,
        root_prefixes=root_prefixes,
    )
    result = frozenset(filter_manifest_swamp(found))
    emit_sdk_outside_census(
        duration_ms=int((time.perf_counter() - started) * 1000),
        found_count=len(result),
        walked=True,
        mount_root=mount_s,
    )
    return result


def _under_repo(
    path_s: str, *, root_exact: set[str], root_prefixes: tuple[str, ...]
) -> bool:
    return path_s in root_exact or path_s.startswith(root_prefixes)


def _walk_outside(
    mount_resolved: Path,
    *,
    root_exact: set[str],
    root_prefixes: tuple[str, ...],
) -> set[str]:
    found: set[str] = set()
    stack = [mount_resolved]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name in _PRUNE_DIR_NAMES:
                            continue
                        try:
                            resolved = os.path.realpath(entry.path)
                        except OSError:
                            continue
                        if _under_repo(
                            resolved,
                            root_exact=root_exact,
                            root_prefixes=root_prefixes,
                        ):
                            continue
                        stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    rel = os.path.relpath(entry.path, mount_resolved)
                    if rel != "." and not is_swamp_excluded_path(rel):
                        found.add(rel)
        except OSError:
            continue
    return found
