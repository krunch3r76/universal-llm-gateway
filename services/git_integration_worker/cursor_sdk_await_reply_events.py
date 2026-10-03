"""``sdk.await_reply.*`` observation events (friction 34156).

A cursor-sdk dispatch that terminalizes with CDP generates still outstanding
is parked (``park_kind=await_cdp_reply``) and resumed by GIW once the replies
land. Every transition has exactly one emitter here so the wait is observable
without logs: ``parked`` → ``resume_admitted`` | ``expired``, plus
``resume_refused`` for an admit the route turned down (row stays open).
Separate from ``cursor_sdk_park_events`` (restart plane, at its SLOC ceiling).
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event


@event_factory
def SdkAwaitReplyParked(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    execution_ids: list[str],
    awaited_threads: list[str],
    ttl_s: int,
) -> Event:
    """Terminal reached with these CDP generates unanswered; lineage parked."""
    return Event(
        signal="sdk.await_reply.parked",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "execution_ids": execution_ids,
            "awaited_threads": awaited_threads,
            "ttl_s": ttl_s,
        },
        scope="node",
    )


@event_factory
def SdkAwaitReplyResumeAdmitted(  # noqa: N802
    parent_dispatch_id: str,
    child_dispatch_id: str,
    thread_id: str,
    outcomes: dict[str, str],
    code_version: str,
    attempt: int,
) -> Event:
    """GIW admitted the ``resume_of`` child carrying every awaited reply."""
    return Event(
        signal="sdk.await_reply.resume_admitted",
        payload={
            "parent_dispatch_id": parent_dispatch_id,
            "child_dispatch_id": child_dispatch_id,
            "thread_id": thread_id,
            "outcomes": outcomes,
            "code_version": code_version,
            "attempt": attempt,
        },
        scope="node",
    )


@event_factory
def SdkAwaitReplyResumeRefused(  # noqa: N802
    parent_dispatch_id: str,
    reason: str,
    attempt: int,
) -> Event:
    """Resume admission refused; the await row stays open for the next tick."""
    return Event(
        signal="sdk.await_reply.resume_refused",
        payload={
            "parent_dispatch_id": parent_dispatch_id,
            "reason": reason,
            "attempt": attempt,
        },
        scope="node",
    )


@event_factory
def SdkAwaitReplyExpired(  # noqa: N802
    parent_dispatch_id: str,
    reason: str,
    parked_at: str | None,
) -> Event:
    """No child can be admitted (agent gone); link closed, awareness posted."""
    payload: dict[str, Any] = {
        "parent_dispatch_id": parent_dispatch_id,
        "reason": reason,
    }
    if parked_at is not None:
        payload["parked_at"] = parked_at
    return Event(signal="sdk.await_reply.expired", payload=payload, scope="node")


def emit_sdk_await_reply_parked(
    *,
    dispatch_id: str,
    thread_id: str,
    execution_ids: list[str],
    awaited_threads: list[str],
    ttl_s: int,
) -> None:
    emit_frontier_event(
        SdkAwaitReplyParked(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            execution_ids=execution_ids,
            awaited_threads=awaited_threads,
            ttl_s=ttl_s,
        )
    )


def emit_sdk_await_reply_resume_admitted(
    *,
    parent_dispatch_id: str,
    child_dispatch_id: str,
    thread_id: str,
    outcomes: dict[str, str],
    code_version: str,
    attempt: int,
) -> None:
    emit_frontier_event(
        SdkAwaitReplyResumeAdmitted(
            parent_dispatch_id=parent_dispatch_id,
            child_dispatch_id=child_dispatch_id,
            thread_id=thread_id,
            outcomes=outcomes,
            code_version=code_version,
            attempt=attempt,
        )
    )


def emit_sdk_await_reply_resume_refused(
    *, parent_dispatch_id: str, reason: str, attempt: int
) -> None:
    emit_frontier_event(
        SdkAwaitReplyResumeRefused(
            parent_dispatch_id=parent_dispatch_id, reason=reason, attempt=attempt
        )
    )


def emit_sdk_await_reply_expired(
    *, parent_dispatch_id: str, reason: str, parked_at: str | None
) -> None:
    emit_frontier_event(
        SdkAwaitReplyExpired(
            parent_dispatch_id=parent_dispatch_id, reason=reason, parked_at=parked_at
        )
    )


__all__ = [
    "SdkAwaitReplyExpired",
    "SdkAwaitReplyParked",
    "SdkAwaitReplyResumeAdmitted",
    "SdkAwaitReplyResumeRefused",
    "emit_sdk_await_reply_expired",
    "emit_sdk_await_reply_parked",
    "emit_sdk_await_reply_resume_admitted",
    "emit_sdk_await_reply_resume_refused",
]
