"""Life MCP notify — server-side proxy to email-bridge ``/pager/notify``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp_events import record
from pager_notify.life_notify import (
    append_ref_to_body,
    deliver_pager_notify,
    normalize_ref,
)
from pager_notify.so_what import SMS_BODY_MAX, SMS_SUBJECT_MAX

from ._agent_bus_author import resolve_dispatch_from_agent

if TYPE_CHECKING:
    from fastmcp import FastMCP

_MAX_SUBJECT = SMS_SUBJECT_MAX
_MAX_BODY = SMS_BODY_MAX
_MAX_TAG = 40


def register_notify_tools(mcp: FastMCP) -> None:
    @mcp.tool(title="Notify")
    def notify(
        subject: str,
        body: str,
        ref: str = "",
        tag: str = "",
    ) -> dict[str, Any]:
        """Attention pager — proxy to email-bridge ``/pager/notify`` (life surface).

        Fields: ``subject`` (≤120), ``body`` (≤4000), ``tag`` (≤40), ``ref`` (turn or
        ``cortex://`` URI — mandatory from caller; missing ref degrades to
        ``(unreferenced)``). Server stamps ``from_agent`` + timestamp. Honors
        ``PAGER_NOTIFY_ENABLED=0``. Delivery identity stays in email-bridge config —
        never in agent prose.

        AUDIENCE — human register binds on this path (and only paths like it): the
        reader is the human operator, so write human register here even when the
        session is agent-operated (web automation ⇏ agent audience — the path
        decides, not the session). Architecture-first: name ULG systems and where
        they sit when that is the point; ground in vision (what this serves);
        so-what first. The distinction is architecture vs implementation — ¬ lead
        with file paths, SHAs, function names, test names, or interagent closeout
        shape (``status:``, paths, evidence); those belong in ``ref``. Subject
        ``COME TO IDE`` = interrupt (problem, options exhausted); any other
        subject is awareness and ¬ implies he must open Cursor. Inverse binds: ¬
        human register on bus turns, packets, or bodies addressed to model seats.
        See ``agent_skill:pager-notify`` (when-to-page classes).
        """
        from_agent, err = resolve_dispatch_from_agent("")
        if err is not None:
            return err

        from claude_bundles.mission_close_wake import (
            refusal_envelope,
            validate_mission_debrief_notify,
        )

        debrief = validate_mission_debrief_notify(
            subject=subject, body=body, tag=tag
        )
        if not debrief.ok:
            record(
                "ops.notify.rejected",
                reason=debrief.reason or "mission_debrief_beyond_missing",
                tag=(tag or "")[:_MAX_TAG],
            )
            return refusal_envelope(debrief)

        return deliver_pager_notify(
            subject=subject,
            body=body,
            tag=tag,
            ref=ref,
            from_agent=from_agent,
        )


__all__ = [
    "append_ref_to_body",
    "normalize_ref",
    "register_notify_tools",
]
