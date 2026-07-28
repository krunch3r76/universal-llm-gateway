"""Charter tick completion pager."""

from __future__ import annotations

import os

from pager_notify.client import notify_pager

# Fi SMS body budget (email-bridge /pager/notify also truncates at 300).
SMS_BODY_MAX = 300


def _tick_pager_enabled() -> bool:
    raw = os.environ.get("PAGER_NOTIFY_TICK", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def format_closed_attribution(gid: str, executor_slug: str, root_id: str) -> str:
    """One harvest-close token: ``G3@cdp/opus-5@5975``."""
    return f"{gid}@{executor_slug}@{root_id}"


def format_tick_sms_body(
    *,
    roots: int,
    in_flight: int,
    admitted: int,
    skipped_by_reason: dict[str, int],
    closed_attributions: list[str] | None = None,
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
    body = (
        f"tick en={roots} live={in_flight} idle={idle} "
        f"adm={admitted} skip={top}"
    )
    if closed_attributions:
        body = f"{body} closed={','.join(closed_attributions)}"
    if max_chars > 0 and len(body) > max_chars:
        return body[:max_chars]
    return body


async def notify_tick_complete(
    *,
    roots: int,
    in_flight: int,
    admitted: int,
    skipped_by_reason: dict[str, int],
    closed_attributions: list[str] | None = None,
) -> bool:
    if not _tick_pager_enabled():
        return False
    body = format_tick_sms_body(
        roots=roots,
        in_flight=in_flight,
        admitted=admitted,
        skipped_by_reason=skipped_by_reason,
        closed_attributions=closed_attributions,
    )
    return await notify_pager("ULG charter tick", body, tag="charter-tick")
