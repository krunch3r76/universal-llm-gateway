"""prompt-expand pipeline bus event factories.

Signals: expand.admitted, expand.retrieve.completed, expand.author.completed,
expand.completed — emitted at step boundaries by prompt_expand domain handlers.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def ExpandAdmitted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    contract: str,
    stage: str,
    executor_tier: str,
    target: str,
    delivery: str,
) -> Event:
    """Emitted when validate_options admits a prompt-expand run."""
    return Event(
        signal="expand.admitted",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "contract": contract,
            "stage": stage,
            "executor_tier": executor_tier,
            "target": target,
            "delivery": delivery,
        },
    )


@event_factory
def ExpandRetrieveCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    rag_status: str,
    attempts: int,
    retrieve_scopes: list[str],
) -> Event:
    """Emitted after retrieve leg classification completes."""
    return Event(
        signal="expand.retrieve.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "rag_status": rag_status,
            "attempts": attempts,
            "retrieve_scopes": retrieve_scopes,
        },
    )


@event_factory
def ExpandAuthorCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    target: str,
) -> Event:
    """Emitted when target author branch completes."""
    return Event(
        signal="expand.author.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "target": target,
        },
    )


@event_factory
def ExpandCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    delivery: str,
    target: str,
    rag_status: str,
    provenance_mode: str,
) -> Event:
    """Emitted when format_output finishes the expand run."""
    return Event(
        signal="expand.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "delivery": delivery,
            "target": target,
            "rag_status": rag_status,
            "provenance_mode": provenance_mode,
        },
    )
