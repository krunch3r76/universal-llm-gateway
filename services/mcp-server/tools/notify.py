"""Life MCP notify — server-side proxy to email-bridge ``/pager/notify``."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mcp_events import record
from pager_notify.client import notify_pager, pager_enabled
from pager_notify.so_what import SMS_BODY_MAX, SMS_SUBJECT_MAX

from ._agent_bus_author import resolve_dispatch_from_agent

if TYPE_CHECKING:
    from fastmcp import FastMCP

_UNREFERENCED = "(unreferenced)"
_MAX_SUBJECT = SMS_SUBJECT_MAX
_MAX_BODY = SMS_BODY_MAX
_MAX_TAG = 40
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
        return suffix.strip()[:_MAX_BODY]
    combined = f"{base}{suffix}"
    if len(combined) <= _MAX_BODY:
        return combined
    if len(base) >= _MAX_BODY:
        return base[:_MAX_BODY]
    return f"{base}{suffix[: _MAX_BODY - len(base)]}"


def register_notify_tools(mcp: FastMCP) -> None:
    @mcp.tool(title="Notify")
    def notify(
        subject: str,
        body: str,
        ref: str = "",
        tag: str = "",
    ) -> dict[str, Any]:
        """Attention pager — proxy to email-bridge ``/pager/notify`` (life surface).

        Fields: ``subject`` (≤120), ``body`` (≤2000), ``tag`` (≤40), ``ref`` (turn or
        ``cortex://`` URI — mandatory from caller; missing ref degrades to
        ``(unreferenced)``). Server stamps ``from_agent`` + timestamp. Honors
        ``PAGER_NOTIFY_ENABLED=0``. Delivery identity stays in email-bridge config —
        never in agent prose.
        """
        from_agent, err = resolve_dispatch_from_agent("")
        if err is not None:
            return err

        ref_norm, unreferenced = normalize_ref(ref)
        subject_out = (subject or "ULG notify")[:_MAX_SUBJECT]
        tag_out = (tag or "")[:_MAX_TAG]
        body_out = append_ref_to_body(body, ref_norm)
        stamped_at = datetime.now(UTC).isoformat()

        base = {
            "from_agent": from_agent,
            "ref": ref_norm,
            "unreferenced": unreferenced,
            "stamped_at": stamped_at,
        }

        if not pager_enabled():
            return {
                **base,
                "status": "disabled",
                "reason": "PAGER_NOTIFY_ENABLED=0",
            }

        result = asyncio.run(
            notify_pager(subject_out, body_out, tag=tag_out)
        )

        if result:
            record(
                "ops.notify.sent",
                from_agent=from_agent,
                ref=ref_norm,
                unreferenced=unreferenced,
                tag=tag_out,
                subject=subject_out,
                stamped_at=stamped_at,
            )
            return {**base, "status": "sent"}

        out: dict[str, Any] = {
            **base,
            "status": "failed",
            "reason": result.reason or "notify_pager returned failed",
        }
        if result.error:
            out["error"] = result.error
        return out
