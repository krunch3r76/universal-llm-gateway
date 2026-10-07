"""Advisory ``jobs.run.*`` events emitted after a journal commit.

The jobs HTTP app and the runner call these factories. The journal is the
authority; Event Service delivery is a projection and is never replayed
into the fold. When ``JOBS_EVENT_SINK`` is set, each signal is appended as
one line so a parent process can read a child's restart reconcile.
"""

from __future__ import annotations

import os
from typing import Any

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory

_SINK: list[Event] = []


def published_events() -> list[Event]:
    """Return the in-process events emitted since process start.

    Tests spy on this list. Ordering matches journal commit order because
    ``emit_transition`` runs only after the append transaction commits.
    """
    return _SINK


def reset_published_events() -> None:
    """Drop the in-process spy buffer. Tests call this between cases."""
    _SINK.clear()


def publish(event: Event) -> None:
    """Record one advisory event locally and, when configured, on a sink file.

    The file sink is the cross-process channel for restart reconcile: the
    child writes ``jobs.run.lost`` and the parent reads the path. A missing
    sink is not an error. This function does not read the journal.
    """
    _SINK.append(event)
    path = os.environ.get("JOBS_EVENT_SINK", "")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(event.signal + "\n")


@event_factory
def jobs_run_admitted(
    *, run_id: str, job: str, surface: str, output_contract: str
) -> Event:
    """Observation that a run row and its admitted transition committed."""
    return Event(
        signal="jobs.run.admitted",
        role="observation",
        scope="node",
        payload={
            "run_id": run_id,
            "job": job,
            "surface": surface,
            "output_contract": output_contract,
        },
    )


@event_factory
def jobs_run_started(*, run_id: str, job: str, pid: int) -> Event:
    """Observation that the runner appended the running transition."""
    return Event(
        signal="jobs.run.started",
        role="observation",
        scope="node",
        payload={"run_id": run_id, "job": job, "pid": pid},
    )


@event_factory
def jobs_run_progress(*, run_id: str, progress_seq: int, nbytes: int) -> Event:
    """Sampled observation of stdout or stderr, at most one event per five seconds."""
    return Event(
        signal="jobs.run.progress",
        role="observation",
        scope="node",
        payload={
            "run_id": run_id,
            "progress_seq": progress_seq,
            "bytes": nbytes,
        },
    )


@event_factory
def jobs_run_cancelling(*, run_id: str) -> Event:
    """Observation that a running fold moved to cancelling."""
    return Event(
        signal="jobs.run.cancelling",
        role="observation",
        scope="node",
        payload={"run_id": run_id},
    )


@event_factory
def jobs_run_completed(
    *, run_id: str, job: str, exit_code: int, duration_ms: int
) -> Event:
    """Observation that a run reached completed with an exit code."""
    return Event(
        signal="jobs.run.completed",
        role="observation",
        scope="node",
        payload={
            "run_id": run_id,
            "job": job,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        },
    )


@event_factory
def jobs_run_failed(
    *, run_id: str, job: str, exit_code: int | None, error_code: str
) -> Event:
    """Observation that a run reached failed with an error code."""
    return Event(
        signal="jobs.run.failed",
        role="observation",
        scope="node",
        payload={
            "run_id": run_id,
            "job": job,
            "exit_code": exit_code,
            "error_code": error_code,
        },
    )


@event_factory
def jobs_run_cancelled(*, run_id: str, signal: str) -> Event:
    """Observation that a run reached cancelled, with the recorded signal name."""
    return Event(
        signal="jobs.run.cancelled",
        role="observation",
        scope="node",
        payload={"run_id": run_id, "signal": signal},
    )


@event_factory
def jobs_run_lost(*, run_id: str, recovery: str) -> Event:
    """Observation that restart reconcile appended lost for an open fold."""
    return Event(
        signal="jobs.run.lost",
        role="observation",
        scope="node",
        payload={"run_id": run_id, "recovery": recovery},
    )


@event_factory
def jobs_run_delivered(
    *, run_id: str, target_thread: str, turn_number: int
) -> Event:
    """Observation that a thread delivery row recorded a bus turn number."""
    return Event(
        signal="jobs.run.delivered",
        role="observation",
        scope="node",
        payload={
            "run_id": run_id,
            "target_thread": target_thread,
            "turn_number": turn_number,
        },
    )


@event_factory
def jobs_run_undelivered(
    *, run_id: str, target_thread: str, error: str
) -> Event:
    """Observation that a thread delivery attempt was journaled undelivered."""
    return Event(
        signal="jobs.run.undelivered",
        role="observation",
        scope="node",
        payload={
            "run_id": run_id,
            "target_thread": target_thread,
            "error": error,
        },
    )


@event_factory
def jobs_run_rejected(*, job: str, code: str, surface: str) -> Event:
    """Observation that create refused the request before a run row existed."""
    return Event(
        signal="jobs.run.rejected",
        role="observation",
        scope="node",
        payload={"job": job, "code": code, "surface": surface},
    )


def emit_transition(
    state: str,
    *,
    run_id: str,
    job: str,
    surface: str,
    output_contract: str,
    data: dict[str, Any],
    target_thread: str | None,
) -> None:
    """Publish the single event that matches one committed transition state.

    Called by the journal after commit. Delivery rows use the target thread
    from the run row. Unknown states are ignored so a progress side channel
    cannot mint a second signal for the same append.
    """
    if state == "admitted":
        publish(
            jobs_run_admitted(
                run_id=run_id,
                job=job,
                surface=surface,
                output_contract=output_contract,
            )
        )
        return
    if state == "running":
        publish(
            jobs_run_started(
                run_id=run_id, job=job, pid=int(data.get("pid") or 0)
            )
        )
        return
    if state == "cancelling":
        publish(jobs_run_cancelling(run_id=run_id))
        return
    if state == "completed":
        publish(
            jobs_run_completed(
                run_id=run_id,
                job=job,
                exit_code=int(data.get("exit_code") or 0),
                duration_ms=int(data.get("duration_ms") or 0),
            )
        )
        return
    if state == "failed":
        publish(
            jobs_run_failed(
                run_id=run_id,
                job=job,
                exit_code=data.get("exit_code"),
                error_code=str((data.get("error") or {}).get("code") or ""),
            )
        )
        return
    if state == "cancelled":
        publish(
            jobs_run_cancelled(
                run_id=run_id, signal=str(data.get("signal") or "none")
            )
        )
        return
    if state == "lost":
        publish(
            jobs_run_lost(
                run_id=run_id,
                recovery=str(data.get("recovery") or "restart_reconcile"),
            )
        )
        return
    if state == "delivered":
        publish(
            jobs_run_delivered(
                run_id=run_id,
                target_thread=str(target_thread or ""),
                turn_number=int(data.get("turn_number") or 0),
            )
        )
        return
    if state == "undelivered":
        err = data.get("error") or data.get("http_status") or ""
        publish(
            jobs_run_undelivered(
                run_id=run_id,
                target_thread=str(target_thread or ""),
                error=str(err),
            )
        )


def emit_progress(*, run_id: str, progress_seq: int, nbytes: int) -> None:
    """Publish one sampled progress event. The idle clock does not read it."""
    publish(
        jobs_run_progress(
            run_id=run_id, progress_seq=progress_seq, nbytes=nbytes
        )
    )


def emit_rejected(*, job: str, code: str, surface: str) -> None:
    """Publish a create refusal. No journal row is written for the refusal."""
    publish(jobs_run_rejected(job=job, code=code, surface=surface))
