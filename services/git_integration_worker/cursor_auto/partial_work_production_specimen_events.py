"""One-shot wake when relay first writes envelope ``status: partial:work``.

Arms ``todo:closeout-plane-legibility`` closure criterion (a:28824): a
production ``partial:work`` envelope must become observable without grepping
bus prose. No mechanical natural-vs-constructed classifier exists at this
edge — emit unconditionally with whatever context the relay path carries.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)

SIGNAL = "frontier.sdk.closeout.partial_work.production_specimen"
CODE_REF = (
    "services.git_integration_worker.cursor_auto.nested_sdk"
    ":post_operator_closeout"
)
SCHEMA_VERSION = 1
NATURAL_SPECIMEN_CLASSIFICATION = "unavailable"


@event_factory
def FrontierSdkCloseoutPartialWorkProductionSpecimen(  # noqa: N802
    dispatch_id: str,
    envelope_turn: int,
    thread_id: str,
    closeout_source: str | None,
    contract: str | None,
    replay_mode: bool,
    natural_specimen_classification: str,
    code_ref: str,
    schema_version: int,
) -> Event:
    """First envelope write with ``status: partial:work`` on the relay path."""
    return Event(
        signal=SIGNAL,
        payload={
            "dispatch_id": dispatch_id,
            "envelope_turn": envelope_turn,
            "thread_id": thread_id,
            "closeout_source": closeout_source,
            "contract": contract,
            "replay_mode": replay_mode,
            "natural_specimen_classification": natural_specimen_classification,
            "code_ref": code_ref,
            "schema_version": schema_version,
        },
        scope="node",
        role="observation",
    )


def emit_partial_work_production_specimen(
    *,
    dispatch_id: str,
    envelope_turn: int,
    thread_id: str,
    closeout_source: str | None = None,
    contract: str | None = None,
    replay_mode: bool = False,
) -> None:
    """Record first production ``partial:work`` envelope write on the relay path.

    Failure-isolated: logs and returns without raising into closeout assembly.
    """
    try:
        emit_frontier_event(
            FrontierSdkCloseoutPartialWorkProductionSpecimen(
                dispatch_id=dispatch_id,
                envelope_turn=envelope_turn,
                thread_id=thread_id,
                closeout_source=closeout_source,
                contract=contract,
                replay_mode=replay_mode,
                natural_specimen_classification=NATURAL_SPECIMEN_CLASSIFICATION,
                code_ref=CODE_REF,
                schema_version=SCHEMA_VERSION,
            )
        )
    except Exception as exc:  # noqa: BLE001 — observation must not raise into relay
        logger.warning(
            "partial_work production_specimen emit failed: dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )


__all__ = [
    "CODE_REF",
    "NATURAL_SPECIMEN_CLASSIFICATION",
    "SCHEMA_VERSION",
    "SIGNAL",
    "emit_partial_work_production_specimen",
]
