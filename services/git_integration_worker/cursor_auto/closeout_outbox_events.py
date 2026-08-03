"""Frontier events for cursor-auto closeout write-ahead and boot replay."""

from __future__ import annotations

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)


@event_factory
def FrontierSdkAutoCloseoutPersisted(  # noqa: N802
    dispatch_id: str,
    job_id: str,
    thread_id: str,
    envelope_sha256: str,
    closeout_status: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.closeout_persisted",
        payload={
            "dispatch_id": dispatch_id,
            "job_id": job_id,
            "thread_id": thread_id,
            "envelope_sha256": envelope_sha256,
            "closeout_status": closeout_status,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoCloseoutReplayed(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    envelope_sha256: str,
    stored_checkpoint: str | None,
    recomputed_checkpoint: str | None,
    stored_tree_residue: int | None,
    recomputed_tree_residue: int | None,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.closeout_replayed",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "envelope_sha256": envelope_sha256,
            "stored_checkpoint": stored_checkpoint,
            "recomputed_checkpoint": recomputed_checkpoint,
            "stored_tree_residue": stored_tree_residue,
            "recomputed_tree_residue": recomputed_tree_residue,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoCloseoutReplaySkipped(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    confirmed_by: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.closeout_replay_skipped",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "confirmed_by": confirmed_by,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoCloseoutReplayDiscarded(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    discarded_reason: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.closeout_replay_discarded",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "discarded_reason": discarded_reason,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoCloseoutReplayDeferred(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    attempts: int,
    reason: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.closeout_replay_deferred",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "attempts": attempts,
            "reason": reason,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoCloseoutReplayAbandoned(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    envelope_sha256: str,
    attempts: int,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.closeout_replay_abandoned",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "envelope_sha256": envelope_sha256,
            "attempts": attempts,
        },
        scope="node",
    )


@event_factory
def FrontierSdkAutoCloseoutReplaySuppressedLossReport(  # noqa: N802
    dispatch_id: str,
    job_id: str,
    thread_id: str,
) -> Event:
    return Event(
        signal="frontier.sdk.auto.closeout_replay_suppressed_loss_report",
        payload={
            "dispatch_id": dispatch_id,
            "job_id": job_id,
            "thread_id": thread_id,
        },
        scope="node",
    )


def _safe_emit(factory: Event, *, label: str) -> None:
    try:
        emit_frontier_event(factory)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cursor-auto %s emit failed: %s", label, exc)


def emit_closeout_persisted(**kwargs: str) -> None:
    _safe_emit(FrontierSdkAutoCloseoutPersisted(**kwargs), label="closeout_persisted")


def emit_closeout_replayed(**kwargs: str | int | None) -> None:
    _safe_emit(FrontierSdkAutoCloseoutReplayed(**kwargs), label="closeout_replayed")


def emit_closeout_replay_skipped(**kwargs: str) -> None:
    _safe_emit(FrontierSdkAutoCloseoutReplaySkipped(**kwargs), label="replay_skipped")


def emit_closeout_replay_discarded(**kwargs: str) -> None:
    _safe_emit(FrontierSdkAutoCloseoutReplayDiscarded(**kwargs), label="replay_discarded")


def emit_closeout_replay_deferred(**kwargs: str | int) -> None:
    _safe_emit(FrontierSdkAutoCloseoutReplayDeferred(**kwargs), label="replay_deferred")


def emit_closeout_replay_abandoned(**kwargs: str | int) -> None:
    _safe_emit(FrontierSdkAutoCloseoutReplayAbandoned(**kwargs), label="replay_abandoned")


def emit_closeout_replay_suppressed_loss_report(**kwargs: str) -> None:
    _safe_emit(
        FrontierSdkAutoCloseoutReplaySuppressedLossReport(**kwargs),
        label="replay_suppressed_loss_report",
    )
