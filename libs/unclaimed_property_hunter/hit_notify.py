"""Pager delivery for hunter outcomes — amount floor suppresses micro-hit noise.

Nine of nineteen adjudicated family hits are under $6.00 (assertion 29267).
Paging every new row would train the operator to ignore $0.17 alerts. Floor:
page when parsed amount >= PAGE_AMOUNT_FLOOR USD, or holder is Prudential,
or infrastructure signals (check_failed streak). Sub-floor new hits are
digested into run notes only.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from unclaimed_property_hunter.diff_runs import RunDiff
from unclaimed_property_hunter.models import Hit, RunRecord

PAGE_AMOUNT_FLOOR = 6.00
CHECK_FAILED_PAGE_THRESHOLD = 3
PAGE_TAG = "unclaimed-hit"
DIGEST_TAG = "unclaimed-hit-digest"
INFRA_TAG = "unclaimed-hunter-infra"

_AMOUNT_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)")


@dataclass(frozen=True)
class NotifyDecision:
    """Which hits page vs digest vs skip for one extract run."""

    page_hits: tuple[Hit, ...]
    digest_hits: tuple[Hit, ...]
    reason: str


def parse_amount_usd(amount_or_range: str) -> float | None:
    """Best-effort parse of SCO CURRENT_CASH_BALANCE / amount strings."""
    text = (amount_or_range or "").strip().replace("$", "").replace(",", "")
    if not text:
        return None
    match = _AMOUNT_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def classify_new_hit(hit: Hit) -> str:
    """Return ``page`` | ``digest`` for a newly seen property row."""
    if hit.is_prudential():
        return "page"
    amount = parse_amount_usd(hit.amount_or_range)
    if amount is not None and amount >= PAGE_AMOUNT_FLOOR:
        return "page"
    return "digest"


def decide_notifications(
    record: RunRecord,
    diff: RunDiff | None,
    *,
    consecutive_check_failed: int = 0,
) -> NotifyDecision:
    """Choose page vs digest vs skip from diff + infrastructure streak."""
    if consecutive_check_failed >= CHECK_FAILED_PAGE_THRESHOLD:
        return NotifyDecision(
            page_hits=(),
            digest_hits=(),
            reason=f"check_failed_streak={consecutive_check_failed}",
        )
    if diff is None:
        return NotifyDecision(page_hits=(), digest_hits=(), reason="no_prior_run")
    new_hits = [h for h in record.hits if h.property_id in diff.added]
    if not new_hits:
        return NotifyDecision(page_hits=(), digest_hits=(), reason="no_new_hits")
    page: list[Hit] = []
    digest: list[Hit] = []
    for hit in new_hits:
        if classify_new_hit(hit) == "page":
            page.append(hit)
        else:
            digest.append(hit)
    return NotifyDecision(
        page_hits=tuple(page),
        digest_hits=tuple(digest),
        reason="new_hit_diff",
    )


def decide_check_failed_notification(
    *,
    failure_reason: str,
    consecutive_check_failed: int,
) -> NotifyDecision:
    """Infrastructure notify for header drift or check_failed streak threshold."""
    if failure_reason.startswith("header_drift:"):
        return NotifyDecision(
            page_hits=(),
            digest_hits=(),
            reason="header_drift",
        )
    if consecutive_check_failed >= CHECK_FAILED_PAGE_THRESHOLD:
        return NotifyDecision(
            page_hits=(),
            digest_hits=(),
            reason=f"check_failed_streak={consecutive_check_failed}",
        )
    return NotifyDecision(page_hits=(), digest_hits=(), reason="check_failed_no_page")


def decide_roster_empty_notification() -> NotifyDecision:
    """One-shot pager when scheduled extract has no roster subjects."""
    return NotifyDecision(page_hits=(), digest_hits=(), reason="roster_empty")


def format_page_body(record: RunRecord, hit: Hit) -> str:
    """Operational cash-alert body — not a ULG growth-map page."""
    amount = hit.amount_or_range or "unknown"
    tier = "prudential" if hit.is_prudential() else "amount_floor"
    return (
        f"CA SCO hit {hit.property_id} holder={hit.holder} "
        f"owner={hit.owner_name} amount={amount} run={record.run_id} tier={tier}"
    )


def format_digest_note(digest_hits: tuple[Hit, ...]) -> str:
    """Persist-only digest for sub-floor new hits."""
    if not digest_hits:
        return ""
    parts = [
        f"{h.property_id}(${h.amount_or_range or '?'})" for h in digest_hits[:8]
    ]
    suffix = f" +{len(digest_hits) - 8} more" if len(digest_hits) > 8 else ""
    return f"notify_digest sub_floor={len(digest_hits)} [{', '.join(parts)}{suffix}]"


async def notify_hit_pages(
    record: RunRecord,
    decision: NotifyDecision,
) -> dict[str, Any]:
    """Fire pager for each page-worthy hit; return per-hit notify outcomes."""
    from pager_notify.client import NotifyResult, notify_pager

    outcomes: list[dict[str, Any]] = []
    for hit in decision.page_hits:
        subject = f"CA SCO ${hit.amount_or_range or '?'} {hit.property_id}"[:60]
        body = format_page_body(record, hit)
        result: NotifyResult = await notify_pager(subject, body, tag=PAGE_TAG)
        outcomes.append(
            {
                "property_id": hit.property_id,
                "status": result.status,
                "reason": result.reason,
                "error": result.error,
                "subject": subject,
                "body": body,
                "tag": PAGE_TAG,
            }
        )
    return {"decision_reason": decision.reason, "pages": outcomes}


def notify_hit_pages_sync(record: RunRecord, decision: NotifyDecision) -> dict[str, Any]:
    """Sync wrapper for CLI / systemd oneshot callers."""
    return asyncio.run(notify_hit_pages(record, decision))


async def notify_infrastructure(
    record: RunRecord,
    decision: NotifyDecision,
) -> dict[str, Any]:
    """Fire pager for roster_empty, header drift, or check_failed streak."""
    from pager_notify.client import NotifyResult, notify_pager

    reason = decision.reason
    if reason == "roster_empty":
        subject = "CA SCO hunter roster_empty"
        body = (
            f"Scheduled extract skipped: roster_empty run={record.run_id} "
            f"surname={record.query.surname}"
        )
    elif reason == "header_drift":
        subject = "CA SCO hunter header_drift"
        body = (
            f"Bulk CSV header drift run={record.run_id} "
            f"reason={record.check_failure_reason or reason}"
        )
    elif reason.startswith("check_failed_streak="):
        subject = "CA SCO hunter check_failed streak"
        body = (
            f"check_failed streak run={record.run_id} surname={record.query.surname} "
            f"{reason} last_reason={record.check_failure_reason!r}"
        )
    else:
        return {"decision_reason": reason, "pages": []}
    result: NotifyResult = await notify_pager(subject, body, tag=INFRA_TAG)
    return {
        "decision_reason": reason,
        "pages": [
            {
                "status": result.status,
                "reason": result.reason,
                "error": result.error,
                "subject": subject,
                "body": body,
                "tag": INFRA_TAG,
            }
        ],
    }


def notify_infrastructure_sync(record: RunRecord, decision: NotifyDecision) -> dict[str, Any]:
    return asyncio.run(notify_infrastructure(record, decision))


async def probe_pager_from_service_context(*, skip_peer_wait: bool = False) -> dict[str, Any]:
    """Fire one real pager POST — used to prove email-bridge reachability."""
    from pager_notify.client import notify_pager

    result = await notify_pager(
        "CA SCO hunter probe",
        "unclaimed-property-hunter systemd execution-context probe",
        tag="unclaimed-hunter-probe",
        wait_for_peer=not skip_peer_wait,
    )
    return {
        "status": result.status,
        "reason": result.reason,
        "error": result.error,
        "tag": "unclaimed-hunter-probe",
    }


def probe_pager_from_service_context_sync(*, skip_peer_wait: bool = False) -> dict[str, Any]:
    return asyncio.run(probe_pager_from_service_context(skip_peer_wait=skip_peer_wait))
