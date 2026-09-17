"""Shared life-surface pager delivery — notify MCP tool and auto debrief paths."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from universal_protocol.errors import delivery_non_event_envelope

from pager_notify.client import _pager_disabled_reason, notify_pager, pager_enabled
from pager_notify.so_what import SMS_BODY_MAX, SMS_SUBJECT_MAX

_UNREFERENCED = "(unreferenced)"
_MAX_REF = 200


def normalize_ref(ref: str) -> tuple[str, bool]:
    """Return ref text and whether the caller omitted it."""
    stripped = (ref or "").strip()
    if not stripped:
        return _UNREFERENCED, True
    return stripped[:_MAX_REF], False


def append_ref_to_body(body: str, ref: str) -> str:
    """Stamp ref into the pager body; prefer full body over ref when at cap."""
    suffix = f"\nref: {ref}"
    base = (body or "").strip()
    if not base:
        return suffix.strip()[:SMS_BODY_MAX]
    combined = f"{base}{suffix}"
    if len(combined) <= SMS_BODY_MAX:
        return combined
    if len(base) >= SMS_BODY_MAX:
        return base[:SMS_BODY_MAX]
    return f"{base}{suffix[: SMS_BODY_MAX - len(base)]}"


def _default_record(name: str, **kwargs: Any) -> None:
    """Lazy import — ``mcp_events`` lives on the MCP process path only."""
    from mcp_events import record

    record(name, **kwargs)


def deliver_pager_notify(
    *,
    subject: str,
    body: str,
    tag: str,
    ref: str,
    from_agent: str,
    sent_event: str = "ops.notify.sent",
    failed_event: str | None = None,
    record_fn: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Validate-free pager delivery with fail-closed status envelope."""
    emit = record_fn if record_fn is not None else _default_record
    ref_norm, unreferenced = normalize_ref(ref)
    subject_out = (subject or "ULG notify")[:SMS_SUBJECT_MAX]
    tag_out = (tag or "")[:40]
    body_out = append_ref_to_body(body, ref_norm)
    stamped_at = datetime.now(UTC).isoformat()

    base: dict[str, Any] = {
        "from_agent": from_agent,
        "ref": ref_norm,
        "unreferenced": unreferenced,
        "stamped_at": stamped_at,
    }

    if not pager_enabled():
        disabled_reason = _pager_disabled_reason() or "PAGER_NOTIFY_ENABLED=0"
        return delivery_non_event_envelope(
            code="PAGER_NOTIFY_DISABLED",
            message=f"Pager delivery did not occur: {disabled_reason}",
            reason=disabled_reason,
            status="blocked",
            data={"channel": "pager", "from_agent": from_agent, "ref": ref_norm},
        )

    result = asyncio.run(notify_pager(subject_out, body_out, tag=tag_out))

    if result:
        emit(
            sent_event,
            from_agent=from_agent,
            ref=ref_norm,
            unreferenced=unreferenced,
            tag=tag_out,
            subject=subject_out,
            stamped_at=stamped_at,
        )
        return {**base, "status": "sent"}

    fail_reason = result.reason or "notify_pager returned failed"
    if failed_event:
        emit(
            failed_event,
            from_agent=from_agent,
            ref=ref_norm,
            tag=tag_out,
            reason=fail_reason,
            error=result.error or "",
            stamped_at=stamped_at,
        )
    return delivery_non_event_envelope(
        code="PAGER_NOTIFY_FAILED",
        message=f"Pager delivery failed: {fail_reason}",
        reason=fail_reason,
        status="failed",
        data={
            "channel": "pager",
            "from_agent": from_agent,
            "ref": ref_norm,
            "detail": result.error or "",
        },
    )


__all__ = [
    "append_ref_to_body",
    "deliver_pager_notify",
    "normalize_ref",
]
