"""Defer resume-fence signals until the surrounding write transaction commits.

``append_fence_event`` inserts a journal row and normally emits at once. Arm
and pour hold one ``write_connect`` across the checkpoint-tip select and that
insert, so a nested append must queue the signal. Emitting before the outer
commit would let a listener observe a row a rollback removed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .events.resume_fence import (
    emit_resume_fence_armed,
    emit_resume_fence_denied,
    emit_resume_fence_expired,
    emit_resume_fence_poured,
    emit_resume_fence_released,
)

_DEFER_FENCE_EMITS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "defer_fence_emits",
    default=None,
)


def fence_emit_bucket() -> list[dict[str, Any]] | None:
    """Return the open defer list, or None when signals should emit now."""
    return _DEFER_FENCE_EMITS.get()


def emit_fence_event(
    *,
    event: str,
    fence_id: str,
    root_thread: str,
    transcript_id: str | None,
    payload: dict[str, Any] | None,
) -> None:
    """Emit the signal that matches one appended fence journal event."""
    if event == "armed":
        emit_resume_fence_armed(
            fence_id=fence_id,
            root_thread=root_thread,
            transcript_id=transcript_id,
            source=(payload or {}).get("source", "mcp"),
        )
    elif event == "poured":
        emit_resume_fence_poured(
            fence_id=fence_id,
            root_thread=root_thread,
            bundle_bytes=int((payload or {}).get("bundle_bytes", 0)),
            readable_counts=(payload or {}).get("readable_counts") or {},
            seal_status=str((payload or {}).get("seal_status", "")),
            mission_bytes=int((payload or {}).get("mission_bytes", 0)),
            card_inlined=bool((payload or {}).get("card_inlined", True)),
            bundle_version=str(
                (payload or {}).get("bundle_version", "resume-bundle-v1")
            ),
            bundle_sha256=(payload or {}).get("bundle_sha256"),
        )
    elif event == "denied":
        emit_resume_fence_denied(
            fence_id=fence_id,
            surface=str((payload or {}).get("surface", "")),
            tool=str((payload or {}).get("tool", "")),
            op=str((payload or {}).get("op", "")),
            target=str((payload or {}).get("target", "")),
            reason=str((payload or {}).get("reason", "")),
        )
    elif event == "released":
        emit_resume_fence_released(
            fence_id=fence_id,
            release_turn=int((payload or {}).get("release_turn", 0)),
        )
    elif event == "expired":
        from .resume_fence_store import RESUME_FENCE_IDLE_S

        emit_resume_fence_expired(
            fence_id=fence_id,
            idle_seconds=int((payload or {}).get("idle_seconds", RESUME_FENCE_IDLE_S)),
        )


@contextmanager
def fence_writes_then_emit() -> Iterator[None]:
    """Queue fence signals until the caller's write transaction commits.

    Nested ``append_fence_event`` calls join the open ``write_connect``.
    The queued signals fire after that block exits, which is after commit.
    An exception drops the bucket with the rollback.
    """
    bucket: list[dict[str, Any]] = []
    token = _DEFER_FENCE_EMITS.set(bucket)
    try:
        yield
    except BaseException:
        raise
    else:
        for item in bucket:
            emit_fence_event(**item)
    finally:
        _DEFER_FENCE_EMITS.reset(token)
