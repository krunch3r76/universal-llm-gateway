"""Thread-persistence compaction event factories.

Signals for the cortex-chat-openai compactor (plan:api-agent-thread-persistence
Phase B/E). Distinct from cortex-store §6.10 compaction-pointer read semantics.

Signals:
- ``pipeline.compaction.archived`` — turn artifact + assertion landed
- ``pipeline.compaction.assembled`` — referential window built for this request
- ``pipeline.compaction.summarized`` — chat summarization collapsed older turns
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def PipelineCompactionArchived(  # noqa: N802
    execution_id: str,
    chat_id: str,
    anchor_id: str,
    turn_index: int,
    role: str,
    artifact_uri: str,
    assertion_id: int | str | None = None,
    tool_calls_count: int = 0,
    synthesized: bool = False,
) -> Event:
    """Emitted when ``archive_*_turn_v1`` persists a turn to cortex."""
    return Event(
        signal="pipeline.compaction.archived",
        payload={
            "execution_id": execution_id,
            "chat_id": chat_id,
            "anchor_id": anchor_id,
            "turn_index": turn_index,
            "role": role,
            "artifact_uri": artifact_uri,
            "assertion_id": assertion_id,
            "tool_calls_count": tool_calls_count,
            "synthesized": synthesized,
        },
        scope="node",
    )


@event_factory
def PipelineCompactionAssembled(  # noqa: N802
    execution_id: str,
    chat_id: str,
    anchor_id: str,
    turn_index: int,
    window_size: int,
    messages_count: int,
    total_turn_pairs: int,
) -> Event:
    """Emitted when ``assemble_thread_v1`` builds the referential prefix."""
    return Event(
        signal="pipeline.compaction.assembled",
        payload={
            "execution_id": execution_id,
            "chat_id": chat_id,
            "anchor_id": anchor_id,
            "turn_index": turn_index,
            "window_size": window_size,
            "messages_count": messages_count,
            "total_turn_pairs": total_turn_pairs,
        },
        scope="node",
    )


@event_factory
def PipelineCompactionSummarized(  # noqa: N802
    execution_id: str,
    chat_id: str,
    anchor_id: str,
    turns_summarized: int,
    summary_assertion_id: int | str | None = None,
) -> Event:
    """Emitted when chat summarization collapses older turns (Phase C)."""
    return Event(
        signal="pipeline.compaction.summarized",
        payload={
            "execution_id": execution_id,
            "chat_id": chat_id,
            "anchor_id": anchor_id,
            "turns_summarized": turns_summarized,
            "summary_assertion_id": summary_assertion_id,
        },
        scope="node",
    )
