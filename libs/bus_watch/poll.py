"""Sliced long-poll loop with heartbeat + optional state-file updates."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bus_watch.state import write_state

DEFAULT_WAIT_SLICE_S = 20.0
DEFAULT_MAX_HOURS = 6.0
DEFAULT_TRANSPORT_RETRY_S = 3.0


def sliced_wait_loop(
    *,
    wait_once: Callable[[int], dict[str, Any]],
    is_complete: Callable[[dict[str, Any]], bool],
    on_incomplete: Callable[[dict[str, Any]], None],
    on_complete: Callable[[dict[str, Any]], int],
    transport_errors: tuple[type[BaseException], ...],
    on_transport_error: Callable[[BaseException], None],
    wait_slice_s: float = DEFAULT_WAIT_SLICE_S,
    max_hours: float = DEFAULT_MAX_HOURS,
    poll_sleep_s: float = 2.0,
    transport_retry_s: float = DEFAULT_TRANSPORT_RETRY_S,
    state_file: Path | None = None,
    heartbeat_label: str = "watcher",
    thread_id: str = "",
    after_turn: int | None = None,
) -> int:
    """Poll until complete / expired. Returns process exit code."""
    started = time.monotonic()
    slice_s = max(1, int(wait_slice_s))
    if state_file is not None:
        write_state(
            state_file,
            status="polling",
            thread=thread_id,
            after_turn=after_turn,
            label=heartbeat_label,
        )

    while True:
        elapsed_s = time.monotonic() - started
        if max_hours > 0 and elapsed_s >= max_hours * 3600.0:
            print(
                f"watcher expired label={heartbeat_label} elapsed={elapsed_s:.0f}s",
                flush=True,
            )
            if state_file is not None:
                write_state(state_file, status="expired", elapsed_s=round(elapsed_s, 1))
            return 2

        print(
            f"… heartbeat label={heartbeat_label} thread={thread_id} "
            f"after_turn={after_turn} elapsed={elapsed_s:.0f}s",
            flush=True,
        )
        try:
            snap = wait_once(slice_s)
        except transport_errors as exc:
            on_transport_error(exc)
            time.sleep(transport_retry_s)
            continue

        if is_complete(snap):
            if state_file is not None:
                write_state(state_file, status="complete")
            return on_complete(snap)

        status = str(snap.get("status") or "")
        if state_file is not None:
            write_state(
                state_file,
                status=("predicate_unmet" if status == "predicate_unmet" else "polling"),
                last_status=status,
                turn_count=snap.get("turn_count"),
                thread_status=snap.get("thread_status"),
            )
        on_incomplete(snap)
        time.sleep(poll_sleep_s)
