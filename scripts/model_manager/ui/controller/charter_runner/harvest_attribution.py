"""SMS close-attribution helpers for harvested charter windows."""

from __future__ import annotations

import re

from pager_notify.tick import ClosedAttribution, task_hint_from_next_pickup

from .checkpoint_schema import parse_checkpoint

_GATED_ID_RE = re.compile(r"\b([GR]\d+[a-z]?)\b")
_CONSULT_ROLE_SNIFF_RE = re.compile(
    r"consult_role:\s*(r_admit|judgment_gap)\b", re.IGNORECASE
)


def executor_slug_for_sms(
    admission_mode: str,
    *,
    executor_lane: str | None,
    consult_role: str | None,
) -> str:
    """Map admit mode + checkpoint lane to the SMS executor slug."""
    del consult_role  # both consult roles host cdp/opus-5 as reviewer
    mode = (admission_mode or "generate").strip().lower()
    if mode == "consult":
        return "cdp/opus-5"
    if mode == "handoff":
        return "cursor"
    if (executor_lane or "").strip().lower() == "implement":
        return "cursor/composer-2.5"
    return "cursor/grok-4.6"


def consult_role_from_pickup(next_pickup: list[str]) -> str | None:
    """Sniff consult_role from Next-pickup when parse leaves it unset."""
    for item in next_pickup:
        m = _CONSULT_ROLE_SNIFF_RE.search(item)
        if m:
            return m.group(1).lower()
        if re.search(r"\bR-admit\b", item, re.IGNORECASE):
            return "r_admit"
    return None


def attribution_for_harvested_window(
    *,
    root_id: str,
    consumed_checkpoint_body: str,
    admission_mode: str,
    thread_slug: str = "",
    completing_subject: str = "",
    window_index: int = 0,
    so_what: str = "",
) -> ClosedAttribution | None:
    """Build harvest-close provenance from the CHECKPOINT the window consumed."""
    parsed = parse_checkpoint(consumed_checkpoint_body)
    gid: str | None = None
    for item in parsed.next_pickup:
        m = _GATED_ID_RE.search(item)
        if m:
            gid = m.group(1)
            break
    if not gid:
        return None
    consult_role = parsed.consult_role or consult_role_from_pickup(parsed.next_pickup)
    executor_slug = executor_slug_for_sms(
        admission_mode,
        executor_lane=parsed.executor_lane,
        consult_role=consult_role,
    )
    return ClosedAttribution(
        gid=gid,
        executor_slug=executor_slug,
        root_id=root_id,
        thread_slug=thread_slug,
        task_hint=task_hint_from_next_pickup(
            parsed.next_pickup,
            gid,
            source_ref=parsed.source_ref,
        ),
        source_ref=parsed.source_ref or "",
        checkpoint_subject=completing_subject,
        window_index=window_index,
        so_what=so_what,
    )


__all__ = [
    "attribution_for_harvested_window",
    "consult_role_from_pickup",
    "executor_slug_for_sms",
]
