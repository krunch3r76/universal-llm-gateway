"""Eligible-denominator tally for closeout authorship-decision outcomes.

Every arm of ``compute_closeout_tree_state``'s authorship decision — including
checkpoint-committed and nothing_authored gate skips, plus every
``compose_deployment_authorship`` branch (omit included) — emits one
observation so vacancy rate is fires/eligible without grepping closeout prose.
Process-frequency fact; Event Service is the store.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)

SIGNAL = "frontier.sdk.closeout.authorship_outcome"
CODE_REF = (
    "services.git_integration_worker.cursor_auto.closeout_tree_state"
    ":compose_deployment_authorship"
)
CODE_REF_COMPUTE = (
    "services.git_integration_worker.cursor_auto.closeout_tree_state"
    ":compute_closeout_tree_state"
)
SCHEMA_VERSION = 1

OUTCOME_ATTRIBUTION_UNAVAILABLE = "attribution_unavailable"
OUTCOME_VACANCY = "vacancy"
OUTCOME_OMIT = "omit"
OUTCOME_AUTHORED_NOT_COMMITTED = "authored_not_committed"
# Gate arms in compute_closeout_tree_state that never reach compose.
OUTCOME_CHECKPOINT_COMMITTED = "checkpoint_committed"
OUTCOME_NOTHING_AUTHORED = "nothing_authored"


@event_factory
def FrontierSdkCloseoutAuthorshipOutcome(  # noqa: N802
    dispatch_id: str,
    outcome: str,
    baseline_present: bool,
    vacancy_eligible: bool,
    vacancy_fired: bool,
    ledger_registration_available: bool,
    authored_count: int,
    code_ref: str,
    schema_version: int,
) -> Event:
    """One authorship-decision arm, including gate-skips and non-firing omit."""
    return Event(
        signal=SIGNAL,
        payload={
            "dispatch_id": dispatch_id,
            "outcome": outcome,
            "baseline_present": baseline_present,
            "vacancy_eligible": vacancy_eligible,
            "vacancy_fired": vacancy_fired,
            "ledger_registration_available": ledger_registration_available,
            "authored_count": authored_count,
            "code_ref": code_ref,
            "schema_version": schema_version,
        },
        scope="node",
        role="observation",
    )


def emit_authorship_outcome(
    *,
    dispatch_id: str,
    outcome: str,
    baseline_present: bool,
    ledger_registration_available: bool,
    authored_count: int,
    code_ref: str = CODE_REF,
) -> None:
    """Record authorship-decision arm at decision time. Never raises into closeout."""
    try:
        emit_frontier_event(
            FrontierSdkCloseoutAuthorshipOutcome(
                dispatch_id=dispatch_id,
                outcome=outcome,
                baseline_present=baseline_present,
                vacancy_eligible=baseline_present,
                vacancy_fired=outcome == OUTCOME_VACANCY,
                ledger_registration_available=ledger_registration_available,
                authored_count=authored_count,
                code_ref=code_ref,
                schema_version=SCHEMA_VERSION,
            )
        )
    except Exception as exc:  # noqa: BLE001 — observation must not raise into relay
        logger.warning(
            "authorship_outcome event emit failed: dispatch_id=%s outcome=%s err=%s",
            dispatch_id,
            outcome,
            exc,
        )


__all__ = [
    "CODE_REF",
    "CODE_REF_COMPUTE",
    "OUTCOME_ATTRIBUTION_UNAVAILABLE",
    "OUTCOME_AUTHORED_NOT_COMMITTED",
    "OUTCOME_CHECKPOINT_COMMITTED",
    "OUTCOME_NOTHING_AUTHORED",
    "OUTCOME_OMIT",
    "OUTCOME_VACANCY",
    "SCHEMA_VERSION",
    "SIGNAL",
    "emit_authorship_outcome",
]
