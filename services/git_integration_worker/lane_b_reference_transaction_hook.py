"""Hub ``reference-transaction`` hook logic (Lane-B git integrity B2).

Invoked from ``hooks/reference-transaction`` during the ``prepared`` phase only.
Refuses hub checkouts that move ``HEAD`` off ``master`` or force-move
``refs/heads/cursor-sdk/*`` while ``CURSOR_SDK_DISPATCH_ID`` is set.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_ZERO_OID = "0" * 40
_CURSOR_SDK_BRANCH_PREFIX = "refs/heads/cursor-sdk/"
_MASTER_SYMREF = "refs/heads/master"
_DISPATCH_ENV = "CURSOR_SDK_DISPATCH_ID"
_GIT_TIMEOUT_S = 30.0


@dataclass(frozen=True, slots=True)
class RefUpdate:
    """One ``reference-transaction`` stdin line."""

    oldrev: str
    newrev: str
    ref_name: str


def _git_capture(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )


def parse_ref_updates(stdin_lines: list[str]) -> list[RefUpdate]:
    """Parse ``<oldrev> <newrev> <ref>`` lines from hook stdin."""
    updates: list[RefUpdate] = []
    for line in stdin_lines:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) != 3:
            continue
        updates.append(RefUpdate(oldrev=parts[0], newrev=parts[1], ref_name=parts[2]))
    return updates


def resolve_hub_toplevel(from_repo: Path) -> Path:
    """Primary worktree path (hub checkout) for a linked repo."""
    from services.git_integration_worker.cursor_sdk_hub_land_scope import (
        resolve_hub_git_repo,
    )

    return resolve_hub_git_repo(from_repo.resolve())


def is_ancestor(repo: Path, *, oldrev: str, newrev: str) -> bool:
    if oldrev == _ZERO_OID or newrev == _ZERO_OID:
        return False
    proc = _git_capture(repo, "merge-base", "--is-ancestor", oldrev, newrev)
    return proc.returncode == 0


def head_symref(repo: Path) -> str | None:
    proc = _git_capture(repo, "symbolic-ref", "-q", "HEAD")
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def refuse_reason(
    repo: Path,
    *,
    updates: list[RefUpdate],
    dispatch_id: str,
) -> RefUpdate | None:
    """Return the first offending ref update, or ``None`` when the txn is allowed."""
    _ = dispatch_id
    toplevel = repo.resolve()
    hub = resolve_hub_toplevel(toplevel)
    if toplevel != hub.resolve():
        return None

    sym = head_symref(toplevel)
    on_master = sym == _MASTER_SYMREF

    for upd in updates:
        if upd.ref_name == "HEAD":
            if upd.newrev.startswith("ref: ") and upd.newrev[5:] != _MASTER_SYMREF:
                return upd
            continue

        if not upd.ref_name.startswith(_CURSOR_SDK_BRANCH_PREFIX):
            continue

        if upd.newrev == _ZERO_OID:
            continue

        if upd.oldrev == _ZERO_OID:
            if on_master:
                return upd
            continue

        if not is_ancestor(toplevel, oldrev=upd.oldrev, newrev=upd.newrev):
            return upd

    if on_master:
        for upd in updates:
            if upd.ref_name == _MASTER_SYMREF and upd.oldrev != _ZERO_OID:
                if not is_ancestor(toplevel, oldrev=upd.oldrev, newrev=upd.newrev):
                    return upd

    return None


def run_reference_transaction_hook(
    *,
    state: str,
    stdin_text: str,
    dispatch_id: str | None,
    repo: Path | None = None,
) -> int:
    """Hook entry: return process exit code (0 allow, non-zero refuse)."""
    if state != "prepared":
        return 0
    if not (dispatch_id or "").strip():
        return 0

    cwd = repo or Path.cwd()
    updates = parse_ref_updates(stdin_text.splitlines())
    if not updates:
        return 0

    offending = refuse_reason(cwd, updates=updates, dispatch_id=dispatch_id.strip())
    if offending is None:
        return 0

    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_hub_checkout_refused,
    )

    try:
        emit_sdk_lane_hub_checkout_refused(
            dispatch_id=dispatch_id.strip(),
            toplevel=str(cwd.resolve()),
            ref_name=offending.ref_name,
        )
    except Exception:
        pass

    return 1


def main() -> int:
    state = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    dispatch_id = os.environ.get(_DISPATCH_ENV)
    stdin_text = sys.stdin.read()
    return run_reference_transaction_hook(
        state=state,
        stdin_text=stdin_text,
        dispatch_id=dispatch_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
