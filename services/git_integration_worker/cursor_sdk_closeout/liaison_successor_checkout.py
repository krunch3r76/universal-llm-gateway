"""Refuse a liaison successor hop whose closeout shows a checkout commit.

The wake doorbell already tells that hop not to land repo edits. That sentence
is not a control: Composer can still commit. This gate runs after the closeout
body exists, so the refusal is attached to evidence the hop already recorded.

Admit-time ``read_only`` for every ``contract=none`` is a different door
(``todo:prompt-expand-none-admit``). ``contract=implement`` and
``contract=conductor`` stay able to land.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

SUCCESSOR_CALLER = "liaison-ticker"
REFUSAL_REASON = "liaison_successor_checkout_commit"


def _measured_count(value: object) -> int:
    """Parse a closeout commit gauge. Absent or non-numeric is zero."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return 0


def closeout_shows_checkout_commit(closeout: Mapping[str, Any]) -> bool:
    """True when the closeout records a commit on the shared checkout.

    ``landed`` true means the hop's tip is on local master. A measured
    ``commits_ahead`` on a non-isolated tree is the same fact: that tree is
    the checkout. An isolated lane-B branch commit that has not landed stays
    off the checkout and does not trip this predicate.
    """
    if closeout.get("landed") is True:
        return True
    ahead = _measured_count(closeout.get("commits_ahead"))
    if ahead < 1:
        ahead = _measured_count(closeout.get("commits_ahead_unfiltered"))
    if ahead < 1:
        return False
    lane = str(closeout.get("lane") or "").upper()
    if lane == "B" and closeout.get("isolation_materialized") is not False:
        return False
    return True


def liaison_successor_checkout_refusal(
    *,
    caller_agent: str | None,
    contract: str | None,
    closeout: Mapping[str, Any],
) -> str | None:
    """Return the refusal token, or ``None`` when this hop may stand.

    Only ``caller_agent=liaison-ticker`` with ``contract=none`` is in scope.
    Implement and conductor lands return ``None`` even when the closeout
    shows a checkout commit.
    """
    if str(caller_agent or "").strip() != SUCCESSOR_CALLER:
        return None
    kind = str(contract or "none").strip().lower() or "none"
    if kind != "none":
        return None
    if not closeout_shows_checkout_commit(closeout):
        return None
    return REFUSAL_REASON


def apply_liaison_successor_checkout_refusal(
    body: str,
    *,
    caller_agent: str | None,
    contract: str | None,
) -> tuple[str, str | None]:
    """Rewrite a closeout body when the successor hop must be refused.

    Returns the original body and ``None`` when the hop stands, including
    when the body is not a JSON object. A refusal sets ``status=failed`` so
    the published closeout is the refusal, not a successful land.
    """
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return body, None
    if not isinstance(payload, dict):
        return body, None
    reason = liaison_successor_checkout_refusal(
        caller_agent=caller_agent,
        contract=contract,
        closeout=payload,
    )
    if reason is None:
        return body, None
    payload["status"] = "failed"
    payload["degraded_reason"] = reason
    payload["work_outcome"] = "not_shipped"
    deviations = [
        item for item in (payload.get("deviations") or []) if isinstance(item, str)
    ]
    token = f"refuse:{reason}"
    if token not in deviations:
        deviations.append(token)
    payload["deviations"] = deviations
    summary = payload.get("summary")
    if isinstance(summary, str) and reason not in summary:
        payload["summary"] = f"{summary} (refused: {reason})"
    return json.dumps(payload, separators=(",", ":")), reason
