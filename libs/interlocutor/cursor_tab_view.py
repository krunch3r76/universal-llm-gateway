"""Cursor Glass display read result shaping — no selection, typed state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cursor_bridge.glass_tab_inspect import inspect_glass_tab
from cursor_bridge.wake_delivery import normalize_target

from interlocutor.targets import DisplayEightTarget

_TURN_CHAR_CAP = 2000
_RUN_STATE_SOURCE = "glass.composerDataService"


def grok_on_cursor_display_reason(target: DisplayEightTarget) -> str | None:
    """Hint when grok-default target hits a Cursor-only display."""
    norm_cursor = normalize_target(target.display, "cursor")
    norm_grok = normalize_target(target.display, "grok")
    if not norm_cursor.get("ok") or norm_grok.get("ok"):
        return None
    aid = str(target.agent_id or "").strip()
    return (
        f"display:{target.display}/{aid}/cursor"
        f" — display {target.display} is a Cursor Glass display:"
        f" use display:{target.display}/{aid}/cursor"
    )


def _map_run_state_phase(run: dict[str, Any]) -> dict[str, Any]:
    if not run or run.get("ok") is False:
        return {
            "phase": "unknown",
            "source": _RUN_STATE_SOURCE,
            "status_raw": "",
            "reason": "run_state_probe_failed",
        }
    status_raw = str(run.get("status") or "")
    if not status_raw:
        return {
            "phase": "unknown",
            "source": _RUN_STATE_SOURCE,
            "status_raw": "",
            "reason": "empty_status",
        }
    if run.get("running"):
        return {
            "phase": "running",
            "source": _RUN_STATE_SOURCE,
            "status_raw": status_raw,
        }
    return {
        "phase": "idle",
        "source": _RUN_STATE_SOURCE,
        "status_raw": status_raw,
    }


def _cap_turn(raw: dict[str, Any]) -> dict[str, Any]:
    role = str(raw.get("role") or "").strip()
    text = str(raw.get("text") or "")
    if role not in {"user", "assistant"} or not text:
        return {}
    char_count = len(text)
    truncated = char_count > _TURN_CHAR_CAP
    if truncated:
        text = text[-_TURN_CHAR_CAP:]
    return {
        "role": role,
        "text": text,
        "truncated": truncated,
        "char_count": char_count,
    }


def _cap_text_list(messages: list[str]) -> list[str]:
    out: list[str] = []
    for msg in messages:
        s = str(msg or "")
        if len(s) > _TURN_CHAR_CAP:
            s = s[-_TURN_CHAR_CAP:]
        out.append(s)
    return out


def _shape_turns(surface: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], str]:
    turns_raw = list(surface.get("message_turns") or [])
    message_turns: list[dict[str, Any]] = []
    for raw in turns_raw:
        if not isinstance(raw, dict):
            continue
        shaped = _cap_turn(raw)
        if shaped:
            message_turns.append(shaped)
    message_turns = message_turns[-3:]
    recent = _cap_text_list([str(x) for x in surface.get("recent_messages") or []])
    recent = recent[-3:]
    latest = message_turns[-1]["text"] if message_turns else ""
    if not latest and recent:
        latest = recent[-1]
    return message_turns, recent, latest


def _with_display(target: DisplayEightTarget, payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "display": target.display}


def shape_cursor_inspect_success(
    target: DisplayEightTarget, raw: dict[str, Any]
) -> dict[str, Any]:
    surface = dict(raw.get("surface") or {})
    state = _map_run_state_phase(dict(raw.get("run_state_probe") or {}))
    phase = str(state.get("phase") or "unknown")
    target_fg = bool(raw.get("target_foreground"))
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    title = raw.get("target_title")
    active_title = surface.get("active_title") if target_fg else None
    active_aid = surface.get("active_agent_id") if target_fg else None
    composer_draft = str(surface.get("composer_draft") or "").strip()
    composer_has_draft: bool | None
    if target_fg:
        composer_has_draft = bool(composer_draft)
    else:
        composer_has_draft = None
    base: dict[str, Any] = {
        "ok": True,
        "adapter": "display_eight",
        "coverage": "latest",
        "endpoint": raw.get("endpoint"),
        "active_agent_id": active_aid,
        "active_title": active_title if target_fg else None,
        "selected": False,
        "target_foreground": target_fg,
        "target_title": title,
        "state": state,
        "generating": phase == "running",
        "observed_at": observed_at,
        "last_committed_at": None,
        "last_committed_at_basis": "no_source",
        "mailbox": None,
        "mailbox_basis": "not_resolved",
        "mailbox_authority": "agent_bus.subscriptions",
        "composer_has_draft": composer_has_draft,
    }
    if not target_fg:
        base.update(
            {
                "message_turns": [],
                "recent_messages": [],
                "latest_message": "",
                "latest_fingerprint": None,
                "empty_basis": "tab_not_foreground",
                "tail_includes_streaming": None,
            }
        )
        return _with_display(target, base)
    message_turns, recent, latest = _shape_turns(surface)
    tail_streaming: bool | None
    if phase == "running":
        tail_streaming = True
    elif phase == "idle":
        tail_streaming = False
    else:
        tail_streaming = None
    base.update(
        {
            "message_turns": message_turns,
            "recent_messages": recent,
            "latest_message": latest,
            "latest_fingerprint": str(surface.get("latest_fingerprint") or "") or None,
            "empty_basis": str(surface.get("empty_basis") or ""),
            "tail_includes_streaming": tail_streaming,
        }
    )
    return _with_display(target, base)


async def read_cursor_display_latest(target: DisplayEightTarget) -> dict[str, Any]:
    raw = await inspect_glass_tab(display=target.display, agent_id=target.agent_id)
    if not raw.get("ok"):
        payload = {
            "ok": False,
            "code": str(raw.get("code") or "unknown"),
            "reason": str(raw.get("reason") or raw.get("code") or "unknown"),
        }
        for key in ("rows_seen", "rows_capped"):
            if key in raw:
                payload[key] = raw[key]
        return _with_display(target, payload)
    return shape_cursor_inspect_success(target, raw)


def map_run_state_phase(run: dict[str, Any]) -> dict[str, Any]:
    """Public for unit tests (T7)."""
    return _map_run_state_phase(run)


__all__ = [
    "map_run_state_phase",
    "read_cursor_display_latest",
    "grok_on_cursor_display_reason",
    "shape_cursor_inspect_success",
]