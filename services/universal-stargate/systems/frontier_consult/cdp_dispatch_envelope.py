"""Per-execution CDP dispatch-link envelope metadata (admit + terminate)."""

from __future__ import annotations

from dataclasses import dataclass

_ENVELOPE: dict[str, CdpDispatchEnvelope] = {}


@dataclass(slots=True)
class CdpDispatchEnvelope:
    execution_id: str
    thread_id: str
    pointer_turn: int
    admit_reason: str
    caller_supplied_thread: bool
    dispatch_link_terminal: bool | None = None


def record_cdp_admit(
    *,
    execution_id: str,
    thread_id: str,
    pointer_turn: int,
    admit_reason: str,
    caller_supplied_thread: bool,
) -> None:
    _ENVELOPE[execution_id] = CdpDispatchEnvelope(
        execution_id=execution_id,
        thread_id=thread_id,
        pointer_turn=pointer_turn,
        admit_reason=admit_reason,
        caller_supplied_thread=caller_supplied_thread,
    )


def record_cdp_dispatch_link_terminal(
    *, execution_id: str, terminated: bool
) -> None:
    env = _ENVELOPE.get(execution_id)
    if env is None:
        return
    env.dispatch_link_terminal = terminated


def get_cdp_dispatch_envelope(execution_id: str) -> CdpDispatchEnvelope | None:
    return _ENVELOPE.get(execution_id)


def clear_cdp_dispatch_envelope(execution_id: str) -> None:
    _ENVELOPE.pop(execution_id, None)


def reset_cdp_dispatch_envelope_for_tests() -> None:
    _ENVELOPE.clear()
