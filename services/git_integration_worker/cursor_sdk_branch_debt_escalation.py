"""Aged branch debt escalates with attribution — it is never swept.

Deleting aged residue on a timer destroys the evidence and lets the lane that
left it off the hook, which is the failure this whole path exists to correct.
So the backstop raises the debt's visibility instead: an attributed event, a
turn on the thread where the work actually lives, and — only at a hard horizon —
a refusal on that lane's next admit, quoting the call that clears it.

Reaping stays where it belongs: an explicit discharge, or provable residue.
"""

from __future__ import annotations

import os

from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_branch_debt import (
    BranchDebt,
    mark_debt_escalated,
    open_debts_for_thread,
)
from services.git_integration_worker.cursor_sdk_events import emit_sdk_lane_b_debt_aged

logger = get_logger(__name__)

_DEFAULT_ESCALATION_HORIZON_S = 86400.0
_DEFAULT_REFUSAL_HORIZON_S = 1209600.0
_BUS_FROM = "git-integration-worker"


def escalation_horizon_s() -> float:
    """Age at which an open debt is announced to its owning lane."""
    return float(
        os.environ.get(
            "CURSOR_SDK_BRANCH_DEBT_ESCALATION_HORIZON_S",
            _DEFAULT_ESCALATION_HORIZON_S,
        )
    )


def refusal_horizon_s() -> float:
    """Age at which an open debt blocks that lane's next Lane-B admit."""
    return float(
        os.environ.get(
            "CURSOR_SDK_BRANCH_DEBT_REFUSAL_HORIZON_S",
            _DEFAULT_REFUSAL_HORIZON_S,
        )
    )


def discharge_hint(branch: str) -> str:
    """The exact call that clears a debt, quoted wherever one is reported."""
    return (
        f"discharge it: POST /cursor-sdk/branch-discharge "
        f'{{"branch": "{branch}", "verb": "landed"}} — or '
        f'{{"branch": "{branch}", "verb": "discard", "reason": "<why>"}}'
    )


def _post_debt_turn(debt: BranchDebt, *, age_s: float, refusing: bool) -> bool:
    """Announce the debt on the thread that owns it; best-effort."""
    if not debt.thread_id or not debt.thread_id.isdigit():
        return False
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if not token:
        logger.warning("branch-debt bus post skipped: AGENT_BUS_TOKEN not configured")
        return False
    days = age_s / 86400.0
    headline = "BLOCKING" if refusing else "aged"
    body = "\n".join(
        [
            f"[branch-debt {headline}] `{debt.branch_name}`",
            "",
            f"**opened**: {debt.opened_at} ({days:.1f}d ago)",
            f"**dispatch**: {debt.dispatch_id or '(unknown)'}",
            f"**caller**: {debt.caller_agent or '(unknown)'}",
            f"**tip**: {debt.tip_sha or '(unknown)'}",
            "",
            (
                "This lane's next Lane-B admit is refused until the branch is "
                "discharged."
                if refusing
                else "The branch is still unlanded and undeclared."
            ),
            "",
            discharge_hint(debt.branch_name),
        ]
    )
    payload = {
        "thread": debt.thread_id,
        "from": _BUS_FROM,
        "to": debt.caller_agent or "all",
        "subject": f"branch-debt {headline}: {debt.branch_name}",
        "body": body,
    }
    try:
        with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = client.post(
                "/turns",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code not in (200, 201):
            logger.warning(
                "branch-debt bus post failed thread=%s status=%s",
                debt.thread_id,
                resp.status_code,
            )
            return False
    except Exception as exc:
        logger.warning("branch-debt bus post error thread=%s: %s", debt.thread_id, exc)
        return False
    return True


def escalate_aged_debts() -> int:
    """Announce every open debt past the escalation horizon, once each."""
    from services.git_integration_worker.cursor_sdk_branch_debt import list_open_debts

    horizon = escalation_horizon_s()
    hard = refusal_horizon_s()
    escalated = 0
    for debt in list_open_debts():
        if debt.escalated_at is not None:
            continue
        age_s = debt.age_s()
        if age_s is None or age_s < horizon:
            continue
        refusing = age_s >= hard
        emit_sdk_lane_b_debt_aged(
            branch=debt.branch_name,
            age_s=age_s,
            thread_id=debt.thread_id,
            dispatch_id=debt.dispatch_id,
            caller_agent=debt.caller_agent,
            refusing=refusing,
        )
        _post_debt_turn(debt, age_s=age_s, refusing=refusing)
        mark_debt_escalated(branch_name=debt.branch_name)
        escalated += 1
        logger.warning(
            "lane_b branch debt aged branch=%s thread_id=%s age_s=%.0f refusing=%s",
            debt.branch_name,
            debt.thread_id,
            age_s,
            refusing,
        )
    return escalated


def debt_admit_refusal(thread_id: str | None) -> str | None:
    """Refusal message when this lane holds a debt past the hard horizon.

    The last rung, not the mechanism: a lane is only stopped once it has been
    told, has had the discharge call in hand, and has left the debt anyway.
    """
    if not thread_id:
        return None
    hard = refusal_horizon_s()
    try:
        debts = open_debts_for_thread(thread_id)
    except Exception as exc:
        logger.warning(
            "branch-debt admit check unavailable thread=%s: %s", thread_id, exc
        )
        return None
    blocking = [debt for debt in debts if (debt.age_s() or 0.0) >= hard]
    if not blocking:
        return None
    oldest = max(blocking, key=lambda d: d.age_s() or 0.0)
    days = (oldest.age_s() or 0.0) / 86400.0
    branches = ", ".join(debt.branch_name for debt in blocking)
    return (
        f"lane {thread_id} holds {len(blocking)} undischarged branch debt(s) "
        f"past the {hard / 86400.0:.0f}d horizon ({branches}; oldest {days:.1f}d). "
        f"{discharge_hint(oldest.branch_name)}"
    )
