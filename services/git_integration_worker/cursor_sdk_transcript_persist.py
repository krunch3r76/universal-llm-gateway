"""Persist full cursor-sdk run transcripts to cortex scratch (``notes/tmp/dispatches``).

Uses pinned-deliverable ingress (Stargate) in production and
``write_pinned_deliverable_impl`` in hermetic tests. Separate from the repo
closeout sidecar and from ``notes/system/threads`` structured receipts.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_deliverables import (
    PinnedWriteFn,
    PinnedWriteResult,
    cortex_promote_ack,
    default_post_pinned_deliverable,
    pinned_write_digest,
)
from services.git_integration_worker.cursor_sdk_transcript import (
    reconstruct_run_transcript,
)

logger = get_logger(__name__)

SDK_TRANSCRIPT_PERSIST_FAILED = "capture:sdk_transcript_persist_failed"
_EMPTY_STUB = "# empty transcript\n"


def sdk_transcript_rel_path(dispatch_id: str) -> str:
    """Relative cortex path for a dispatch run transcript."""
    return f"notes/tmp/dispatches/{dispatch_id}/transcript.md"


def prepare_sdk_transcript_body(turns: Iterable | None) -> str:
    """Build transcript markdown from post-wait conversation turns."""
    if not turns:
        return _EMPTY_STUB
    text = reconstruct_run_transcript(turns)
    if not text.strip():
        return _EMPTY_STUB
    return f"{text}\n"


WritePinnedFn = Callable[[str, str], PinnedWriteResult | dict[str, Any]]


@dataclass(frozen=True)
class SdkTranscriptPersistResult:
    """Outcome of a transcript pin attempt."""

    uri: str | None
    sha256: str | None
    deviation: str | None = None


def persist_sdk_transcript_sync(
    *,
    dispatch_id: str,
    turns: Iterable | None,
    write_impl: WritePinnedFn | None = None,
) -> SdkTranscriptPersistResult:
    """Hermetic transcript persist via ``write_pinned_deliverable_impl`` (tests)."""
    if write_impl is None:
        from libs.cortex_store.dispatch_ops._pinned_deliverable import (
            write_pinned_deliverable_impl,
        )

        write_impl = write_pinned_deliverable_impl

    rel = sdk_transcript_rel_path(dispatch_id)
    content = prepare_sdk_transcript_body(turns)
    try:
        ack = write_impl(rel, content)
    except Exception as exc:
        logger.warning(
            "sdk transcript pinned write raised: dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )
        return SdkTranscriptPersistResult(None, None, SDK_TRANSCRIPT_PERSIST_FAILED)
    if not ack or ack.get("error"):
        logger.warning(
            "sdk transcript pinned write failed: dispatch_id=%s rel=%s err=%s",
            dispatch_id,
            rel,
            (ack or {}).get("error"),
        )
        return SdkTranscriptPersistResult(None, None, SDK_TRANSCRIPT_PERSIST_FAILED)
    uri = cortex_promote_ack(ack)
    if not uri:
        return SdkTranscriptPersistResult(None, None, SDK_TRANSCRIPT_PERSIST_FAILED)
    return SdkTranscriptPersistResult(uri, pinned_write_digest(ack), None)


async def persist_sdk_transcript_async(
    *,
    dispatch_id: str,
    thread_id: str,
    turns: Iterable | None,
    post_pinned: PinnedWriteFn | None = None,
) -> SdkTranscriptPersistResult:
    """Pin transcript bytes through Stargate pinned-deliverable ingress."""
    rel = sdk_transcript_rel_path(dispatch_id)
    content = prepare_sdk_transcript_body(turns)
    writer = post_pinned or default_post_pinned_deliverable
    try:
        ack = await writer(
            rel_path=rel,
            content=content,
            write_if_absent=False,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
        )
    except Exception as exc:
        logger.warning(
            "sdk transcript ingress transport error: dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )
        return SdkTranscriptPersistResult(None, None, SDK_TRANSCRIPT_PERSIST_FAILED)
    if not ack or "error" in ack:
        logger.warning(
            "sdk transcript ingress rejected: dispatch_id=%s rel=%s",
            dispatch_id,
            rel,
        )
        return SdkTranscriptPersistResult(None, None, SDK_TRANSCRIPT_PERSIST_FAILED)
    uri = cortex_promote_ack(ack)
    if not uri:
        return SdkTranscriptPersistResult(None, None, SDK_TRANSCRIPT_PERSIST_FAILED)
    return SdkTranscriptPersistResult(uri, pinned_write_digest(ack), None)
