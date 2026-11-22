"""Event vocabulary for run budget and native helper policy."""

from __future__ import annotations

from universal_event_bus import Event, event_factory

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event


@event_factory
def SdkRunBudgetDeclared(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    tool_call_budget: int | None,
    warn_at: int | None,
) -> Event:
    return Event(
        signal="frontier.sdk.run.budget.declared",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "tool_call_budget": tool_call_budget,
            "warn_at": warn_at,
        },
        scope="node",
    )


@event_factory
def SdkRunBudgetWarned(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    tool_call_count: int,
    warn_at: int,
) -> Event:
    return Event(
        signal="frontier.sdk.run.budget.warned",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "tool_call_count": tool_call_count,
            "warn_at": warn_at,
        },
        scope="node",
    )


@event_factory
def SdkRunBudgetExhausted(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    tool_call_count: int,
    tool_call_budget: int,
) -> Event:
    return Event(
        signal="frontier.sdk.run.budget.exhausted",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "tool_call_count": tool_call_count,
            "tool_call_budget": tool_call_budget,
        },
        scope="node",
    )


@event_factory
def SdkNativeHelperDenied(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    kind: str,
) -> Event:
    return Event(
        signal="frontier.sdk.native.helper.denied",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "kind": kind,
        },
        scope="node",
    )


@event_factory
def SdkNativeHelperBreach(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    kind: str,
) -> Event:
    return Event(
        signal="frontier.sdk.native.helper.breach",
        payload={
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "kind": kind,
        },
        scope="node",
    )


def emit_run_budget_declared(
    *,
    dispatch_id: str,
    thread_id: str,
    tool_call_budget: int | None,
    warn_at: int | None,
) -> None:
    emit_frontier_event(
        SdkRunBudgetDeclared(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            tool_call_budget=tool_call_budget,
            warn_at=warn_at,
        )
    )


def emit_run_budget_warned(
    *,
    dispatch_id: str,
    thread_id: str,
    tool_call_count: int,
    warn_at: int,
) -> None:
    emit_frontier_event(
        SdkRunBudgetWarned(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            tool_call_count=tool_call_count,
            warn_at=warn_at,
        )
    )


def emit_run_budget_exhausted(
    *,
    dispatch_id: str,
    thread_id: str,
    tool_call_count: int,
    tool_call_budget: int,
) -> None:
    emit_frontier_event(
        SdkRunBudgetExhausted(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            tool_call_count=tool_call_count,
            tool_call_budget=tool_call_budget,
        )
    )


def emit_native_helper_denied(
    *,
    dispatch_id: str,
    thread_id: str,
    kind: str,
) -> None:
    emit_frontier_event(
        SdkNativeHelperDenied(dispatch_id=dispatch_id, thread_id=thread_id, kind=kind)
    )


def emit_native_helper_breach(
    *,
    dispatch_id: str,
    thread_id: str,
    kind: str,
) -> None:
    emit_frontier_event(
        SdkNativeHelperBreach(dispatch_id=dispatch_id, thread_id=thread_id, kind=kind)
    )


__all__ = [
    "SdkNativeHelperBreach",
    "SdkNativeHelperDenied",
    "SdkRunBudgetDeclared",
    "SdkRunBudgetExhausted",
    "SdkRunBudgetWarned",
    "emit_native_helper_breach",
    "emit_native_helper_denied",
    "emit_run_budget_declared",
    "emit_run_budget_exhausted",
    "emit_run_budget_warned",
]
