"""Thin progress turns for long-running cursor-auto dispatches (Fable §5).

Without them a codeblind operator cannot tell slow from dead, and finding out
means violating the Cowork wait ceiling (many short holds, never one long one).

Progress turns are emitted from the ledger poll loop's own tick, so one can never
land after the terminal turn, and they are **completion-token-free**: a waiter
keyed on ``status:done`` (or any alternate) must not resolve on a heartbeat.
"""

from __future__ import annotations

import json
import time
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_bus import CursorBusClient

logger = get_logger(__name__)

_FROM_AUTO = "cursor-auto"
# Fable §5 "every N minutes" — sparse enough that a heartbeat is not the noise.
PROGRESS_INTERVAL_S = 300.0

_FORBIDDEN_TOKENS = (
    "status:done",
    "status:failed",
    "status:needs-attended",
    "status:blocked",
    "status:superseded",
)


def strip_completion_tokens(text: str) -> str:
    """Neutralize completion tokens so a heartbeat can never resolve a waiter."""
    cleaned = text
    for token in _FORBIDDEN_TOKENS:
        cleaned = cleaned.replace(token, token.replace("status:", "state-"))
    return cleaned


async def emit_dispatch_progress(
    job: AutoJob,
    *,
    client: CursorBusClient,
    elapsed_s: float,
    ledger_state: dict[str, Any] | None,
    started_at: float,
) -> dict[str, Any]:
    """Post one token-free progress turn; failures are logged, never raised."""
    payload = {
        "summary": (
            f"Still running — {elapsed_s / 60:.1f} min elapsed, no terminal yet."
        ),
        "elapsed_s": round(elapsed_s, 3),
        "started_at": round(started_at, 3),
        "job_id": job.job_id,
        "request_turn": job.turn_number,
        "request_id": job.request_id,
        "ledger": ledger_state or {},
    }
    body = strip_completion_tokens(json.dumps(payload, indent=2))
    subject = strip_completion_tokens(f"progress — elapsed {int(elapsed_s)}s")
    try:
        reply = await client.reply(
            thread_id=job.thread_id,
            to_agent=job.from_agent,
            from_agent=_FROM_AUTO,
            subject=subject,
            body=body,
            allow_long_body=True,
        )
    except Exception as exc:
        logger.warning("cursor-auto progress turn failed job=%s: %s", job.job_id, exc)
        return {"ok": False, "error": str(exc)}
    return {"ok": reply.status_code < 400, "status_code": reply.status_code}


class ProgressEmitter:
    """Emit progress at most once per interval while a dispatch runs."""

    def __init__(
        self,
        job: AutoJob,
        *,
        client: CursorBusClient,
        interval_s: float = PROGRESS_INTERVAL_S,
    ) -> None:
        self._job = job
        self._client = client
        self._interval_s = interval_s
        self._started_at = time.monotonic()
        self._last_emit = self._started_at

    async def maybe_emit(self, ledger_state: dict[str, Any] | None) -> None:
        now = time.monotonic()
        if (now - self._last_emit) < self._interval_s:
            return
        self._last_emit = now
        await emit_dispatch_progress(
            self._job,
            client=self._client,
            elapsed_s=now - self._started_at,
            ledger_state=ledger_state,
            started_at=self._started_at,
        )


__all__ = [
    "PROGRESS_INTERVAL_S",
    "ProgressEmitter",
    "emit_dispatch_progress",
    "strip_completion_tokens",
]
