"""RAG admission gate event factories."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_admission_first_burst_observed(
    *,
    model_id: str,
    workers_in_flight: int,
    stargate_queue_depth: int | None,
) -> Event:
    """First-time OPEN→CLOSED transition via model.loading.started.

    Emitted exactly once per model per AdmissionGate lifetime, the first time
    the gate closes because a cold-load window opened (signal=model.loading.started).
    Quantifies the first-batch burst — how many contextualize workers submitted
    requests to Stargate before model.loading.started arrived and closed the gate.

    workers_in_flight: count of wait_for_admission() calls that returned True
        (or proceeded on timeout) since the gate was last OPEN, or since startup
        if this is the first close. This is the burst bound N from the phase4
        Worst-Case Cold-Load Timing analysis.
    stargate_queue_depth: queue_depth from GET /api/v1/admission/state at
        transition time; None if Stargate was unreachable. If P95 of this
        value over production runs approaches max_queue_depth, escalate
        todo:rag-admission-gate-startup-snapshot to high priority.
    """
    return Event(
        signal="rag.admission.first.burst.observed",
        payload={
            "model_id": model_id,
            "workers_in_flight": workers_in_flight,
            "stargate_queue_depth": stargate_queue_depth,
        },
    )


@event_factory
def rag_admission_io_failed(
    *,
    operation: str,
    model_id: str,
    error: str,
) -> Event:
    """Emitted when an admission gate HTTP I/O operation fails.

    operation: short label for the failed call — "snapshot" or "burst_fetch".
    model_id: routing_key of the model being queried.
    error: str(exc) from the caught exception.
    """
    return Event(
        signal="rag.admission.io.failed",
        payload={
            "operation": operation,
            "model_id": model_id,
            "error": error,
        },
    )


__all__ = ["rag_admission_first_burst_observed", "rag_admission_io_failed"]
