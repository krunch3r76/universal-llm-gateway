"""Hub ``reference-transaction`` hook logic (Lane-B git integrity B2).

Invoked from ``hooks/reference-transaction`` during the ``prepared`` phase only.
While ``CURSOR_SDK_DISPATCH_ID`` is set in the hub worktree, refuses ref creates
and non-fast-forward updates under ``refs/heads/*`` (git 2.43 prepared stdin).
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_ZERO_OID = "0" * 40
_REFS_HEADS_PREFIX = "refs/heads/"
_HEAD_REF = "HEAD"
_DISPATCH_ENV = "CURSOR_SDK_DISPATCH_ID"
_GIT_TIMEOUT_S = 30.0

# Distinct exit status for policy refusal; the bash wrapper maps this to hook exit 1.
HOOK_POLICY_REFUSE_EXIT = 2


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

    for upd in updates:
        if upd.ref_name == _HEAD_REF:
            # Detach / direct OID updates: 2.43 residual — not a refuse signal.
            continue

        if not upd.ref_name.startswith(_REFS_HEADS_PREFIX):
            continue

        if upd.newrev == _ZERO_OID:
            continue

        if upd.oldrev == _ZERO_OID:
            return upd

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
    """Hook entry: return process exit code (0 allow, HOOK_POLICY_REFUSE_EXIT refuse)."""
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

    return HOOK_POLICY_REFUSE_EXIT


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
