"""``reference-transaction`` hook logic (Lane-B forward-only git integrity).

Invoked from ``hooks/reference-transaction`` during ``prepared`` and ``committed``.
Always refuses non-fast-forward updates to existing ``refs/heads/*`` refs from
any worktree (hub or linked). Ref creates (``oldrev`` all-zero) and branch
deletes (``newrev`` all-zero) stay allowed.

A fast-forward of ``refs/heads/master`` from any worktree moves the hub ref
without updating the checked-out index (``git update-ref``). ``prepared``
refuses that move when a landed path holds bytes that are neither the old
nor the new blob. ``committed`` checks out the landed paths that still hold
the pre-move blob so the hub index matches HEAD.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_ZERO_OID = "0" * 40
_REFS_HEADS_PREFIX = "refs/heads/"
_MASTER_REF = "refs/heads/master"
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


def is_ancestor(repo: Path, *, oldrev: str, newrev: str) -> bool:
    if oldrev == _ZERO_OID or newrev == _ZERO_OID:
        return False
    proc = _git_capture(repo, "merge-base", "--is-ancestor", oldrev, newrev)
    return proc.returncode == 0


def _ref_exists(repo: Path, ref_name: str) -> bool:
    proc = _git_capture(repo, "show-ref", "--verify", "--hash", ref_name)
    return proc.returncode == 0


def _resolve_ref(repo: Path, ref_name: str) -> str | None:
    proc = _git_capture(repo, "rev-parse", "--verify", ref_name)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def refuse_reason(
    repo: Path,
    *,
    updates: list[RefUpdate],
    dispatch_id: str,
) -> RefUpdate | None:
    """Return the first offending ref update, or ``None`` when the txn is allowed."""
    _ = dispatch_id
    toplevel = repo.resolve()

    for upd in updates:
        if upd.ref_name == _HEAD_REF:
            # Detach / direct OID updates: 2.43 residual — not a refuse signal.
            continue

        if not upd.ref_name.startswith(_REFS_HEADS_PREFIX):
            continue

        if upd.newrev == _ZERO_OID:
            continue

        if upd.oldrev == _ZERO_OID:
            # ``switch -C`` / recreate paths may queue a pseudo-create for an existing ref.
            if _ref_exists(toplevel, upd.ref_name):
                current = _resolve_ref(toplevel, upd.ref_name)
                if current and not is_ancestor(
                    toplevel, oldrev=current, newrev=upd.newrev
                ):
                    return upd
            continue

        if not is_ancestor(toplevel, oldrev=upd.oldrev, newrev=upd.newrev):
            return upd

    return None


def _master_ff_updates(repo: Path, updates: list[RefUpdate]) -> list[RefUpdate]:
    """Fast-forward moves of ``refs/heads/master`` in this transaction."""
    found: list[RefUpdate] = []
    for upd in updates:
        if upd.ref_name != _MASTER_REF:
            continue
        if upd.oldrev == _ZERO_OID or upd.newrev == _ZERO_OID:
            continue
        if upd.oldrev == upd.newrev:
            continue
        if not is_ancestor(repo, oldrev=upd.oldrev, newrev=upd.newrev):
            continue
        found.append(upd)
    return found


def _load_hub_tree_sync():
    """Import ``hub_tree_sync`` from the repo that contains this module.

    Hook cwd is the worktree that ran git, so ``services`` resolves to that
    tree, while the hook script's ``PYTHONPATH`` may still point at the hub
    libs. A lane copy of this module must load the lane's repair function.
    """
    import sys

    libs = str(Path(__file__).resolve().parents[2] / "libs")
    if libs not in sys.path:
        sys.path.insert(0, libs)
    import git_integrate.hub_tree_sync as hub_tree_sync

    return hub_tree_sync


def _emit_refused(dispatch_id: str, repo: Path, ref_name: str) -> None:
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_hub_checkout_refused,
    )

    try:
        emit_sdk_lane_hub_checkout_refused(
            dispatch_id=dispatch_id,
            toplevel=str(repo.resolve()),
            ref_name=ref_name,
        )
    except Exception:
        pass


def run_reference_transaction_hook(
    *,
    state: str,
    stdin_text: str,
    dispatch_id: str | None,
    repo: Path | None = None,
) -> int:
    """Hook entry: return process exit code (0 allow, HOOK_POLICY_REFUSE_EXIT refuse)."""
    if state not in {"prepared", "committed"}:
        return 0
    if os.environ.get("GIW_MASTER_REF_REPAIR") == "1":
        return 0

    cwd = repo or Path.cwd()
    updates = parse_ref_updates(stdin_text.splitlines())
    if not updates:
        return 0

    dispatch_marker = (dispatch_id or "").strip() or "none"
    master_updates = _master_ff_updates(cwd, updates)
    if state == "committed":
        if not master_updates:
            return 0
        try:
            hub_tree_sync = _load_hub_tree_sync()
        except Exception:
            return 0
        for upd in master_updates:
            try:
                hub_tree_sync.repair_master_worktree(str(cwd), upd.oldrev, upd.newrev)
            except Exception:
                return 0
        return 0

    offending = refuse_reason(cwd, updates=updates, dispatch_id=dispatch_marker)
    if offending is not None:
        _emit_refused(dispatch_marker, cwd, offending.ref_name)
        return HOOK_POLICY_REFUSE_EXIT

    if not master_updates:
        return 0
    try:
        hub_tree_sync = _load_hub_tree_sync()
    except Exception:
        return 0

    for upd in master_updates:
        try:
            reason = hub_tree_sync.master_ref_move_refusal(
                str(cwd), upd.oldrev, upd.newrev
            )
        except Exception:
            return 0
        if not reason:
            continue
        print(
            f"reference-transaction: refusing {upd.ref_name} {reason}",
            file=sys.stderr,
        )
        _emit_refused(dispatch_marker, cwd, upd.ref_name)
        return HOOK_POLICY_REFUSE_EXIT
    return 0


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
