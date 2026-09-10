"""claude.ai ↔ Cursor Composer keystroke bridge — thin agent-bus relay.

Each op is exactly one agent-bus REST call with the bridge subject vocabulary
(``BRIDGE_OPEN`` / ``MSG`` on write, ``TAB_READY`` / ``REPLY`` read back).
The io-side watcher (``scripts/watch-cursor-bridge-inbox.py``) consumes the
turns and drives the keystrokes; nothing here touches a process, ssh, or evdev.
Spec: cortex://notes/system/specs/cursor-keystroke-bridge-v1.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from mcp_events import record
from mcp_toolprogress import toolprogress_begin, toolprogress_end

from .agent_bus import _fetch_impl, _send_impl

if TYPE_CHECKING:
    from fastmcp import FastMCP

# Bridge turns are always authored by the life seat and addressed to the IDE seat.
_FROM = "web-anthropic"
_TO = "cursor"
_LANE_TAG = "lane:cursor-bridge"
_PREVIEW_CHARS = 300

BridgeOp = Literal["open_tab", "send_msg", "status"]


def _param_error(op: str, code: str, detail: str) -> dict[str, Any]:
    record(f"mcp.cursor_bridge.{op}.rejected", reason=code)
    return {"ok": False, "error": code, "detail": detail}


def _open_tab(
    *,
    thread: str | None,
    new_slug: str | None,
    slug: str | None,
    cowork_url: str | None,
) -> dict[str, Any]:
    if not slug:
        return _param_error("open_tab", "slug_required", "open_tab requires slug")
    if bool(thread) == bool(new_slug):
        return _param_error(
            "open_tab",
            "thread_xor_new_slug",
            "open_tab requires exactly one of thread or new_slug",
        )
    result = _send_impl(
        new_slug=new_slug,
        thread=thread or None,
        to=_TO,
        subject="BRIDGE_OPEN",
        body=f"slug: {slug}\ncowork_url: {cowork_url or ''}\n",
        from_agent=_FROM,
        summary=None,
        # Tagging is a thread-level attribute — only meaningful when minting.
        tags=[_LANE_TAG] if new_slug else None,
        lifecycle_state=None,
        after_turn=0,
        status="open",
        mark_read=False,
        close=False,
        attachments=None,
        allow_long_body=False,
    )
    if "error" in result:
        return result
    thread_id = (result.get("thread") or {}).get("id", "")
    record("mcp.cursor_bridge.open_tab", thread=thread_id, minted=bool(new_slug))
    return {**result, "op": "open_tab", "title": f"{thread_id} {slug}"}


def _send_msg(
    *, thread: str | None, body: str | None, after_turn: int | None
) -> dict[str, Any]:
    if not thread:
        return _param_error("send_msg", "thread_required", "send_msg requires thread")
    if not body:
        return _param_error("send_msg", "body_required", "send_msg requires body")
    result = _send_impl(
        new_slug=None,
        thread=thread,
        to=_TO,
        subject="MSG",
        body=body,
        from_agent=_FROM,
        summary=None,
        tags=None,
        lifecycle_state=None,
        after_turn=after_turn or 0,
        status="open",
        mark_read=False,
        close=False,
        attachments=None,
        allow_long_body=False,
    )
    if "error" in result:
        return result
    record("mcp.cursor_bridge.send_msg", thread=thread)
    return {**result, "op": "send_msg"}


def _status(*, thread: str | None, last: int) -> dict[str, Any]:
    if not thread:
        return _param_error("status", "thread_required", "status requires thread")
    result = _fetch_impl(
        to=None, thread=thread, last=last, unread=False, mark_read=False, compact=False
    )
    if isinstance(result, dict) and "error" in result:
        return result
    raw = result if isinstance(result, list) else result.get("turns", [])
    turns = [
        {
            "turn_number": t.get("turn_number"),
            "from": t.get("from"),
            "to": t.get("to"),
            "subject": t.get("subject"),
            "created_at": t.get("created_at"),
            "body_preview": (t.get("body") or "")[:_PREVIEW_CHARS],
        }
        for t in raw
        if isinstance(t, dict)
    ]
    reply_turns = [
        t["turn_number"]
        for t in turns
        if t["subject"] == "REPLY" and t["turn_number"] is not None
    ]
    record("mcp.cursor_bridge.status", thread=thread, count=len(turns))
    return {
        "op": "status",
        "thread": thread,
        "turns": turns,
        "tab_ready": any(t["subject"] == "TAB_READY" for t in turns),
        "last_reply_turn": max(reply_turns) if reply_turns else None,
    }


def register_cursor_bridge_tools(mcp: FastMCP) -> None:
    """Register the ``cursor_bridge`` relay tool on the MCP server instance."""

    @mcp.tool(title="Cursor Bridge")
    def cursor_bridge(
        op: BridgeOp,
        thread: str | None = None,
        new_slug: str | None = None,
        slug: str | None = None,
        cowork_url: str | None = None,
        body: str | None = None,
        after_turn: int | None = None,
        last: int = 10,
    ) -> dict[str, Any]:
        """claude.ai ↔ Cursor Composer keystroke bridge.

        open_tab posts BRIDGE_OPEN (watcher opens a Cursor tab titled
        ``<thread> <slug>`` and the in-tab agent posts TAB_READY); send_msg posts
        MSG (watcher wakes the tab; in-tab agent replies with subject REPLY);
        status shows lane turns. Thin relay over agent-bus — equivalent to
        agent_bus send/fetch with the bridge subject vocabulary.
        Spec: cortex://notes/system/specs/cursor-keystroke-bridge-v1.md

        Operations:
          open_tab (slug, thread XOR new_slug, cowork_url?) — returns the send
            payload + {op, title: "<thread_id> <slug>"}; new_slug also tags the
            lane ``lane:cursor-bridge``.
          send_msg (thread, body, after_turn?) — returns the send payload + {op}.
          status   (thread, last=10) — {op, thread, turns[turn_number, from, to,
            subject, created_at, body_preview], tab_ready, last_reply_turn}.

        Missing required params return ``{ok: false, error: <code>, detail}``.
        """
        t_prog, prog_timer = toolprogress_begin("cursor_bridge", inner_tool=op)
        err: str | None = None
        try:
            if op == "open_tab":
                return _open_tab(
                    thread=thread, new_slug=new_slug, slug=slug, cowork_url=cowork_url
                )
            if op == "send_msg":
                return _send_msg(thread=thread, body=body, after_turn=after_turn)
            if op == "status":
                return _status(thread=thread, last=last)
            return _param_error(
                "dispatch", "unknown_op", f"unknown op {op!r}; expected {BridgeOp}"
            )
        except Exception as exc:
            err = str(exc)
            raise
        finally:
            toolprogress_end(
                t_prog, prog_timer, "cursor_bridge", error=err, inner_tool=op
            )
