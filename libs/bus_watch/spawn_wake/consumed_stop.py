"""A finished hire's one-shot stop slot must not swallow the next play.

The slot is claimed once. When that admit never becomes a dispatch row, every
later play of the same roster row fails the claim and the tick records a hold
with no body. The conductor still has to read the closeout; this only drops
the dead stop id so a fresh play can be posted.
"""

from __future__ import annotations

from typing import Any


def release_consumed_stop(
    body: dict[str, Any],
    stop_id: str,
    unstarted_claims: list[Any],
) -> bool:
    """Drop ``_stop_id`` when this stop's admit never became a dispatch.

    Returns True when the body may be posted as a fresh play.
    """
    sid = str(stop_id or "").strip()
    if not sid:
        return False
    for row in unstarted_claims:
        if isinstance(row, dict) and str(row.get("stop_id") or "") == sid:
            body.pop("_stop_id", None)
            return True
    return False
