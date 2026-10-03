"""revert_op: one ``git revert -m1`` of a land merge, in-process.

Manage imports this module. It does not call the worker HTTP. The master
land lease is acquired only for this revert land. Gate failure, CAS conflict,
or revert conflict is ``indeterminate``: the original land stays.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

from git_integrate.events import emit_git_integrate_gate_failed
from git_integrate import git_cas
from git_integrate.ops_common import _bounded_gate_output, _run_command
from git_integrate.schema import RC_GATE_FAILED

_logger = get_logger(__name__)

GitRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
_GATE_TIMEOUT = 300.0


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
    green_gate_cmd: list[str] | None = None,
) -> dict[str, Any]:
    """Land ``git revert -m1`` of *merge_sha* through gate, lease, and CAS.

    ``worker_http`` is always false. The revert SHA is read from
    ``git rev-parse HEAD`` on *source_repo* after a successful CAS.
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

    if green_gate_cmd is None:
        from services.git_integration_worker.config import load_config

        green_gate_cmd = list(load_config().green_gate_cmd)

    acquired = await asyncio.to_thread(acquire_lease, lease_key, holder)
    if not acquired:
        return {
            "status": "indeterminate",
            "reason": "land_lease_unavailable",
            "worker_http": False,
            "revert_calls": 1,
            "integration_id": integration_id,
        }

    master_before = await git_cas.current_sha(source_repo, "refs/heads/master")
    if not master_before:
        if release_lease is not None:
            await asyncio.to_thread(release_lease, lease_key, holder)
        return {
            "status": "indeterminate",
            "reason": "master_unreadable",
            "worker_http": False,
            "revert_calls": 1,
            "integration_id": integration_id,
        }

    try:
        result = await asyncio.to_thread(
            runner,
            ["git", "-C", source_repo, "revert", "-m1", "--no-edit", merge_sha],
        )
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

        gate = await _run_command(
            green_gate_cmd, cwd=source_repo, timeout=_GATE_TIMEOUT
        )
        if gate.returncode != 0:
            await asyncio.to_thread(
                runner,
                ["git", "-C", source_repo, "reset", "--hard", master_before],
            )
            emit_git_integrate_gate_failed(
                integration_id=integration_id,
                arc="revert",
                phase="revert",
                gate_cmd=" ".join(green_gate_cmd),
                gate_exit=gate.returncode,
                duration_s=0.0,
            )
            return {
                "status": "indeterminate",
                "reason": "revert_gate_failed",
                "reason_code": RC_GATE_FAILED,
                "worker_http": False,
                "revert_calls": 1,
                "integration_id": integration_id,
                **_bounded_gate_output(gate.stdout, gate.stderr),
            }

        adv = await git_cas.advance_master_cas(
            source_repo, source_repo, expected=master_before
        )
        if adv.non_ff:
            await asyncio.to_thread(
                runner,
                ["git", "-C", source_repo, "reset", "--hard", master_before],
            )
            return {
                "status": "indeterminate",
                "reason": "revert_cas_conflict",
                "worker_http": False,
                "revert_calls": 1,
                "integration_id": integration_id,
            }

        sha_proc = await asyncio.to_thread(
            runner,
            ["git", "-C", source_repo, "rev-parse", "HEAD"],
        )
        sha = (sha_proc.stdout or "").strip() if sha_proc.returncode == 0 else ""
        if not sha:
            return {
                "status": "indeterminate",
                "reason": "revert_sha_unreadable",
                "worker_http": False,
                "revert_calls": 1,
                "integration_id": integration_id,
            }
    finally:
        if release_lease is not None:
            await asyncio.to_thread(release_lease, lease_key, holder)

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
