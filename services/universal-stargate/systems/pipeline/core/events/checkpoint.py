"""Checkpoint operation events."""

from universal_event_bus import Event, event_factory


@event_factory
def CheckpointSaved(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    checkpoint_key: str,
    storage_backend: str,
) -> Event:
    """
    Emitted after checkpoint successfully saved.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step that was checkpointed
        checkpoint_key: Storage key for retrieval
        storage_backend: Backend type (e.g., "filesystem")
    """
    return Event(
        signal="pipeline.checkpoint.saved",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "checkpoint_key": checkpoint_key,
            "storage_backend": storage_backend,
        },
    )


@event_factory
def CheckpointLoaded(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    checkpoint_key: str,
    storage_backend: str,
    saved_at: str,  # ISO timestamp
) -> Event:
    """
    Emitted when step resumed from checkpoint.

    Payload:
        pipeline_id: Pipeline identifier
        execution_id: Current execution UUID
        step_name: Step that was resumed
        checkpoint_key: Storage key used
        storage_backend: Backend type
        saved_at: When checkpoint was originally saved
    """
    return Event(
        signal="pipeline.checkpoint.loaded",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "checkpoint_key": checkpoint_key,
            "storage_backend": storage_backend,
            "saved_at": saved_at,
        },
    )


@event_factory
def CheckpointFailed(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    operation: str,  # "save" or "load"
    error: str,
) -> Event:
    """
    Emitted when checkpoint operation fails.

    Note: Checkpoint failures are non-fatal (logged, execution continues).
    """
    return Event(
        signal="pipeline.checkpoint.failed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "operation": operation,
            "error": error,
        },
    )
