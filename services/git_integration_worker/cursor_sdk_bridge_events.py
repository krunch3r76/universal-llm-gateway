"""Event factory for unexpected cursor-sdk bridge subprocess exits.

Sibling of ``cursor_sdk_events`` rather than an addition to it: that module is
already well past the SLOC ceiling, and this signal has a single producer
(``cursor_sdk_bridge_stderr``).
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

logger = get_logger(__name__)


@event_factory
def FrontierSdkBridgeExited(  # noqa: N802
    dispatch_id: str,
    thread_id: str,
    elapsed_s: float,
    stderr_bytes: int,
    stderr_tail: list[str],
    log_path: str,
    exit_code: int | None = None,
    signal_name: str | None = None,
) -> Event:
    # Distinct from frontier.sdk.worker.failed, which reports what the HTTP
    # client saw after the fact (connection refused). This reports why the
    # subprocess went away, so an exit reason can be correlated with host-level
    # signals instead of being inferred from the refusal.
    payload: dict[str, Any] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "elapsed_s": elapsed_s,
        "stderr_bytes": stderr_bytes,
        "stderr_tail": stderr_tail,
        "log_path": log_path,
    }
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if signal_name is not None:
        payload["signal_name"] = signal_name
    return Event(
        signal="frontier.sdk.bridge.exited",
        payload=payload,
        scope="node",
    )


def emit_sdk_bridge_exited(
    *,
    dispatch_id: str,
    thread_id: str,
    elapsed_s: float,
    stderr_bytes: int,
    stderr_tail: list[str],
    log_path: str,
    exit_code: int | None = None,
    signal_name: str | None = None,
) -> None:
    """Publish an unexpected bridge subprocess exit with its captured stderr tail."""
    emit_frontier_event(
        FrontierSdkBridgeExited(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            elapsed_s=elapsed_s,
            stderr_bytes=stderr_bytes,
            stderr_tail=stderr_tail,
            log_path=log_path,
            exit_code=exit_code,
            signal_name=signal_name,
        )
    )
    logger.error(
        "cursor sdk bridge exited unexpectedly: dispatch_id=%s thread_id=%s "
        "exit_code=%s signal=%s elapsed_s=%s stderr_bytes=%s log=%s",
        dispatch_id,
        thread_id,
        exit_code,
        signal_name,
        elapsed_s,
        stderr_bytes,
        log_path,
    )
