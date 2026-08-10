"""Auto mission-debrief pager after durable MISSION_CLOSEOUT bus posts.

Composes the growth-map pager (vision → look-back → architecture → look-ahead
→ Beyond) from closeout slots. Hollow closeouts (no Vision/Architecture, no
named ULG systems) are **rejected** — fail closed rather than SMS a status telegram.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from pager_notify.life_notify import deliver_pager_notify
from pager_notify.mission_page import (
    AwarenessSlots,
    extract_awareness_slots,
    format_mission_awareness_page,
    named_ulg_systems,
)
from pager_notify.so_what import SMS_SUBJECT_MAX, clip

from claude_bundles.mission_close_wake import (
    MissionCloseWakeVerdict,
    format_beyond_notify_line,
    validate_mission_debrief_notify,
)

_MISSION_DEBRIEF_TAG = "mission-debrief"


def _default_record(name: str, **kwargs: Any) -> None:
    """Lazy import — ``mcp_events`` is MCP-process-path only."""
    from mcp_events import record

    record(name, **kwargs)


def _beyond_bullets_from_closeout(body: str) -> list[str]:
    payload = format_beyond_notify_line(body)
    if payload is None:
        return []
    if payload.casefold() == "none":
        return ["none"]
    return [part.strip() for part in payload.split(" · ") if part.strip()]


_VISION_OPENER_SPLIT_RE = re.compile(
    r"(?im)^(?:Vision|##\s*Vision|Looking back|Architecture|##\s*Architecture|"
    r"Looking ahead|Beyond this close|##\s*Work beyond)"
)
_FRONTMATTER_RE = re.compile(r"(?i)^(type|arc|lane|so_what|status):\s*")


def _resolve_closeout_vision(body: str, slots: AwarenessSlots) -> str:
    """Vision label, or opening closeout prose before growth-map labels."""
    if slots.vision.strip():
        return slots.vision.strip()
    opener = _VISION_OPENER_SPLIT_RE.split(body or "", maxsplit=1)[0]
    lines: list[str] = []
    for raw in opener.splitlines():
        line = raw.strip()
        if not line or _FRONTMATTER_RE.match(line):
            continue
        lines.append(line)
    return " ".join(" ".join(lines).split())


def _resolve_closeout_architecture(body: str, slots: AwarenessSlots) -> str:
    if slots.architecture.strip():
        return slots.architecture.strip()
    return ""


def check_closeout_growth_map_gaps(body: str) -> MissionCloseWakeVerdict | None:
    """Refuse when closeout slots are missing — inspect the turn body, not a stub."""
    slots = extract_awareness_slots(body)
    vision = _resolve_closeout_vision(body, slots)
    architecture = _resolve_closeout_architecture(body, slots)
    missed: list[str] = []
    if len(vision) < 40:
        missed.append("Vision: <fleet gap this work closes>")
    if not architecture:
        missed.append("Architecture: <named ULG systems>")
    elif not named_ulg_systems(f"{architecture}\n{vision}\n{body}"):
        missed.append(
            "name concrete ULG systems (e.g. CSE Session Registry, "
            "project_ask, cdp-registry, agent-bus, cortex)"
        )
    if not missed:
        return None
    if len(missed) == 1:
        token = missed[0]
        if token.startswith("Vision:"):
            reason = "mission_debrief_vision_missing"
        elif token.startswith("Architecture:"):
            reason = "mission_debrief_architecture_missing"
        else:
            reason = "mission_debrief_systems_unnamed"
    else:
        reason = "mission_debrief_closeout_slots_missing"
    return MissionCloseWakeVerdict(
        ok=False,
        reason=reason,
        missed_tokens=tuple(missed),
    )


def _fallback_looking_ahead(beyond_bullets: list[str]) -> str:
    if not beyond_bullets or beyond_bullets == ["none"]:
        return "Nothing further is owed from this close."
    first = beyond_bullets[0]
    return f"Next concrete move: {first}"


def compose_mission_debrief_from_closeout(
    *,
    subject: str,
    body: str,
    thread_id: str,
) -> dict[str, str]:
    """Build growth-map pager fields from a closeout turn.

    Requires Vision + Architecture naming concrete ULG systems. Missing slots
    are refused in ``deliver_mission_debrief_auto`` before validate-on-stub.
    """
    del subject  # awareness subject is synthesized; closeout subject may say CLOSEOUT
    thread = (thread_id or "").strip() or "unknown"
    slots = extract_awareness_slots(body)
    beyond_bullets = _beyond_bullets_from_closeout(body)

    vision = _resolve_closeout_vision(body, slots)
    architecture = _resolve_closeout_architecture(body, slots)
    looking_back = slots.looking_back.strip() or (
        "Episode closed; the Architecture line is what landed in the fleet."
    )
    looking_ahead = slots.looking_ahead.strip() or _fallback_looking_ahead(
        beyond_bullets
    )

    systems = named_ulg_systems(f"{architecture}\n{vision}\n{body}")
    so_what = slots.so_what
    if so_what:
        debrief_subject = clip(so_what, SMS_SUBJECT_MAX)
    elif systems:
        debrief_subject = clip(
            f"ULG grew — {', '.join(systems[:3])}",
            SMS_SUBJECT_MAX,
        )
    else:
        debrief_subject = clip(
            f"ULG mission debrief — name systems (bus:{thread})",
            SMS_SUBJECT_MAX,
        )

    # Fail closed: do not invent Architecture/Vision. Incomplete closeouts
    # are refused in deliver_mission_debrief_auto before validate-on-stub.
    if len(vision) < 40 or not architecture:
        beyond_line = (
            f"Beyond this close: {beyond_bullets[0]}"
            if beyond_bullets
            else ""
        )
        stub = "Closeout omitted Vision/Architecture growth-map slots."
        if beyond_line:
            stub = f"{stub}\n\n{beyond_line}"
        return {
            "subject": debrief_subject,
            "body": stub,
            "tag": _MISSION_DEBRIEF_TAG,
        }

    # Do not invent ``Beyond this close: none`` when the closeout omitted the
    # section — leave it absent so validation returns beyond_missing.
    if not beyond_bullets:
        _subject, pager_body, tag = format_mission_awareness_page(
            subject=debrief_subject,
            vision=vision,
            looking_back=looking_back,
            architecture=architecture,
            looking_ahead=looking_ahead,
            beyond_bullets=["none"],
            tag=_MISSION_DEBRIEF_TAG,
        )
        # Strip the invented Beyond block.
        pager_body = re.sub(
            r"(?im)\n*Beyond this close:.*\Z",
            "",
            pager_body,
            flags=re.DOTALL,
        ).rstrip()
        return {
            "subject": _subject,
            "body": pager_body,
            "tag": tag,
        }

    _subject, pager_body, tag = format_mission_awareness_page(
        subject=debrief_subject,
        vision=vision,
        looking_back=looking_back,
        architecture=architecture,
        looking_ahead=looking_ahead,
        beyond_bullets=beyond_bullets,
        tag=_MISSION_DEBRIEF_TAG,
    )
    return {
        "subject": _subject,
        "body": pager_body,
        "tag": tag,
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
    gap = check_closeout_growth_map_gaps(closeout_body)
    if gap is not None:
        reason = gap.reason or "mission_debrief_closeout_slots_missing"
        emit(
            "mcp.agentbus.mission_debrief.failed",
            from_agent=from_agent,
            thread=thread_id,
            reason=reason,
            tag=_MISSION_DEBRIEF_TAG,
            missed_tokens=list(gap.missed_tokens),
        )
        return {
            "status": "rejected",
            "reason": reason,
            "missed_tokens": list(gap.missed_tokens),
        }
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
            missed_tokens=list(verdict.missed_tokens),
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
    "check_closeout_growth_map_gaps",
    "compose_mission_debrief_from_closeout",
    "deliver_mission_debrief_auto",
]
