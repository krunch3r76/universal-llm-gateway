"""Charter tick completion pager."""

from __future__ import annotations

import os

from pager_notify.client import notify_pager


def _tick_pager_enabled() -> bool:
    raw = os.environ.get("PAGER_NOTIFY_TICK", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


async def notify_tick_complete(
    *,
    roots: int,
    in_flight: int,
    admitted: int,
    skipped_by_reason: dict[str, int],
) -> bool:
    if not _tick_pager_enabled():
        return False
    if skipped_by_reason:
        top = ",".join(
            f"{k}:{v}"
            for k, v in list(skipped_by_reason.items())[:4]
        )
    else:
        top = "none"
    idle = max(roots - in_flight, 0)
    body = (
        f"tick en={roots} live={in_flight} idle={idle} "
        f"adm={admitted} skip={top}"
    )
    return await notify_pager("ULG charter tick", body, tag="charter-tick")
