"""Git integration MCP tools — thin relay to git-integration-worker via Stargate.

Routes ``git_integrate``, ``git_land``, ``git_status``, ``git_diff``, and
``git_commit`` to ``/api/v1/git/{integrate,land,status,diff,commit}``.
Request/response shapes mirror the worker OpenAPI
(``services/git_integration_worker/models/api.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_SYNC_TIMEOUT = 60.0
# integrate runs pull + green-gate + CAS; budget above sync probes.
_INTEGRATE_TIMEOUT = 300.0


async def _relay(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    async with make_async_client(DEFAULT_STARGATE_URL, timeout=timeout) as client:
        try:
            resp = await client.request(method, path, json=json_body, params=params)
        except httpx.RequestError as exc:
            logger.error("git relay transport failure: %s %s — %s", method, path, exc)
            return {
                "error": {
                    "code": "git_integration_worker_unreachable",
                    "message": str(exc),
                }
            }

    if resp.status_code >= 400:
        return _http_error_to_envelope(resp)
    try:
        return resp.json()
    except ValueError:
        return {"error": {"code": "invalid_response", "message": resp.text[:200]}}


def _http_error_to_envelope(resp: httpx.Response) -> dict[str, Any]:
    try:
        body: dict[str, Any] = resp.json()
    except ValueError:
        body = {}
    detail = body.get("detail")
    if isinstance(detail, dict):
        return {
            "status": "rejected",
            "reason_code": detail.get("reason_code", "relay_error"),
            "reason": detail.get("reason", f"HTTP {resp.status_code}"),
        }
    err = body.get("error")
    if isinstance(err, dict):
        return {
            "status": "rejected",
            "reason_code": err.get("code", "relay_error"),
            "reason": err.get("message", f"HTTP {resp.status_code}"),
        }
    return {
        "status": "rejected",
        "reason_code": "relay_error",
        "reason": f"HTTP {resp.status_code}",
    }


def register_git_integrate_tools(mcp: FastMCP) -> None:
    """Register git_integrate, git_land, git_status, git_diff, and git_commit on the MCP catalog."""

    @mcp.tool(title="Git Integrate")
    async def git_integrate(  # noqa: PLR0913 — mirrors IntegrateRequest fields
        arc: str,
        phase: str,
        worktree_path: str,
        approval: str,
        expected_diff_sha256: str,
        remove_worktree: bool = True,
    ) -> dict[str, Any]:
        """Atomically merge a reviewed arc worktree into master.

        The lead calls this ONCE — it pulls master, runs the green-gate on the
        integrated result, advances master at the ref level, and tears down the
        worktree. Replaces the multi-step stash→merge→commit→remove choreography.
        ``approval`` is the operator's approval bound to ``expected_diff_sha256``
        (obtain the hash from ``git_diff`` first).

        Args:
            arc: Plan slug; worktree branch must be ``arc/<arc>``.
            phase: Phase label for audit events.
            worktree_path: Absolute path to the arc worktree.
            approval: Operator approval string bound to the diff fingerprint.
            expected_diff_sha256: SHA-256 of the approved unified diff from
                ``git_diff``.
            remove_worktree: Remove the arc worktree after successful integration
                (default True).

        Returns:
            Worker ``IntegrateResponse`` envelope (``integration_id``, ``status``,
            ``reason_code``, ``reason``).
        """
        return await _relay(
            "POST",
            "/api/v1/git/integrate",
            json_body={
                "arc": arc,
                "phase": phase,
                "worktree_path": worktree_path,
                "approval": approval,
                "expected_diff_sha256": expected_diff_sha256,
                "remove_worktree": remove_worktree,
            },
            timeout=_INTEGRATE_TIMEOUT,
        )

    @mcp.tool(title="Git Land")
    async def git_land(  # noqa: PLR0913 — mirrors LandRequest fields
        arc: str,
        phase: str,
        worktree_path: str,
        approval: str,
        expected_diff_sha256: str,
        commit_message: str = "",
        remove_worktree: bool = True,
    ) -> dict[str, Any]:
        """One atomic operator-gated land — commit, merge, gate, ref-advance, teardown.

        Commits the reviewed arc worktree when dirty (``commit_message`` required),
        merges master into the arc, runs the server green gate, advances master at
        the ref level, and tears down the worktree. Obtain ``expected_diff_sha256``
        from ``git_diff`` first (dirty-aware fingerprint).

        Args:
            arc: Plan slug; worktree branch must be ``arc/<arc>``.
            phase: Phase label for audit events.
            worktree_path: Absolute path to the arc worktree.
            approval: Operator approval string bound to the diff fingerprint.
            expected_diff_sha256: SHA-256 of the approved diff from ``git_diff``.
            commit_message: Commit message when the worktree has uncommitted changes.
            remove_worktree: Remove the arc worktree after successful land
                (default True).

        Returns:
            Worker land envelope (``integration_id``, ``status``, ``committed``,
            ``commit_sha``, ``master_sha``, …). ``master_sha`` is the advanced
            tip of ``landed_ref`` (refs/heads/master) in source_repo — the
            authoritative land target. The live working checkout ff-pulls on its
            own cadence and origin push is operator-discretionary; neither is
            implied by a completed land (see ``land_report``). Reconcile
            "landed" claims against the ref reachability, not a working tree.
        """
        return await _relay(
            "POST",
            "/api/v1/git/land",
            json_body={
                "arc": arc,
                "phase": phase,
                "worktree_path": worktree_path,
                "approval": approval,
                "expected_diff_sha256": expected_diff_sha256,
                "commit_message": commit_message,
                "remove_worktree": remove_worktree,
            },
            timeout=_INTEGRATE_TIMEOUT,
        )

    @mcp.tool(title="Git Status")
    async def git_status(worktree_path: str) -> dict[str, Any]:
        """Read-only arc worktree status (branch, dirty flag).

        Does not acquire the integrate gate. Use before review to confirm the
        worktree exists and is on the expected ``arc/<arc>`` branch.

        Args:
            worktree_path: Absolute path to the arc worktree.

        Returns:
            Worker ``StatusResponse`` (``branch``, ``dirty``, ``status``, …).
        """
        return await _relay(
            "GET",
            "/api/v1/git/status",
            params={"worktree_path": worktree_path},
            timeout=_SYNC_TIMEOUT,
        )

    @mcp.tool(title="Git Diff")
    async def git_diff(
        worktree_path: str,
        path_filter: str = "",
        include_full_diff: bool = False,
    ) -> dict[str, Any]:
        """Compact change-set envelope of what would land, plus ``diff_sha256``.

        Read-only; does not acquire the integrate gate. By default returns a
        compact envelope (``diff`` empty) plus ``diff_sha256``, ``diffstat``,
        ``branch``, and ``includes_uncommitted``. Pass ``include_full_diff=true``
        for the full unified diff inline when a human or non-model consumer
        explicitly opts in. The fingerprint and diffstat always cover the full
        arc-vs-master change set regardless of this flag.

        Pass ``diff_sha256`` and operator ``approval`` into ``git_land`` after review.

        Args:
            worktree_path: Absolute path to the arc worktree.
            path_filter: Optional pathspec limiting the inline diff body (display
                only, requires full diff; fingerprint and diffstat use the full
                arc-vs-master change set).
            include_full_diff: Include the full unified diff body inline
                (``full_diff_included=true`` in the response). Default false.

        Returns:
            Worker ``DiffResponse`` (``diff_sha256``, ``diffstat``, ``branch``,
            ``includes_uncommitted``, ``full_diff_included``, ``diff`` when
            requested, ``status``, …).
        """
        params: dict[str, Any] = {
            "worktree_path": worktree_path,
            "include_full_diff": include_full_diff,
        }
        if path_filter:
            params["path_filter"] = path_filter
        return await _relay(
            "GET",
            "/api/v1/git/diff",
            params=params,
            timeout=_SYNC_TIMEOUT,
        )

    @mcp.tool(title="Git Commit")
    async def git_commit(  # noqa: PLR0913 — mirrors CommitRequest fields
        worktree_path: str,
        expected_branch: str,
        paths: list[str],
        approval: str = "",
        expected_paths_sha256: str = "",
        commit_message: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Commit explicit named paths on the current branch (non-arc, gated).

        The path-explicit complement to ``git_land``: commits ONLY ``paths`` on
        the current branch of a non-arc checkout (typically the live master
        working tree). No ``--all`` — concurrent edits to unnamed files are
        never captured. No merge, CAS, or teardown.

        Two-step approval workflow (mirrors ``git_diff`` → ``git_land``):

        1. ``git_commit(worktree_path, expected_branch, paths, dry_run=True)``
           returns ``expected_paths_sha256`` (path-scoped fingerprint) + ``numstat``.
        2. After operator review, call again with ``approval``,
           ``expected_paths_sha256``, and ``commit_message`` to commit. A
           fingerprint mismatch (named paths changed since preview) rejects.

        Use ``git_land`` for arc→master integration; use this for committing
        reviewed changes that already live on a non-arc working tree.

        Args:
            worktree_path: Absolute path to the working tree (e.g. live master checkout).
            expected_branch: Branch the caller affirms HEAD is on (rejects on mismatch).
            paths: Explicit repo-relative paths to stage and commit (no --all).
            approval: Operator approval bound to expected_paths_sha256 (required unless dry_run).
            expected_paths_sha256: Path-scoped fingerprint from a prior dry_run (required unless dry_run).
            commit_message: Commit message (required unless dry_run).
            dry_run: Return fingerprint + numstat without committing (read-only).

        Returns:
            Worker envelope — ``status`` ∈ {preview, completed, rejected};
            preview carries ``expected_paths_sha256`` + ``numstat``; completion
            carries ``commit_sha`` + ``branch``.
        """
        return await _relay(
            "POST",
            "/api/v1/git/commit",
            json_body={
                "worktree_path": worktree_path,
                "expected_branch": expected_branch,
                "paths": paths,
                "approval": approval,
                "expected_paths_sha256": expected_paths_sha256,
                "commit_message": commit_message,
                "dry_run": dry_run,
            },
            timeout=_SYNC_TIMEOUT,
        )
