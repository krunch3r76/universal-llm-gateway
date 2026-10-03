"""revert_op: one ``git revert -m1`` of a land merge, in-process.

Manage imports this module. It does not call the worker HTTP. The master
land lease is acquired only for this revert land. A CAS or revert conflict
is ``indeterminate``: the original land stays, and this function does not
revert again.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

_logger = get_logger(__name__)

GitRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _default_git(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
    )


def _emit_revert_landed(*, integration_id: str, merge_sha: str, revert_sha: str) -> None:
    payload = {
        "integration_id": integration_id,
        "merge_sha": merge_sha,
        "revert_sha": revert_sha,
    }
    try:
        from mcp_events import record
    except ImportError:
        record = None  # type: ignore[assignment]
    if record is not None:
        record("git.land.revert_landed", **payload)


async def revert_op(
    *,
    source_repo: str,
    merge_sha: str,
    holder_op_id: str | None = None,
    git_runner: GitRunner | None = None,
    acquire_lease: Callable[[str, str], Any] | None = None,
    release_lease: Callable[[str, str], Any] | None = None,
) -> dict[str, Any]:
    """Land ``git revert -m1`` of *merge_sha* through the master land lease.

    ``worker_http`` is always false. Conflict returns ``indeterminate`` and
    does not issue a second revert.
    """
    integration_id = str(uuid.uuid4())
    holder = holder_op_id or integration_id
    runner = git_runner or _default_git
    lease_key = source_repo

    if acquire_lease is None:
        from services.git_integration_worker.cursor_sdk_land_lease import (
            master_land_lease_key,
            release_land_lease,
            try_acquire_land_lease,
        )

        lease_key = master_land_lease_key(source_repo)

        def _acquire(key: str, op_id: str) -> bool:
            return try_acquire_land_lease(lease_key=key, holder_op_id=op_id)

        def _release(key: str, op_id: str) -> bool:
            return release_land_lease(lease_key=key, holder_op_id=op_id)

        acquire_lease = _acquire
        release_lease = _release

    acquired = await asyncio.to_thread(acquire_lease, lease_key, holder)
    if not acquired:
        return {
            "status": "indeterminate",
            "reason": "land_lease_unavailable",
            "worker_http": False,
            "revert_calls": 1,
            "integration_id": integration_id,
        }
    try:
        result = await asyncio.to_thread(
            runner,
            ["git", "-C", source_repo, "revert", "-m1", "--no-edit", merge_sha],
        )
    finally:
        if release_lease is not None:
            await asyncio.to_thread(release_lease, lease_key, holder)

    if result.returncode != 0:
        _logger.info(
            "revert_op conflict merge_sha=%s code=%s",
            merge_sha,
            result.returncode,
        )
        return {
            "status": "indeterminate",
            "reason": "revert_conflict",
            "worker_http": False,
            "revert_calls": 1,
            "integration_id": integration_id,
            "stderr": (result.stderr or "")[:500],
        }

    revert_sha = (result.stdout or "").strip().splitlines()[-1:] or [""]
    sha = revert_sha[0].strip()
    _emit_revert_landed(
        integration_id=integration_id, merge_sha=merge_sha, revert_sha=sha
    )
    return {
        "status": "landed",
        "revert_sha": sha,
        "worker_http": False,
        "revert_calls": 1,
        "integration_id": integration_id,
        "signal": "git.land.revert_landed",
    }
