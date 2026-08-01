"""Auto mission-debrief pager after durable MISSION_CLOSEOUT bus posts."""

from __future__ import annotations

import re
from typing import Any, Callable

from pager_notify.life_notify import deliver_pager_notify
from pager_notify.so_what import SMS_BODY_MAX, SMS_SUBJECT_MAX, clip, extract_so_what_from_body

from claude_bundles.mission_close_wake import (
    BEYOND_HEADING,
    BEYOND_NOTIFY_PREFIX,
    format_beyond_notify_line,
    validate_mission_debrief_notify,
)

_MISSION_DEBRIEF_TAG = "mission-debrief"
_LAYMAN_MAX = 800
_TYPE_LINE_RE = re.compile(r"(?im)^TYPE:\s*.*$")
_LANE_LINE_RE = re.compile(r"(?im)^lane:\s*.*$")
_HEADING_LINE_RE = re.compile(r"(?im)^##\s+.*$")


def _default_record(name: str, **kwargs: Any) -> None:
    """Lazy import — ``mcp_events`` is MCP-process-path only."""
    from mcp_events import record

    record(name, **kwargs)


def _strip_beyond_section(body: str) -> str:
    pattern = re.compile(
        rf"(?im)^{re.escape(BEYOND_HEADING)}\s*\n.*?(?=^##\s|\Z)",
        re.DOTALL,
    )
    return pattern.sub("", body or "")


def _compress_closeout_prose(body: str) -> str:
    """Layman outcome line — strip TYPE/inventory noise, keep one clear outcome."""
    so_what = extract_so_what_from_body(body)
    if so_what:
        return clip(so_what, _LAYMAN_MAX)

    lines: list[str] = []
    for raw in _strip_beyond_section(body).splitlines():
        line = raw.strip()
        if not line or line.startswith("<!--"):
            continue
        if _TYPE_LINE_RE.match(line) or _LANE_LINE_RE.match(line):
            continue
        if _HEADING_LINE_RE.match(line):
            continue
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        if line:
            lines.append(line)

    prose = clip(" ".join(" ".join(lines).split()), _LAYMAN_MAX)
    return prose or "Mission closed."


def compose_mission_debrief_from_closeout(
    *,
    subject: str,
    body: str,
    thread_id: str,
) -> dict[str, str]:
    """Build pager-ready mission debrief fields from a closeout turn."""
    del subject  # awareness subject is synthesized; closeout subject may say CLOSEOUT
    thread = (thread_id or "").strip() or "unknown"
    debrief_subject = clip(f"Mission debrief — bus:{thread}", SMS_SUBJECT_MAX)
    layman = _compress_closeout_prose(body)
    beyond_payload = format_beyond_notify_line(body)
    if beyond_payload is None:
        pager_body = clip(layman, SMS_BODY_MAX - 40)
    else:
        beyond_line = f"{BEYOND_NOTIFY_PREFIX} {beyond_payload}"
        reserve = len(beyond_line) + 1 + 40
        layman_clipped = clip(layman, max(SMS_BODY_MAX - reserve, 80))
        pager_body = f"{layman_clipped}\n{beyond_line}"
        if len(pager_body) > SMS_BODY_MAX:
            pager_body = pager_body[: SMS_BODY_MAX - 1] + "…"
    return {
        "subject": debrief_subject,
        "body": pager_body,
        "tag": _MISSION_DEBRIEF_TAG,
    }


def deliver_mission_debrief_auto(
    *,
    closeout_subject: str,
    closeout_body: str,
    thread_id: str,
    from_agent: str,
    record_fn: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Compose, validate, and page a mission debrief after durable closeout."""
    emit = record_fn if record_fn is not None else _default_record
    composed = compose_mission_debrief_from_closeout(
        subject=closeout_subject,
        body=closeout_body,
        thread_id=thread_id,
    )
    verdict = validate_mission_debrief_notify(
        subject=composed["subject"],
        body=composed["body"],
        tag=composed["tag"],
    )
    if not verdict.ok:
        reason = verdict.reason or "mission_debrief_beyond_missing"
        emit(
            "mcp.agentbus.mission_debrief.failed",
            from_agent=from_agent,
            thread=thread_id,
            reason=reason,
            tag=composed["tag"],
        )
        return {
            "status": "rejected",
            "reason": reason,
            "missed_tokens": list(verdict.missed_tokens),
        }

    delivered = deliver_pager_notify(
        subject=composed["subject"],
        body=composed["body"],
        tag=composed["tag"],
        ref=f"agent-bus:{thread_id}",
        from_agent=from_agent,
        sent_event="mcp.agentbus.mission_debrief.sent",
        failed_event="mcp.agentbus.mission_debrief.failed",
        record_fn=emit,
    )
    return {
        "status": delivered["status"],
        "stamped_at": delivered.get("stamped_at"),
        "reason": delivered.get("reason"),
        "error": delivered.get("error"),
        "ref": delivered.get("ref"),
    }


def attach_mission_debrief_notify(
    result: dict[str, Any],
    *,
    subject: str,
    body: str,
    thread_id: str,
    from_agent: str,
) -> dict[str, Any]:
    """After successful post, attach auto debrief outcome when closeout."""
    from claude_bundles.mission_close_wake import is_mission_closeout

    if not is_mission_closeout(subject=subject, body=body):
        return result
    result["mission_debrief_notify"] = deliver_mission_debrief_auto(
        closeout_subject=subject,
        closeout_body=body,
        thread_id=thread_id,
        from_agent=from_agent,
    )
    return result


__all__ = [
    "attach_mission_debrief_notify",
    "compose_mission_debrief_from_closeout",
    "deliver_mission_debrief_auto",
]
