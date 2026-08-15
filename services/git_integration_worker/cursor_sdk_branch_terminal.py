"""Settle a lane branch at terminal — discharge it, or record who owes it.

This is where the ``land:lane_b_unlanded`` grade stops evaporating. A closeout
that declares ``land_disposition:`` gets the branch retired on the spot; one that
stays silent while the branch carries commits master lacks leaves an attributed
debt behind instead of an anonymous branch.

A refused ``landed`` claim also opens a debt: an assertion the tree does not
support is residue plus a false report, not a clean exit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_branch_debt import open_branch_debt
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    DISCHARGE_DISCARD,
    DISCHARGE_LANDED,
    discharge,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_lane_b_debt_opened,
)

logger = get_logger(__name__)

_DISPOSITION_RE = re.compile(
    r"^\s*land_disposition\s*:\s*[`\"']?([A-Za-z_-]+)[`\"']?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_REASON_RE = re.compile(
    r"^\s*land_reason\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class LaneBranchSettlement:
    """What terminal did with the lane branch."""

    outcome: str
    branch: str | None = None
    verb: str | None = None
    archive_tag: str | None = None
    detail: str | None = None


def parse_land_disposition(text: str | None) -> tuple[str | None, str | None]:
    """Extract ``land_disposition`` and ``land_reason`` from closeout prose."""
    if not text:
        return None, None
    match = _DISPOSITION_RE.search(text)
    if match is None:
        return None, None
    verb = match.group(1).strip().lower()
    reason_match = _REASON_RE.search(text)
    reason = reason_match.group(1).strip() if reason_match else None
    return verb, reason


def _caller_agent_for(dispatch_id: str) -> str | None:
    from services.git_integration_worker.cursor_dispatch_ledger import _connect

    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT caller_agent FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
    except Exception:  # attribution is best-effort, never fatal at terminal
        return None
    return row["caller_agent"] if row is not None else None


def _open_debt(
    *,
    branch_name: str,
    thread_id: str | None,
    dispatch_id: str,
    tip_sha: str | None,
    files: list[str] | None,
    detail: str,
) -> LaneBranchSettlement:
    caller_agent = _caller_agent_for(dispatch_id)
    open_branch_debt(
        branch_name=branch_name,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        caller_agent=caller_agent,
        tip_sha=tip_sha,
        files=files,
    )
    emit_sdk_lane_b_debt_opened(
        branch=branch_name,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        caller_agent=caller_agent,
        tip_sha=tip_sha,
    )
    logger.warning(
        "lane_b branch debt opened branch=%s thread_id=%s dispatch_id=%s reason=%s",
        branch_name,
        thread_id,
        dispatch_id,
        detail,
    )
    return LaneBranchSettlement(
        outcome="debt_opened",
        branch=branch_name,
        detail=detail,
    )


def settle_lane_branch(
    *,
    source_repo: Path,
    branch_name: str | None,
    thread_id: str | None,
    dispatch_id: str,
    closeout_text: str | None,
    commits_ahead: int | None,
    landed: bool | None,
    head_sha: str | None = None,
    files: list[str] | None = None,
) -> LaneBranchSettlement:
    """Discharge the lane branch on declaration, else record the debt.

    Never raises into the closeout path: a settle failure must not cost the
    caller its closeout, so anything unexpected degrades to a logged no-op.
    """
    if not branch_name:
        return LaneBranchSettlement(outcome="no_branch")
    try:
        return _settle(
            source_repo=source_repo,
            branch_name=branch_name,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            closeout_text=closeout_text,
            commits_ahead=commits_ahead,
            landed=landed,
            head_sha=head_sha,
            files=files,
        )
    except Exception as exc:
        logger.warning(
            "lane branch settle failed branch=%s dispatch_id=%s: %s",
            branch_name,
            dispatch_id,
            exc,
        )
        return LaneBranchSettlement(
            outcome="settle_error",
            branch=branch_name,
            detail=str(exc),
        )


def _settle(
    *,
    source_repo: Path,
    branch_name: str,
    thread_id: str | None,
    dispatch_id: str,
    closeout_text: str | None,
    commits_ahead: int | None,
    landed: bool | None,
    head_sha: str | None,
    files: list[str] | None,
) -> LaneBranchSettlement:
    verb, reason = parse_land_disposition(closeout_text)

    if verb in {DISCHARGE_LANDED, DISCHARGE_DISCARD}:
        result = discharge(
            repo=source_repo,
            branch_name=branch_name,
            verb=verb,
            reason=reason,
        )
        if result.discharged:
            return LaneBranchSettlement(
                outcome="discharged",
                branch=branch_name,
                verb=result.verb,
                archive_tag=result.archive_tag,
            )
        # A declaration the tree contradicts is residue, not a clean exit.
        return _open_debt(
            branch_name=branch_name,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            tip_sha=head_sha,
            files=files,
            detail=f"declared {verb} but refused: {result.refused_reason}",
        )

    if verb is not None:
        return _open_debt(
            branch_name=branch_name,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            tip_sha=head_sha,
            files=files,
            detail=f"unknown land_disposition {verb!r}",
        )

    if (commits_ahead or 0) >= 1 and landed is not True:
        return _open_debt(
            branch_name=branch_name,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            tip_sha=head_sha,
            files=files,
            detail="no land_disposition declared while branch carries commits",
        )

    return LaneBranchSettlement(outcome="nothing_owed", branch=branch_name)
