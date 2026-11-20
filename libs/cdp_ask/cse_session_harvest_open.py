"""Open a CSE URL for harvest when no lane is attached — no paste, no submit."""

from __future__ import annotations

import contextlib
from typing import Any

from claude_bundles import cdp_registry

from cdp_ask.cse_session_models import HarvestRequest, HarvestResponse
from cdp_ask.followup import _acquire_lane, _release_lane
from cdp_ask.followup_reattach import _teardown_attempt, ensure_cse_attached

HARVEST_HOLDER = "cse-session-harvest"

_ATTACH_RETRY_ERRORS = frozenset({"attachment.refused", "not_attached"})


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


async def _teardown_opened(
    outcome,
    response: HarvestResponse | None = None,
    *,
    release_minted: bool = False,
) -> None:
    """Disconnect Playwright after a read and close what the read opened.

    A tab this read opened is closed; a tab it found is left as found. A host
    the attach minted is released when the caller passes *release_minted*,
    which only the two harvest callers do. Hygiene treats an idle ``ask`` host
    as a leak; the lane hold above is what stops that kill during the read.
    """
    del response
    if outcome is None or not outcome.ok:
        return
    # Page-only borrow: close only a tab this attach opened, not a reused lane tab.
    close_page = bool(getattr(outcome, "page_only_close_tab", False))
    await _teardown_attempt(outcome.page, outcome.pw, close_page=close_page)
    minted = bool(getattr(outcome, "lane_created", False))
    page_only = bool(getattr(outcome, "page_only_borrow", False))
    if release_minted and minted and page_only:
        with contextlib.suppress(Exception):
            cdp_registry.deregister_lane(outcome.registration_id)


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
    """Goto *chat_url* on a registry host and harvest.

    A tab this call opened is closed after the read, and a host it minted is
    released.
    """
    attach_attempts: list[dict[str, Any]] = []
    outcome = None
    for attempt_idx in range(2):
        outcome = await ensure_cse_attached(
            chat_url,
            holder=HARVEST_HOLDER,
            allow_mint=True,
            allow_k44_superseded_page_only=True,
            page_only_attach=True,
        )
        attach_attempts.append(
            {
                "attempt": attempt_idx + 1,
                "ok": outcome.ok,
                "error": outcome.error,
                "reattach": attempt_idx == 1,
            }
        )
        if outcome.ok and outcome.page is not None:
            break
        err = (outcome.error or "").strip()
        if attempt_idx == 0 and err in _ATTACH_RETRY_ERRORS:
            continue
        break

    opened_prov = _with_opened(
        provenance,
        registration_id=outcome.registration_id if outcome is not None else None,
    )
    opened_prov["attach_attempts"] = attach_attempts
    if len(attach_attempts) > 1:
        opened_prov["reattach"] = True
    if outcome is None or not outcome.ok or outcome.page is None:
        reason = (outcome.error if outcome is not None else None) or "open_failed"
        return HarvestResponse(
            outcome="not_attached",
            reason=reason,
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
            merged = dict(opened_prov)
            if response.provenance:
                merged.update(response.provenance)
            response.provenance = merged
            return response
        finally:
            await _teardown_opened(outcome, response, release_minted=True)