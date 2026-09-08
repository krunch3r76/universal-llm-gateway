"""O15 D2 — lid-close seal: harvest Window anchors after CHECKPOINT post."""

from __future__ import annotations

import os
import threading
from typing import Any

from cortex_store.transcript_cp_anchors import window_anchors_from_text
from universal_logging import get_logger

from .checkpoint_projection import is_checkpoint_subject
from .events.checkpoint_lid_seal import (
    emit_checkpoint_lid_seal_failed,
    emit_checkpoint_lid_seal_requested,
)
from .thread_classification import classify_thread

logger = get_logger(__name__)

_ENABLED = os.environ.get("AGENT_BUS_LID_SEAL", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)


def maybe_schedule_lid_seal_on_checkpoint(
    *,
    thread: str,
    subject: str,
    body: str,
    thread_tags: list[str],
) -> None:
    """Fire-and-forget harvest for each Window anchor on a root CHECKPOINT."""
    if not _ENABLED:
        return
    if not is_checkpoint_subject(subject):
        return
    if classify_thread(thread_tags)["spine"] != "root":
        return
    anchors = window_anchors_from_text(body)
    if not anchors:
        return
    try:
        threading.Thread(
            target=_run_lid_seals,
            kwargs={"thread": thread, "anchors": anchors},
            daemon=True,
            name=f"lid-seal-{thread}",
        ).start()
    except Exception:
        logger.warning(
            "lid_seal thread spawn failed for thread %s",
            thread,
            exc_info=True,
        )


def _run_lid_seals(
    *,
    thread: str,
    anchors: tuple[tuple[str, int], ...],
) -> None:
    from .tape_harvest import request_lid_close_seal

    for transcript_id, turns_at_cp in anchors:
        emit_checkpoint_lid_seal_requested(
            thread=thread,
            transcript_id=transcript_id,
            turns_at_cp=turns_at_cp,
        )
        try:
            result = request_lid_close_seal(
                thread_id=thread,
                explicit_transcript_ids=[transcript_id],
            )
        except Exception as exc:
            emit_checkpoint_lid_seal_failed(
                thread=thread,
                transcript_id=transcript_id,
                turns_at_cp=turns_at_cp,
                error=str(exc),
            )
            logger.warning(
                "lid_seal harvest raised for thread %s transcript %s",
                thread,
                transcript_id,
                exc_info=True,
            )
            continue
        if result.get("error"):
            emit_checkpoint_lid_seal_failed(
                thread=thread,
                transcript_id=transcript_id,
                turns_at_cp=turns_at_cp,
                error=str(result.get("error")),
                detail=result.get("detail"),
            )


__all__ = ["maybe_schedule_lid_seal_on_checkpoint"]
