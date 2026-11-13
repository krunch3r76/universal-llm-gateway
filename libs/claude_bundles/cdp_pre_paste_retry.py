"""Pre-paste CDP submit retry — reattach house, backoff, dispatch-row accounting (a:43814)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cdp_ask.client import CdpAskClient, CdpAskClientError

PRE_PASTE_BACKOFF_S = (10.0, 30.0)
MAX_PRE_PASTE_ATTEMPTS = 3
_CLAUDE_URL_MARKERS = ("claude.ai/",)

_PRE_PASTE_TOKENS = (
    "connection refused",
    "econnrefused",
    "connect timeout",
    "no tab",
    "no attachment",
    "not attached",
    "cdp connect timeout",
    "project_ask_url not configured",
    "no registration",
    "no host",
    "reattach_no_host",
)

_READ_TIMEOUT_MARKERS = (
    "read timeout",
    "readtimedout",
    "timed out waiting",
)


def has_claude_conversation_url(url: str | None) -> bool:
    """True when *url* is a recorded claude.ai conversation URL."""
    text = (url or "").strip().lower()
    return bool(text) and any(marker in text for marker in _CLAUDE_URL_MARKERS)


def is_post_paste_state(
    *,
    chat_url: str | None,
    satellite_execution_id: str | None,
) -> bool:
    """Post-paste when a claude URL exists or the satellite accepted submit."""
    if has_claude_conversation_url(chat_url):
        return True
    return bool((satellite_execution_id or "").strip())


def is_pre_paste_failure(exc: CdpAskClientError | str | None) -> bool:
    """Classify connect-phase satellite errors that occur before paste."""
    if exc is None:
        return False
    text = str(exc).lower()
    if "http 529" in text or "http 503" in text or "upstream_overloaded" in text:
        return False
    if isinstance(exc, CdpAskClientError) and exc.status_code in (529, 503):
        return False
    if any(marker in text for marker in _READ_TIMEOUT_MARKERS):
        return False
    if "timed out" in text and "connect" not in text:
        return False
    if "cdp-ask unreachable" in text and "connection" not in text:
        return False
    return any(token in text for token in _PRE_PASTE_TOKENS)


@dataclass(frozen=True, slots=True)
class PrePasteRetryContext:
    """Dispatch-scoped pre-paste retry (one loop per execution_id)."""

    thread_id: str
    execution_id: str
    request_id: str
    read_chat_url: Callable[[], str | None]
    read_attempt_count: Callable[[], int]
    read_satellite_execution_id: Callable[[], str | None]
    record_attempt: Callable[[int], None]
    emit_attempt: Callable[[int, str], None]
    emit_exhausted: Callable[[str], None]
    reattach_house: Callable[[], dict[str, Any]]


def submit_with_pre_paste_retry(
    relay: CdpAskClient,
    submit_req: Any,
    *,
    client: Any | None,
    sleep: Callable[[float], None],
    ctx: PrePasteRetryContext | None,
) -> dict[str, Any]:
    """Submit with bounded pre-paste retries; overload stays single-retry upstream."""
    from claude_bundles.cdp_model_endpoint import (
        _is_retryable_overload_status,
    )

    if ctx is None:
        return _submit_once_or_overload(relay, submit_req, client=client, sleep=sleep)

    def _post_paste() -> bool:
        return is_post_paste_state(
            chat_url=ctx.read_chat_url(),
            satellite_execution_id=ctx.read_satellite_execution_id(),
        )

    if _post_paste():
        return _submit_once_or_overload(relay, submit_req, client=client, sleep=sleep)

    attempt_base = max(0, min(ctx.read_attempt_count(), MAX_PRE_PASTE_ATTEMPTS))
    if attempt_base >= MAX_PRE_PASTE_ATTEMPTS:
        msg = "pre_paste_retry_exhausted"
        ctx.emit_exhausted(msg)
        raise CdpAskClientError(msg)

    last_exc: CdpAskClientError | None = None
    for offset in range(MAX_PRE_PASTE_ATTEMPTS - attempt_base):
        attempt_no = attempt_base + offset + 1
        try:
            return relay.submit(submit_req, client=client)
        except CdpAskClientError as exc:
            last_exc = exc
            if _is_retryable_overload_status(exc):
                return _submit_once_or_overload(
                    relay, submit_req, client=client, sleep=sleep
                )
            if not is_pre_paste_failure(exc):
                raise
            if _post_paste():
                raise
            if attempt_no >= MAX_PRE_PASTE_ATTEMPTS:
                ctx.record_attempt(attempt_no)
                ctx.emit_exhausted(str(exc))
                raise
            ctx.record_attempt(attempt_no)
            ctx.emit_attempt(attempt_no, str(exc))
            ctx.reattach_house()
            backoff = PRE_PASTE_BACKOFF_S[
                min(attempt_no - 1, len(PRE_PASTE_BACKOFF_S) - 1)
            ]
            sleep(backoff)
            if _post_paste():
                raise
    assert last_exc is not None
    raise last_exc


def _submit_once_or_overload(
    relay: CdpAskClient,
    submit_req: Any,
    *,
    client: Any | None,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    from claude_bundles.cdp_model_endpoint import (
        MAX_OVERLOAD_SUBMIT_ATTEMPTS,
        SUBMIT_RETRY_BACKOFF_S,
        _is_retryable_overload_status,
    )

    last_exc: CdpAskClientError | None = None
    for attempt in range(MAX_OVERLOAD_SUBMIT_ATTEMPTS):
        try:
            return relay.submit(submit_req, client=client)
        except CdpAskClientError as exc:
            last_exc = exc
            if (
                attempt + 1 < MAX_OVERLOAD_SUBMIT_ATTEMPTS
                and _is_retryable_overload_status(exc)
            ):
                sleep(SUBMIT_RETRY_BACKOFF_S)
                continue
            raise
    assert last_exc is not None
    raise last_exc


__all__ = [
    "MAX_PRE_PASTE_ATTEMPTS",
    "PRE_PASTE_BACKOFF_S",
    "PrePasteRetryContext",
    "has_claude_conversation_url",
    "is_post_paste_state",
    "is_pre_paste_failure",
    "submit_with_pre_paste_retry",
]
