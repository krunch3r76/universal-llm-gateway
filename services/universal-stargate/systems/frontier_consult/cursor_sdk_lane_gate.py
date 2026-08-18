"""Refuse omitted checkout lane on top-level cursor-sdk generate.

``team_dispatch(op=generate, seat=cursor-sdk)`` must pass ``lane ∈ {A,B}``.
The only documented omit is ``nest_under`` inherit. ``contract=wrap`` is
exempt — wrap never posts GIW. ``resume_of`` is a GIW worker-POST field,
not a team_dispatch generate field.
"""

from __future__ import annotations

from .admission import FrontierEndpointError

LANE_REQUIRED_CODE = "lane_required"
LANE_REQUIRED_REASON = (
    "lane is required for top-level seat=cursor-sdk generate "
    "(pass A or B). Omit only when nest_under inherits parent isolation. "
    "See consult-routing § cursor-sdk checkout lane. "
    "contract=wrap is exempt (no GIW checkout)."
)


def require_cursor_sdk_checkout_lane(
    *,
    request_id: str,
    lane: str | None,
    nest_under: str | None,
    contract: str | None,
) -> None:
    """Raise 422 ``lane_required`` when a top-level cursor-sdk generate omits lane.

    Invoked only from the cursor-sdk generate route (seat already bound).
    """
    if contract == "wrap":
        return
    if lane is not None:
        return
    if nest_under and str(nest_under).strip():
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="lane",
        reason=LANE_REQUIRED_REASON,
        status_code=422,
        code=LANE_REQUIRED_CODE,
    )
