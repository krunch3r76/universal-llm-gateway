"""claude.ai ↔ Cursor Composer keystroke bridge — thin agent-bus relay.

Each op is exactly one agent-bus REST call with the bridge subject vocabulary
(``BRIDGE_OPEN`` / ``MSG`` on write, ``TAB_READY`` / ``REPLY`` read back).
The io-side watcher (``scripts/watch-cursor-bridge-inbox.py``) consumes the
turns and drives the keystrokes; nothing here touches a process, ssh, or evdev.
Spec: cortex://notes/system/specs/cursor-keystroke-bridge-v1.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from cursor_bridge.bridge_glass_relay import (
    relay_prune_sidebar,
    relay_rename_tab,
    relay_wake_tab,
)
from cursor_bridge.bridge_spawn_relay import relay_spawn_house, relay_spawn_tab
from cursor_bridge.lane_ready import assess_lane_readiness
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

BridgeOp = Literal[
    "open_tab",
    "send_msg",
    "status",
    "spawn_tab",
    "spawn_house",
    "wake_tab",
    "rename_tab",
    "prune_sidebar",
]


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
    *,
    thread: str | None,
    body: str | None,
    after_turn: int | None,
    gui_host: str = ":8",
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Post MSG on the lane and optionally paste into a Glass agent by editor id.

    spawn_tab agents never post lane TAB_READY, so the io watcher refuses MSG
    preflight (watch-cursor-bridge-inbox.py). When ``agent_id`` is set, paste
    the body through Glass CDP like wake_tab so spawn_tab follow-ups land.
    Use ``agent_id`` only for spawn_tab agents — open_tab lanes already wake
    via the io watcher and would double-deliver if both paths run.
    """
    if not thread:
        return _param_error("send_msg", "thread_required", "send_msg requires thread")
    body_s = str(body or "").strip()
    if not body_s:
        return _param_error("send_msg", "body_required", "send_msg requires body")
    result = _send_impl(
        new_slug=None,
        thread=thread,
        to=_TO,
        subject="MSG",
        body=body_s,
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
    out: dict[str, Any] = {**result, "op": "send_msg"}
    aid = str(agent_id or "").strip()
    if aid:
        paste = relay_wake_tab(gui_host=gui_host, agent_id=aid, body=body_s)
        out["glass_paste"] = paste
        out["editor_agent_id"] = aid
        out["gui_host"] = gui_host
        if not paste.get("ok"):
            out["ok"] = False
            out["error"] = str(paste.get("error") or paste.get("reason") or "glass_paste_failed")
    record(
        "mcp.cursor_bridge.send_msg",
        thread=thread,
        glass_agent=aid or None,
        glass_ok=out.get("glass_paste", {}).get("ok") if aid else None,
    )
    return out


def _spawn_tab(
    *,
    gui_host: str,
    prompt: str | None,
    model: str | None,
    effort: str | None = None,
    fast: bool | str | None = None,
    model_knobs: dict[str, str] | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    if not prompt or not str(prompt).strip():
        return _param_error("spawn_tab", "prompt_required", "spawn_tab requires prompt")
    res = relay_spawn_tab(
        gui_host=gui_host,
        prompt=str(prompt).strip(),
        model=model,
        effort=effort,
        fast=fast,
        model_knobs=model_knobs,
        agent_id=agent_id,
    )
    record(
        "mcp.cursor_bridge.spawn_tab",
        gui_host=gui_host,
        ok=bool(res.get("ok")),
        agent_id=res.get("editor_agent_id") or res.get("_requested_agent_id"),
        has_model_knobs=bool(res.get("_knobs")),
    )
    res.pop("_knobs", None)
    res.pop("_requested_agent_id", None)
    return res


def _spawn_house(
    *,
    gui_host: str,
    slug: str | None,
    prompt: str | None,
    model: str | None,
    effort: str | None = None,
    fast: bool | str | None = None,
    model_knobs: dict[str, str] | None = None,
    agent_id: str | None = None,
    work_key: str | None = None,
) -> dict[str, Any]:
    if not slug or not str(slug).strip():
        return _param_error("spawn_house", "slug_required", "spawn_house requires slug")
    if not prompt or not str(prompt).strip():
        return _param_error(
            "spawn_house", "prompt_required", "spawn_house requires prompt"
        )
    res = relay_spawn_house(
        gui_host=gui_host,
        slug=str(slug).strip(),
        prompt=str(prompt).strip(),
        model=model,
        effort=effort,
        fast=fast,
        model_knobs=model_knobs,
        agent_id=agent_id,
        work_key=work_key,
    )
    record(
        "mcp.cursor_bridge.spawn_house",
        gui_host=gui_host,
        ok=bool(res.get("ok")),
        thread_id=res.get("thread_id"),
        agent_id=res.get("agent_id"),
    )
    return res


def _wake_tab(
    *, gui_host: str, agent_id: str | None, body: str | None
) -> dict[str, Any]:
    if not body or not str(body).strip():
        return _param_error("wake_tab", "body_required", "wake_tab requires body")
    res = relay_wake_tab(gui_host=gui_host, agent_id=agent_id, body=str(body).strip())
    if res.get("error") == "unknown_target":
        return _param_error("wake_tab", "unknown_target", str(res.get("detail") or ""))
    record("mcp.cursor_bridge.wake_tab", gui_host=gui_host, ok=bool(res.get("ok")))
    return res


def _rename_tab(
    *, gui_host: str, agent_id: str | None, title: str | None
) -> dict[str, Any]:
    if not agent_id or not str(agent_id).strip():
        return _param_error(
            "rename_tab", "agent_id_required", "rename_tab requires agent_id"
        )
    if not title or not str(title).strip():
        return _param_error("rename_tab", "title_required", "rename_tab requires title")
    res = relay_rename_tab(
        gui_host=gui_host,
        agent_id=str(agent_id).strip(),
        title=str(title).strip(),
    )
    record("mcp.cursor_bridge.rename_tab", gui_host=gui_host, ok=bool(res.get("ok")))
    return res


def _prune_sidebar(
    *, gui_host: str, thread: str | None, agent_id: str | None
) -> dict[str, Any]:
    if not thread or not str(thread).strip():
        return _param_error(
            "prune_sidebar",
            "thread_required",
            "prune_sidebar requires thread (root_id)",
        )
    if not agent_id or not str(agent_id).strip():
        return _param_error(
            "prune_sidebar", "agent_id_required", "prune_sidebar requires agent_id"
        )
    res = relay_prune_sidebar(
        gui_host=gui_host,
        thread=str(thread).strip(),
        agent_id=str(agent_id).strip(),
    )
    record("mcp.cursor_bridge.prune_sidebar", gui_host=gui_host, ok=bool(res.get("ok")))
    return res


def _status(*, thread: str | None, last: int) -> dict[str, Any]:
    if not thread:
        return _param_error("status", "thread_required", "status requires thread")
    fetch_last = max(last, 100)
    result = _fetch_impl(
        to=None,
        thread=thread,
        last=fetch_last,
        unread=False,
        mark_read=False,
        compact=False,
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
    if last > 0 and len(turns) > last:
        turns = turns[:last]
    turns = sorted(turns, key=lambda t: int(t.get("turn_number") or 0))
    reply_turns = [
        t["turn_number"]
        for t in raw
        if isinstance(t, dict)
        and t.get("subject") == "REPLY"
        and t.get("turn_number") is not None
    ]
    readiness = assess_lane_readiness([t for t in raw if isinstance(t, dict)])
    record("mcp.cursor_bridge.status", thread=thread, count=len(turns))
    return {
        "op": "status",
        "thread": thread,
        "turns": turns,
        "tab_ready": readiness["ready"],
        "tab_ready_turn": readiness.get("turn"),
        "tab_ready_reason": readiness.get("reason"),
        "tab_ready_age_s": readiness.get("tab_ready_age_s"),
        "tab_ready_ttl_s": readiness.get("tab_ready_ttl_s"),
        "bridge_open_turn": readiness.get("bridge_open_turn"),
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
        gui_host: str = ":8",
        agent_id: str | None = None,
        prompt: str | None = None,
        model: str | None = None,
        title: str | None = None,
        effort: str | None = None,
        fast: bool | str | None = None,
        model_knobs: dict[str, str] | None = None,
        work_key: str | None = None,
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
          send_msg (thread, body, after_turn?, gui_host?, agent_id?) — bus MSG plus,
            when agent_id is set, Glass paste into that composer (spawn_tab path).
          status   (thread, last=10) — {op, thread, turns[...], tab_ready,
            tab_ready_turn, tab_ready_reason, tab_ready_age_s, bridge_open_turn,
            last_reply_turn}. tab_ready is true only when a lane-scoped TAB_READY
            or TAB_ALIVE is within TTL after the latest BRIDGE_OPEN/TAB_GONE.

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
                return _send_msg(
                    thread=thread,
                    body=body,
                    after_turn=after_turn,
                    gui_host=gui_host,
                    agent_id=agent_id,
                )
            if op == "status":
                return _status(thread=thread, last=last)
            if op == "spawn_tab":
                return _spawn_tab(
                    gui_host=gui_host,
                    prompt=prompt,
                    model=model,
                    effort=effort,
                    fast=fast,
                    model_knobs=model_knobs,
                    agent_id=agent_id,
                )
            if op == "spawn_house":
                return _spawn_house(
                    gui_host=gui_host,
                    slug=slug,
                    prompt=prompt,
                    model=model,
                    effort=effort,
                    fast=fast,
                    model_knobs=model_knobs,
                    agent_id=agent_id,
                    work_key=work_key,
                )
            if op == "wake_tab":
                return _wake_tab(gui_host=gui_host, agent_id=agent_id, body=body)
            if op == "rename_tab":
                return _rename_tab(gui_host=gui_host, agent_id=agent_id, title=title)
            if op == "prune_sidebar":
                return _prune_sidebar(
                    gui_host=gui_host, thread=thread, agent_id=agent_id
                )
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
