"""Build ``PrePasteRetryContext`` for Stargate CDP workers (a:43814)."""

from __future__ import annotations

from typing import Any

from agent_bus_store.db.threads_atomic import (
    read_dispatch_link_chat_url,
    read_pre_paste_retry_attempts,
    record_pre_paste_retry_attempt,
)
from cdp_ask.client import CdpAskClient
from claude_bundles.cdp_pre_paste_retry import PrePasteRetryContext

from .cdp_events import (
    CdpGeneratePrePasteRetryAttempt,
    CdpGeneratePrePasteRetryExhausted,
    publish_cdp_kwargs,
)


def build_pre_paste_retry_context(
    *,
    thread_id: str,
    execution_id: str,
    request_id: str,
    purpose: str | None,
    relay: CdpAskClient | None = None,
) -> PrePasteRetryContext:
    """Dispatch-row-backed pre-paste retry callbacks."""
    client = relay or CdpAskClient()
    purp = (purpose or "ask").strip() or "ask"

    def _read_chat() -> str | None:
        return read_dispatch_link_chat_url(
            thread_id=thread_id,
            execution_id=execution_id,
        )

    def _read_count() -> int:
        return read_pre_paste_retry_attempts(
            thread_id=thread_id,
            execution_id=execution_id,
        )

    def _record(attempt: int) -> None:
        record_pre_paste_retry_attempt(
            thread_id=thread_id,
            execution_id=execution_id,
            attempt=attempt,
        )

    def _emit_attempt(attempt: int, error: str) -> None:
        publish_cdp_kwargs(
            CdpGeneratePrePasteRetryAttempt,
            request_id=request_id,
            execution_id=execution_id,
            thread_id=thread_id,
            attempt=attempt,
            error=error,
        )

    def _emit_exhausted(error: str) -> None:
        publish_cdp_kwargs(
            CdpGeneratePrePasteRetryExhausted,
            request_id=request_id,
            execution_id=execution_id,
            thread_id=thread_id,
            error=error,
        )

    def _probe() -> dict[str, Any]:
        try:
            return client.health()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def _reattach() -> dict[str, Any]:
        try:
            return client.reattach_house(purpose=purp)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    return PrePasteRetryContext(
        thread_id=thread_id,
        execution_id=execution_id,
        request_id=request_id,
        read_chat_url=_read_chat,
        read_attempt_count=_read_count,
        record_attempt=_record,
        emit_attempt=_emit_attempt,
        emit_exhausted=_emit_exhausted,
        reattach_house=_reattach,
        probe_health=_probe,
    )


__all__ = ["build_pre_paste_retry_context"]
