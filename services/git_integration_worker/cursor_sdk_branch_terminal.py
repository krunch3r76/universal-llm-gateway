"""Settle a lane branch at terminal — discharge it, or record who owes it.

This is where the ``land:lane_b_unlanded`` grade stops evaporating. A closeout
that declares ``land_disposition:`` gets the branch retired on the spot; one that
stays silent while the branch carries commits master lacks leaves an attributed
debt behind instead of an anonymous branch. Hub land at that silence is opt-in:
only a packet line ``land: silent`` (see ``packet_requests_silent_land``)
fast-forwards or clean-merges.

A refused ``landed`` claim also opens a debt: an assertion the tree does not
support is residue plus a false report, not a clean exit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_branch_debt import (
    open_branch_debt,
    workspace_token_for_repo,
)
from services.git_integration_worker.cursor_sdk_branch_debt_tags import (
    add_land_required_tag,
    remove_land_required_tag,
)
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    DISCHARGE_DISCARD,
    DISCHARGE_LANDED,
    DISCHARGE_UNLANDED,
    discharge,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_lane_b_debt_opened,
)

logger = get_logger(__name__)

_UNLANDED_DISPOSITION_RE = re.compile(
    r"^\s*land_disposition\s*:\s*[`\"']?unlanded[`\"']?\s+([0-9a-f]{7,40})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_DISPOSITION_RE = re.compile(
    r"^\s*land_disposition\s*:\s*[`\"']?([A-Za-z_-]+)[`\"']?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_DISPOSITION_VALUE_RE = re.compile(
    r"^\s*land_disposition\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_REASON_RE = re.compile(
    r"^\s*land_reason\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Line-start opt-in. ``land: silent`` alone on the line (leading whitespace
# allowed). A mention mid-sentence or with trailing words does not opt in.
_SILENT_LAND_OPT_IN_RE = re.compile(r"(?im)^[ \t]*land:[ \t]+silent[ \t]*$")


@dataclass(frozen=True, slots=True)
class LaneBranchSettlement:
    """What terminal did with the lane branch."""

    outcome: str
    branch: str | None = None
    verb: str | None = None
    archive_tag: str | None = None
    detail: str | None = None


def _land_disposition_verb_from_value(value: str) -> str | None:
    """Map a relay-qualified value back to the settlement verb (compute unchanged)."""
    text = value.strip().strip("`\"'")
    if text.lower().startswith("unlanded"):
        return "unlanded"
    lead = text.split("·", 1)[0].strip().lower()
    if lead in {"landed", "landed@local-master"}:
        return "landed"
    if lead.startswith("tip@lane-b"):
        return "landed"
    if lead == "discard":
        return "discard"
    return None


def parse_land_disposition(
    text: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Extract ``land_disposition``, ``land_reason``, and optional unlanded tip sha."""
    if not text:
        return None, None, None
    unlanded_match = _UNLANDED_DISPOSITION_RE.search(text)
    if unlanded_match is not None:
        reason_match = _REASON_RE.search(text)
        reason = reason_match.group(1).strip() if reason_match else None
        return "unlanded", reason, unlanded_match.group(1).strip().lower()
    value_match = _DISPOSITION_VALUE_RE.search(text)
    if value_match is not None:
        raw_value = value_match.group(1).strip()
        if raw_value.lower().startswith("unlanded"):
            parts = raw_value.split()
            sha = parts[1].strip().lower() if len(parts) > 1 else None
            reason_match = _REASON_RE.search(text)
            reason = reason_match.group(1).strip() if reason_match else None
            return "unlanded", reason, sha
        verb = _land_disposition_verb_from_value(raw_value)
        if verb is not None:
            reason_match = _REASON_RE.search(text)
            reason = reason_match.group(1).strip() if reason_match else None
            return verb, reason, None
    match = _DISPOSITION_RE.search(text)
    if match is None:
        return None, None, None
    verb = match.group(1).strip().lower()
    reason_match = _REASON_RE.search(text)
    reason = reason_match.group(1).strip() if reason_match else None
    return verb, reason, None


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
    source_repo: Path,
    branch_name: str,
    thread_id: str | None,
    dispatch_id: str,
    tip_sha: str | None,
    files: list[str] | None,
    detail: str,
) -> LaneBranchSettlement:
    from services.git_integration_worker.config import load_config

    cfg = load_config()
    workspace_token = workspace_token_for_repo(
        source_repo,
        hub=cfg.source_repo,
        projects_root=cfg.dispatch_workspace,
    )
    caller_agent = _caller_agent_for(dispatch_id)
    open_branch_debt(
        branch_name=branch_name,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        caller_agent=caller_agent,
        tip_sha=tip_sha,
        files=files,
        source_repo=workspace_token,
    )
    add_land_required_tag(thread_id=thread_id)
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


def packet_requests_silent_land(packet_text: str | None) -> bool:
    """True when the packet opts into closeout hub land.

    Opt-in token, line-start, off by default: ``land: silent``.
    Leading whitespace is allowed; the rest of the line must be empty.
    ``do not hub-land``, a declared ``land_disposition``, and a read-only
    admit still refuse the land even when this line is present.
    """
    if not packet_text:
        return False
    return _SILENT_LAND_OPT_IN_RE.search(packet_text) is not None


def maybe_ff_land_silent_lane(
    *,
    repo: Path,
    branch_name: str,
    dispatch_id: str,
    packet_text: str | None,
    closeout_text: str | None,
    commits_ahead: int | None,
) -> bool:
    """Land a silent in-scope lane onto hub master, only when opted in.

    The packet must contain a line-start ``land: silent`` (see
    ``packet_requests_silent_land``). Without that token the branch is left
    for a guarded land. Fast-forward when master has not moved. When a peer
    commit landed first and git can merge with no conflict, merge.
    Exploratory packets (``do not hub-land``), any declared disposition, a
    read-only admit, a branch with nothing ahead, and a textual conflict
    are left untouched even when the token is present.
    Returns True only when hub master now contains the branch tip.
    Side effects: may fast-forward or merge the hub master worktree.
    """
    if (commits_ahead or 0) < 1:
        return False
    if _dispatch_read_only(dispatch_id):
        return False
    from services.git_integration_worker.cursor_sdk_hub_land_scope import (
        clean_merge_onto_hub_master,
        ff_only_onto_hub_master,
        packet_hub_land_scoped_out,
    )

    if packet_hub_land_scoped_out(packet_text):
        return False
    verb, _reason, _sha = parse_land_disposition(closeout_text)
    if verb is not None:
        return False
    if not packet_requests_silent_land(packet_text):
        return False
    from services.git_integration_worker.cursor_sdk_hub_land_scope import (
        resolve_hub_git_repo,
    )
    from services.git_integration_worker.cursor_sdk_land_lease import (
        master_land_lease_key,
        release_land_lease_best_effort,
        try_acquire_land_lease,
    )

    hub = resolve_hub_git_repo(repo)
    lease_key = master_land_lease_key(hub)
    holder_op_id = f"silent-land:{dispatch_id}"
    try:
        acquired = try_acquire_land_lease(
            lease_key=lease_key, holder_op_id=holder_op_id
        )
    except Exception:
        logger.error(
            "silent land left unlanded; master land lease acquire failed "
            "lease_key=%s branch=%s dispatch_id=%s",
            lease_key,
            branch_name,
            dispatch_id,
        )
        return False
    if not acquired:
        logger.warning(
            "silent land left unlanded; master land lease held "
            "lease_key=%s branch=%s dispatch_id=%s",
            lease_key,
            branch_name,
            dispatch_id,
        )
        return False
    try:
        if ff_only_onto_hub_master(repo, branch_name=branch_name):
            return True
        return clean_merge_onto_hub_master(repo, branch_name=branch_name)
    finally:
        release_land_lease_best_effort(
            lease_key=lease_key, holder_op_id=holder_op_id
        )


def _dispatch_read_only(dispatch_id: str) -> bool:
    """True when this admit is read-only, or when the flag cannot be read."""
    try:
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        return CursorDispatchLedger.instance().read_read_only(dispatch_id=dispatch_id)
    except Exception:
        logger.warning(
            "lane_b ff-land skipped; read_only unreadable dispatch_id=%s",
            dispatch_id,
        )
        return True


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
    packet_text: str | None = None,
) -> LaneBranchSettlement:
    """Discharge the lane branch on declaration, else record the debt.

    A branch still owned by an open conductor mission is neither discharged
    nor charged: the mission's next hop, nested limb, or crash-resume runs on
    it, so settlement waits for the closeout that carries ``DONE``
    (``conductor_lane_retention``). That outcome is ``retained_for_mission``.
    Only the conductor's own lane branch is retained; a nested limb's branch
    settles.

    Never raises into the closeout path: the retention read and the settle
    share one try. A ledger read that fails inside the predicate returns None
    and settlement proceeds; anything else unexpected degrades to a logged
    no-op.
    """
    if not branch_name:
        return LaneBranchSettlement(outcome="no_branch")
    try:
        from services.git_integration_worker.cursor_sdk_closeout.conductor_lane_retention import (
            RETAINED_FOR_MISSION,
            lane_retention_reason,
        )

        retained = lane_retention_reason(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            closeout_text=closeout_text,
            branch_name=branch_name,
        )
        if retained:
            logger.info(
                "lane_b branch retained for open conductor mission branch=%s "
                "dispatch_id=%s reason=%s",
                branch_name,
                dispatch_id,
                retained,
            )
            return LaneBranchSettlement(
                outcome=RETAINED_FOR_MISSION,
                branch=branch_name,
                detail=retained,
            )
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
            packet_text=packet_text,
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
    packet_text: str | None,
) -> LaneBranchSettlement:
    verb, reason, unlanded_sha = parse_land_disposition(closeout_text)

    if verb in {DISCHARGE_LANDED, DISCHARGE_DISCARD, DISCHARGE_UNLANDED}:
        result = discharge(
            repo=source_repo,
            branch_name=branch_name,
            verb=verb,
            reason=reason,
            declared_tip_sha=unlanded_sha,
            completing_dispatch_id=dispatch_id,
        )
        if result.inherited:
            return LaneBranchSettlement(
                outcome="inherited",
                branch=branch_name,
                verb=result.verb,
                detail=result.refused_reason,
            )
        if result.discharged:
            remove_land_required_tag(thread_id=thread_id)
            return LaneBranchSettlement(
                outcome="discharged",
                branch=branch_name,
                verb=result.verb,
                archive_tag=result.archive_tag,
            )
        # A declaration the tree contradicts is residue, not a clean exit.
        return _open_debt(
            source_repo=source_repo,
            branch_name=branch_name,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            tip_sha=head_sha,
            files=files,
            detail=f"declared {verb} but refused: {result.refused_reason}",
        )

    if verb is not None:
        return _open_debt(
            source_repo=source_repo,
            branch_name=branch_name,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            tip_sha=head_sha,
            files=files,
            detail=f"unknown land_disposition {verb!r}",
        )

    if (commits_ahead or 0) >= 1 and landed is not True:
        if maybe_ff_land_silent_lane(
            repo=source_repo,
            branch_name=branch_name,
            dispatch_id=dispatch_id,
            packet_text=packet_text,
            closeout_text=closeout_text,
            commits_ahead=commits_ahead,
        ):
            result = discharge(
                repo=source_repo,
                branch_name=branch_name,
                verb=DISCHARGE_LANDED,
                completing_dispatch_id=dispatch_id,
            )
            if result.inherited:
                return LaneBranchSettlement(
                    outcome="inherited",
                    branch=branch_name,
                    verb=result.verb,
                    detail=result.refused_reason,
                )
            if result.discharged:
                remove_land_required_tag(thread_id=thread_id)
                return LaneBranchSettlement(
                    outcome="discharged",
                    branch=branch_name,
                    verb=result.verb,
                    archive_tag=result.archive_tag,
                )
            # Bytes are on master. A checked-out lane ref is not a strand.
            return LaneBranchSettlement(
                outcome="ff_landed",
                branch=branch_name,
                verb=DISCHARGE_LANDED,
                detail=result.refused_reason,
            )
        return _open_debt(
            source_repo=source_repo,
            branch_name=branch_name,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            tip_sha=head_sha,
            files=files,
            detail="no land_disposition declared while branch carries commits",
        )

    return LaneBranchSettlement(outcome="nothing_owed", branch=branch_name)
