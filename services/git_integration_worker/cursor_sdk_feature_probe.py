"""R2 feature-presence probe for cursor-sdk RunGitInfo (closeout-correctness)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_sdk_closeout import (
    extract_sdk_git_snapshot,
    sdk_fs_git_mismatch_reason,
)

LOCAL_BRIDGE_PATH_LABEL = "local-bridge"
CLOUD_SEND_PATH_LABEL = "cloud-send"
SDK_GIT_PROBE_ABSENT = "sdk_git_probe_absent"


@dataclass(frozen=True)
class ProbeResult:
    path_label: str
    git_available: bool
    sample_branch: str | None
    probed_at: float


_PROBE_CACHE: dict[str, ProbeResult] = {}


def clear_probe_cache() -> None:
    """Reset process-local probe cache (tests)."""
    _PROBE_CACHE.clear()


def _git_shape_from_result(result: Any | None) -> tuple[bool, str | None]:
    if result is None:
        return False, None
    snapshot = extract_sdk_git_snapshot(getattr(result, "git", None))
    if snapshot is None:
        return False, None
    branch = snapshot.get("branch")
    return True, str(branch) if branch else None


def probe_run_git_info(
    *,
    path_label: str,
    result: Any | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> ProbeResult:
    """Introspection-only probe cached per ``path_label`` for process lifetime.

    First call is fail-closed: without a ``RunResult.git`` shape on ``result`` or
    ``client_factory()``, ``git_available`` is False.
    """
    cached = _PROBE_CACHE.get(path_label)
    if cached is not None:
        return cached

    git_available, sample_branch = _git_shape_from_result(result)
    if not git_available and client_factory is not None:
        try:
            git_available, sample_branch = _git_shape_from_result(client_factory())
        except Exception:
            git_available = False
            sample_branch = None

    probe = ProbeResult(
        path_label=path_label,
        git_available=git_available,
        sample_branch=sample_branch,
        probed_at=time.time(),
    )
    _PROBE_CACHE[path_label] = probe
    return probe


def git_probe_degraded_reasons(
    *,
    probe: ProbeResult,
    sdk_git: dict[str, Any] | None,
    source_repo: Path,
) -> tuple[str, ...]:
    """Gate ``sdk_fs_mismatch`` on probe availability; fail-closed otherwise."""
    if not probe.git_available:
        return (SDK_GIT_PROBE_ABSENT,)
    mismatch = sdk_fs_git_mismatch_reason(sdk_git, source_repo)
    if mismatch:
        return (mismatch,)
    return ()
