"""Charter tick completion pager."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from pager_notify.client import notify_pager
from pager_notify.so_what import (
    SMS_BODY_MAX,
    SMS_SUBJECT_MAX,
    clip,
    standing_skip_signature,
    tick_should_page,
)
from pager_notify.state import claim_tick_standing_page

# Fi SMS body budget (email-bridge /pager/notify also truncates at 300).


def _tick_pager_enabled() -> bool:
    raw = os.environ.get("PAGER_NOTIFY_TICK", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class ClosedAttribution:
    """One harvested charter window — provenance for tick SMS."""

    gid: str
    executor_slug: str
    root_id: str
    thread_slug: str = ""
    task_hint: str = ""
    source_ref: str = ""
    checkpoint_subject: str = ""
    window_index: int = 0
    so_what: str = ""

    def compact_token(self) -> str:
        """Machine token kept for logs / backward-compatible grep."""
        return format_closed_attribution(self.gid, self.executor_slug, self.root_id)


def format_closed_attribution(gid: str, executor_slug: str, root_id: str) -> str:
    """One harvest-close token: ``G3@cdp/opus-5@5975``."""
    return f"{gid}@{executor_slug}@{root_id}"


def _short_executor(slug: str) -> str:
    """Drop redundant ``cursor/`` prefix for SMS brevity."""
    s = (slug or "").strip()
    if s.startswith("cursor/"):
        return s.removeprefix("cursor/")
    return s


def format_closed_human(attribution: ClosedAttribution) -> str:
    """Layman close line: so-what or gated step + charter title + executor."""
    if attribution.so_what:
        parts: list[str] = [
            clip(attribution.so_what, 72),
            f"{attribution.gid}@{attribution.root_id}",
        ]
        parts.append(f"via {_short_executor(attribution.executor_slug)}")
        return " · ".join(parts)
    parts = [f"{attribution.gid} done"]
    title = attribution.thread_slug or f"root-{attribution.root_id}"
    parts.append(clip(title, 36))
    parts.append(f"#{attribution.root_id}")
    parts.append(f"via {_short_executor(attribution.executor_slug)}")
    if attribution.task_hint:
        parts.append(clip(attribution.task_hint, 48))
    elif attribution.source_ref:
        parts.append(clip(attribution.source_ref, 40))
    elif attribution.checkpoint_subject:
        parts.append(clip(attribution.checkpoint_subject, 40))
    return " · ".join(parts)


def format_tick_subject(
    *,
    roots: int,
    in_flight: int,
    closed: list[ClosedAttribution] | None = None,
) -> str:
    """SMS subject — so-what / gate done headline; else conveyor snapshot."""
    idle = max(roots - in_flight, 0)
    if closed:
        first = closed[0]
        if first.so_what:
            headline = f"{clip(first.so_what, 70)} ({first.gid}#{first.root_id})"
        else:
            slug = first.thread_slug or f"root-{first.root_id}"
            headline = f"{first.gid} done — {clip(slug, 50)} (#{first.root_id})"
        if len(closed) > 1:
            headline = f"{headline} +{len(closed) - 1}"
        return headline[:SMS_SUBJECT_MAX]
    return f"Charter tick · {roots} enrolled · {idle} idle"[:SMS_SUBJECT_MAX]


def format_tick_sms_body(
    *,
    roots: int,
    in_flight: int,
    admitted: int,
    skipped_by_reason: dict[str, int],
    closed_attributions: list[ClosedAttribution | str] | None = None,
    max_chars: int = SMS_BODY_MAX,
) -> str:
    """Build the tick SMS body; truncates to ``max_chars`` (Fi budget)."""
    if skipped_by_reason:
        top = ",".join(
            f"{k}:{v}" for k, v in list(skipped_by_reason.items())[:4]
        )
    else:
        top = "none"
    idle = max(roots - in_flight, 0)
    summary = (
        f"conveyor en={roots} live={in_flight} idle={idle} "
        f"adm={admitted} skip={top}"
    )

    closed_lines: list[str] = []
    if closed_attributions:
        for item in closed_attributions:
            if isinstance(item, ClosedAttribution):
                closed_lines.append(format_closed_human(item))
            else:
                closed_lines.append(f"closed {clip(str(item), 80)}")

    if not closed_lines:
        body = summary
    else:
        body = " | ".join(closed_lines) + " || " + summary

    if max_chars > 0 and len(body) > max_chars:
        if closed_lines:
            # Preserve summary tail; shrink close prose first.
            suffix = " || " + summary
            budget = max_chars - len(suffix)
            if budget > 20:
                trimmed = clip(" | ".join(closed_lines), budget)
                return trimmed + suffix
        return body[:max_chars]
    return body


async def notify_tick_complete(
    *,
    roots: int,
    in_flight: int,
    admitted: int,
    skipped_by_reason: dict[str, int],
    closed_attributions: list[ClosedAttribution | str] | None = None,
) -> bool:
    if not _tick_pager_enabled():
        return False
    closed_only = [
        a
        for a in (closed_attributions or [])
        if isinstance(a, ClosedAttribution)
    ] or None
    closed_count = len(closed_only or [])
    if not tick_should_page(
        admitted=admitted,
        closed_count=closed_count,
        skipped_by_reason=skipped_by_reason,
    ):
        return False
    # Standing stops (blocked / stopped:*) otherwise SMS every tick interval.
    if admitted <= 0 and closed_count <= 0:
        standing_sig = standing_skip_signature(skipped_by_reason)
        if standing_sig is not None and not claim_tick_standing_page(standing_sig):
            return False
    subject = format_tick_subject(
        roots=roots,
        in_flight=in_flight,
        closed=closed_only,
    )
    body = format_tick_sms_body(
        roots=roots,
        in_flight=in_flight,
        admitted=admitted,
        skipped_by_reason=skipped_by_reason,
        closed_attributions=closed_attributions,
    )
    return await notify_pager(subject, body, tag="charter-tick")


def task_hint_from_next_pickup(
    next_pickup: list[str],
    gid: str,
    *,
    source_ref: str | None = None,
) -> str:
    """First human task phrase for ``gid`` from CHECKPOINT Next-pickup rows."""
    gid_re = re.compile(rf"\b{re.escape(gid)}\b")
    for item in next_pickup:
        if not gid_re.search(item):
            continue
        hint = gid_re.sub("", item, count=1)
        hint = re.sub(r"^[—–\-·|:\s]+", "", hint)
        hint = re.sub(r"\bexecutor_lane:\s*\w+\b", "", hint, flags=re.IGNORECASE)
        hint = re.sub(r"\bdetent=\w+\b", "", hint, flags=re.IGNORECASE)
        hint = re.sub(r"\bdensity=\w+\b", "", hint, flags=re.IGNORECASE)
        hint = " ".join(hint.split())
        if hint:
            return clip(hint, 56)
    if source_ref:
        ref = source_ref.split(":", 1)[-1]
        return clip(ref.replace("-", " "), 40)
    return ""
