"""Caller-facing bridge failure envelopes — message/code/retryable from forensics.

``bridge_death_class`` is inferred from stderr; transport exceptions
(``NetworkError``, connection refused) are secondary. This module keeps
``error_envelope.message`` and ``code`` aligned with classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_BRIDGE_CLASS_TO_CODE: dict[str, str] = {
    "spawn_enoent_missing_cwd": "CURSOR_SDK_BRIDGE_SPAWN_CWD",
    "uncaught_exception_bundle": "CURSOR_SDK_BRIDGE_CRASH",
}

_NON_RETRYABLE_BRIDGE_CLASSES = frozenset(
    {"spawn_enoent_missing_cwd", "uncaught_exception_bundle"}
)


@dataclass(frozen=True, slots=True)
class BridgeFailureDelivery:
    """Resolved caller-facing fields for a classified bridge death."""

    code: str
    message: str
    retryable: bool
    worker_error: str


def bridge_failure_delivery_from_forensics(
    *,
    forensics: dict[str, Any] | None,
    exc: BaseException | None = None,
    default_code: str = "CURSOR_SDK_BRIDGE_ABORT",
) -> BridgeFailureDelivery | None:
    """Return delivery overrides when forensics carry a classified bridge death."""
    if not isinstance(forensics, dict):
        return None
    bridge_class = forensics.get("bridge_death_class")
    if not isinstance(bridge_class, str) or not bridge_class.strip():
        return None
    if bridge_class in ("empty", "unclassified"):
        return None

    code = _BRIDGE_CLASS_TO_CODE.get(bridge_class, default_code)
    message = _compose_bridge_death_message(bridge_class=bridge_class, forensics=forensics)
    retryable = bridge_class not in _NON_RETRYABLE_BRIDGE_CLASSES
    transport = forensics.get("cause")
    if exc is not None:
        worker_error = f"{type(exc).__name__}: {exc}"
    elif isinstance(transport, str) and transport:
        worker_error = transport
    else:
        worker_error = message
    return BridgeFailureDelivery(
        code=code,
        message=message,
        retryable=retryable,
        worker_error=worker_error,
    )


def _compose_bridge_death_message(
    *,
    bridge_class: str,
    forensics: dict[str, Any],
) -> str:
    tail = forensics.get("bridge_stderr_tail")
    tail_line = ""
    if isinstance(tail, list) and tail:
        tail_line = str(tail[-1]).strip()
    cwd = forensics.get("bridge_spawn_cwd")
    cwd_exists = forensics.get("bridge_spawn_cwd_exists")
    proc_cwd = forensics.get("bridge_process_cwd")
    parts: list[str] = [f"cursor-sdk bridge death ({bridge_class})"]
    if isinstance(cwd, str) and cwd:
        exists_bit = (
            "missing"
            if cwd_exists is False
            else "present"
            if cwd_exists is True
            else "unknown"
        )
        parts.append(f"spawn_cwd={cwd} ({exists_bit} at capture)")
    if isinstance(proc_cwd, str) and proc_cwd:
        parts.append(f"process_cwd={proc_cwd}")
    if tail_line:
        parts.append(f"stderr_tail={tail_line}")
    transport = forensics.get("cause")
    if isinstance(transport, str) and transport.strip():
        parts.append(f"transport={transport.strip()}")
    return "; ".join(parts)
