"""Pipeline admission control events (observation role)."""

from universal_event_bus import Event, event_factory


@event_factory
def PipelineAdmissionQueued(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    queue_depth: int,
    active_count: int,
) -> Event:
    """Emitted when a pipeline request enters the admission wait queue."""
    return Event(
        signal="pipeline.admission.queued",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "queue_depth": queue_depth,
            "active_count": active_count,
        },
    )


@event_factory
def PipelineAdmissionAdmitted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    queue_depth: int,
    active_count: int,
    wait_ms: float,
) -> Event:
    """Emitted when a pipeline acquires an admission token and begins execution."""
    return Event(
        signal="pipeline.admission.admitted",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "queue_depth": queue_depth,
            "active_count": active_count,
            "wait_ms": wait_ms,
        },
    )


@event_factory
def PipelineAdmissionRejected(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    queue_depth: int,
    active_count: int,
    wait_ms: float,
) -> Event:
    """Emitted when a pipeline is rejected due to admission timeout."""
    return Event(
        signal="pipeline.admission.rejected",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "queue_depth": queue_depth,
            "active_count": active_count,
            "wait_ms": wait_ms,
        },
    )


@event_factory
def PipelineAdmissionReleased(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    queue_depth: int,
    active_count: int,
    wait_ms: float,
) -> Event:
    """Emitted when a pipeline releases its admission token."""
    return Event(
        signal="pipeline.admission.released",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "queue_depth": queue_depth,
            "active_count": active_count,
            "wait_ms": wait_ms,
        },
    )
