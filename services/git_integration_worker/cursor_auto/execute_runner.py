"""In-seat single-op runner for ``contract: execute`` (Fable Option A).

A single manifest-approved tier-M op does not justify a nested SDK dispatch: the
cost and blast radius are both wrong-sized for one tool call. Auto fires it in
seat and relays the raw payload.

Auto has no tool surface of its own, so the actual invocation is injected:
whoever owns a tool surface registers a :class:`ToolOpInvoker` at process start.
There is deliberately **no default invoker** — an unconfigured process refuses
the job (``execute_invoker_unconfigured``) rather than pretending. Invariant 1:
never claim executed without an observed tool payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from services.git_integration_worker.cursor_auto.tier_m_manifest import ManifestRow

INVOKER_UNCONFIGURED_REASON = "execute_invoker_unconfigured"


class InvokerUnconfiguredError(Exception):
    """An allowlisted op is not wired on this worker seat."""


class ToolOpInvoker(Protocol):
    """Fires one tier-M tool op and returns its raw payload."""

    async def __call__(
        self,
        *,
        tool: str,
        op: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]: ...


_invoker: ToolOpInvoker | None = None


def set_tool_op_invoker(invoker: ToolOpInvoker) -> None:
    """Install the process-wide tier-M op invoker."""
    global _invoker
    _invoker = invoker


def clear_tool_op_invoker() -> None:
    """Remove the installed invoker (process teardown / test isolation)."""
    global _invoker
    _invoker = None


def get_tool_op_invoker() -> ToolOpInvoker | None:
    """Return the installed invoker, or ``None`` when this process has none."""
    return _invoker


@dataclass(frozen=True, slots=True)
class ExecuteOutcome:
    """Result of one in-seat op: an observed payload, or a named refusal."""

    ok: bool
    tool_op: str
    payload: dict[str, Any] | None = None
    reason: str | None = None
    error: str | None = None


async def run_tool_op(
    row: ManifestRow,
    arguments: dict[str, Any],
) -> ExecuteOutcome:
    """Fire *row*'s op through the installed invoker and capture the payload."""
    invoker = get_tool_op_invoker()
    if invoker is None:
        return ExecuteOutcome(
            ok=False,
            tool_op=row.tool_op,
            reason=INVOKER_UNCONFIGURED_REASON,
            error=(
                "this worker process has no tier-M tool-op invoker registered; "
                "the op was not fired"
            ),
        )
    try:
        payload = await invoker(tool=row.tool, op=row.op, arguments=arguments)
    except InvokerUnconfiguredError as exc:
        return ExecuteOutcome(
            ok=False,
            tool_op=row.tool_op,
            reason=INVOKER_UNCONFIGURED_REASON,
            error=str(exc),
        )
    except Exception as exc:
        return ExecuteOutcome(
            ok=False,
            tool_op=row.tool_op,
            reason="execute_invoker_raised",
            error=f"{type(exc).__name__}: {exc}",
        )
    if not isinstance(payload, dict):
        return ExecuteOutcome(
            ok=False,
            tool_op=row.tool_op,
            reason="execute_payload_not_object",
            error=f"invoker returned {type(payload).__name__}, expected an object",
        )
    return ExecuteOutcome(ok=True, tool_op=row.tool_op, payload=payload)


__all__ = [
    "INVOKER_UNCONFIGURED_REASON",
    "ExecuteOutcome",
    "InvokerUnconfiguredError",
    "ToolOpInvoker",
    "clear_tool_op_invoker",
    "get_tool_op_invoker",
    "run_tool_op",
    "set_tool_op_invoker",
]
