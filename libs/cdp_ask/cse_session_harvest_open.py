"""Open a CSE URL for harvest when no lane is attached — no paste, no submit."""

from __future__ import annotations

import contextlib
from typing import Any

from claude_bundles import cdp_registry

from cdp_ask.cse_session_models import HarvestRequest, HarvestResponse
from cdp_ask.followup_dormant import park_relaunched_host
from cdp_ask.followup_reattach import _teardown_attempt, ensure_cse_attached

HARVEST_HOLDER = "cse-session-harvest"


async def _teardown_opened(outcome) -> None:
    """Park a woken seat or drop a minted/borrowed tab after scrape."""
    if outcome is None or not outcome.ok:
        return
    if outcome.relaunched:
        await park_relaunched_host(outcome)
        return
    if outcome.lane_created:
        await _teardown_attempt(outcome.page, outcome.pw, close_page=True)
        with contextlib.suppress(Exception):
            cdp_registry.deregister_lane(outcome.registration_id or "")
        return
    await _teardown_attempt(outcome.page, outcome.pw)


def _with_opened(provenance: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(provenance or {})
    merged["opened_on_demand"] = True
    return merged


async def harvest_by_opening_url(
    chat_url: str,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    harvest_page,
) -> HarvestResponse:
    """Goto *chat_url* on a registry host, harvest, then park or drop the tab."""
    outcome = await ensure_cse_attached(
        chat_url,
        holder=HARVEST_HOLDER,
        allow_mint=True,
    )
    if not outcome.ok or outcome.page is None:
        return HarvestResponse(
            outcome="not_attached",
            reason=outcome.error or "open_failed",
            provenance=_with_opened(provenance),
        )
    try:
        return await harvest_page(
            outcome.page,
            req,
            provenance=_with_opened(provenance),
        )
    finally:
        await _teardown_opened(outcome)
