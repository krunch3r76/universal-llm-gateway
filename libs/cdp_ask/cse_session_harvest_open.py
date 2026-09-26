"""Open a CSE URL for harvest when no lane is attached — no paste, no submit."""

from __future__ import annotations

from typing import Any

from cdp_ask.cse_session_models import HarvestRequest, HarvestResponse
from cdp_ask.followup import _acquire_lane, _release_lane
from cdp_ask.followup_reattach import _teardown_attempt, ensure_cse_attached

HARVEST_HOLDER = "cse-session-harvest"


class _LaneHold:
    """In-flight mark hygiene already consults via ``lane_in_flight``."""

    def __init__(self, registration_id: str | None) -> None:
        self.registration_id = (registration_id or "").strip()
        self.held = False

    async def __aenter__(self) -> _LaneHold:
        if self.registration_id:
            self.held = await _acquire_lane(self.registration_id)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self.held:
            _release_lane(self.registration_id)


async def _teardown_opened(outcome, response: HarvestResponse | None = None) -> None:
    """Disconnect Playwright and leave the Chrome and tab up.

    Harvest is a read. Parking a relaunched seat or closing the tab drops the
    browser the caller just proved is the page. Hygiene treats an idle ``ask``
    host as a leak; the lane hold above is what stops that kill during the read.
    """
    del response
    if outcome is None or not outcome.ok:
        return
    await _teardown_attempt(outcome.page, outcome.pw, close_page=False)


def _with_opened(
    provenance: dict[str, Any] | None,
    *,
    registration_id: str | None = None,
) -> dict[str, Any]:
    merged = dict(provenance or {})
    merged["opened_on_demand"] = True
    if registration_id:
        merged["registration_id"] = registration_id
    return merged


async def harvest_by_opening_url(
    chat_url: str,
    req: HarvestRequest,
    provenance: dict[str, Any] | None,
    harvest_page,
    *,
    requested_registration_id: str | None = None,
    requested_chat_url: str | None = None,
) -> HarvestResponse:
    """Goto *chat_url* on a registry host and harvest. The tab stays open."""
    outcome = await ensure_cse_attached(
        chat_url,
        holder=HARVEST_HOLDER,
        allow_mint=True,
    )
    opened_prov = _with_opened(
        provenance,
        registration_id=outcome.registration_id,
    )
    if not outcome.ok or outcome.page is None:
        return HarvestResponse(
            outcome="not_attached",
            reason=outcome.error or "open_failed",
            provenance=opened_prov,
        )
    response: HarvestResponse | None = None
    async with _LaneHold(outcome.registration_id):
        try:
            response = await harvest_page(
                outcome.page,
                req,
                provenance=opened_prov,
                requested_registration_id=requested_registration_id,
                requested_chat_url=requested_chat_url or chat_url,
            )
            return response
        finally:
            await _teardown_opened(outcome, response)
