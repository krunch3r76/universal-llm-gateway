"""Pre-push hook logic (Lane-B forward-only git integrity).

Refuses non-fast-forward updates to ``refs/heads/*`` on push and refuses
deleting ``refs/heads/master`` on the remote.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from services.git_integration_worker.lane_b_reference_transaction_hook import (
    _GIT_TIMEOUT_S,
    _ZERO_OID,
    HOOK_POLICY_REFUSE_EXIT,
    is_ancestor,
)

_REFS_HEADS_PREFIX = "refs/heads/"
_MASTER_SUFFIX = "refs/heads/master"


@dataclass(frozen=True, slots=True)
class PushUpdate:
    """One ``pre-push`` stdin line."""

    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def _git_capture(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )


def parse_push_updates(stdin_lines: list[str]) -> list[PushUpdate]:
    """Parse ``<local ref> <local sha> <remote ref> <remote sha>`` lines."""
    updates: list[PushUpdate] = []
    for line in stdin_lines:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) != 4:
            continue
        updates.append(
            PushUpdate(
                local_ref=parts[0],
                local_sha=parts[1],
                remote_ref=parts[2],
                remote_sha=parts[3],
            )
        )
    return updates


def refuse_push_reason(
    repo: Path,
    *,
    updates: list[PushUpdate],
) -> PushUpdate | None:
    """Return the first offending push update, or ``None`` when push is allowed."""
    toplevel = repo.resolve()
    for upd in updates:
        if not upd.remote_ref.startswith(_REFS_HEADS_PREFIX):
            continue

        if upd.local_sha == _ZERO_OID and upd.remote_ref == _MASTER_SUFFIX:
            return upd

        if upd.remote_sha == _ZERO_OID:
            continue

        if upd.local_sha == _ZERO_OID:
            continue

        if not is_ancestor(toplevel, oldrev=upd.remote_sha, newrev=upd.local_sha):
            return upd

    return None


def run_pre_push_hook(
    *,
    stdin_text: str,
    repo: Path | None = None,
) -> int:
    """Hook entry: return process exit code (0 allow, HOOK_POLICY_REFUSE_EXIT refuse)."""
    cwd = repo or Path.cwd()
    updates = parse_push_updates(stdin_text.splitlines())
    if not updates:
        return 0

    offending = refuse_push_reason(cwd, updates=updates)
    if offending is None:
        return 0

    return HOOK_POLICY_REFUSE_EXIT


def main() -> int:
    stdin_text = sys.stdin.read()
    return run_pre_push_hook(stdin_text=stdin_text)


if __name__ == "__main__":
    raise SystemExit(main())
