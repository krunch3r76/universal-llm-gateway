"""Revert the working-tree writes attributed to one superseded dispatch.

Attribution is the admit-time ``wt_baseline`` captured for implement dispatches
(``routes/cursor_sdk`` §friction 23001): paths whose porcelain code or content
hash moved between that snapshot and now belong to this dispatch's episode.

Shared-checkout safety (``shared-checkout-housekeeping``): only paths that exist
in ``HEAD`` are restored, one explicit pathspec at a time. Files the episode
*created* are reported, never deleted — an unattributed new path in a shared
checkout may belong to another actor. No ``git clean``, no tree-wide reset.
Missing baseline fails closed (``ok=False``) rather than reporting a clean tree.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_closeout import changed_paths

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 30


@dataclass(frozen=True)
class RevertReport:
    """Outcome of reverting one superseded dispatch's writes."""

    dispatch_id: str
    ok: bool
    restored: tuple[str, ...]
    created_left: tuple[str, ...]
    unrevertable: tuple[str, ...]
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serializable view for bus payloads and closeout evidence."""
        return {
            "dispatch_id": self.dispatch_id,
            "ok": self.ok,
            "restored": list(self.restored),
            "created_left": list(self.created_left),
            "unrevertable": list(self.unrevertable),
            "reason": self.reason,
        }


def _paths_in_head(source_repo: Path, paths: tuple[str, ...]) -> set[str]:
    if not paths:
        return set()
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "ls-tree",
                "-r",
                "--name-only",
                "-z",
                "HEAD",
                "--",
                *paths,
            ],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("supersede revert ls-tree failed repo=%s: %s", source_repo, exc)
        return set()
    return {chunk.decode() for chunk in proc.stdout.split(b"\0") if chunk}


def _restore_path(source_repo: Path, path: str) -> bool:
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "restore",
                "--source=HEAD",
                "--staged",
                "--worktree",
                "--",
                path,
            ],
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("supersede revert restore failed path=%s: %s", path, exc)
        return False
    return True


def revert_dispatch_writes(*, dispatch_id: str, source_repo: Path) -> RevertReport:
    """Restore git-tracked paths this dispatch changed since its admit baseline."""
    baseline = CursorDispatchLedger.instance().read_wt_baseline(dispatch_id=dispatch_id)
    if baseline is None:
        return RevertReport(
            dispatch_id=dispatch_id,
            ok=False,
            restored=(),
            created_left=(),
            unrevertable=(),
            reason="baseline_unavailable",
        )
    change_set = changed_paths(source_repo, baseline)
    candidates = tuple(dict.fromkeys((*change_set.modified, *change_set.deleted)))
    in_head = _paths_in_head(source_repo, candidates)
    restored: list[str] = []
    unrevertable: list[str] = [path for path in candidates if path not in in_head]
    for path in candidates:
        if path not in in_head:
            continue
        if _restore_path(source_repo, path):
            restored.append(path)
        else:
            unrevertable.append(path)
    report = RevertReport(
        dispatch_id=dispatch_id,
        ok=not unrevertable,
        restored=tuple(restored),
        created_left=tuple(change_set.created),
        unrevertable=tuple(sorted(set(unrevertable))),
        reason=None if not unrevertable else "unrevertable_paths_present",
    )
    logger.warning(
        "supersede revert dispatch_id=%s restored=%d created_left=%d "
        "unrevertable=%d ok=%s",
        dispatch_id,
        len(report.restored),
        len(report.created_left),
        len(report.unrevertable),
        report.ok,
    )
    return report
