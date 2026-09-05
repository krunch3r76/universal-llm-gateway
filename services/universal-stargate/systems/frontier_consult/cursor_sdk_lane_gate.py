"""Refuse omitted checkout lane on top-level cursor-sdk generate.

``team_dispatch(op=generate, seat=cursor-sdk)`` must pass ``lane ∈ {A,B}``.
Documented omit paths: ``nest_under`` inherit, ``resume_of`` inherit.
``contract=wrap`` is exempt — wrap never posts GIW.
"""

from __future__ import annotations

from .admission import FrontierEndpointError

LANE_REQUIRED_CODE = "lane_required"
LANE_REQUIRED_REASON = (
    "lane is required for top-level seat=cursor-sdk generate "
    "(pass A or B). Omit only when nest_under or resume_of inherits parent "
    "isolation. See consult-routing § cursor-sdk checkout lane. "
    "contract=wrap is exempt (no GIW checkout)."
)
RESUME_OF_XOR_NEST_UNDER_CODE = "resume_of_xor_nest_under"
RESUME_OF_REQUIRES_REUSE_THREAD_CODE = "resume_of_requires_reuse_thread"


def require_cursor_sdk_checkout_lane(
    *,
    request_id: str,
    lane: str | None,
    nest_under: str | None,
    resume_of: str | None = None,
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
    if resume_of and str(resume_of).strip():
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="lane",
        reason=LANE_REQUIRED_REASON,
        status_code=422,
        code=LANE_REQUIRED_CODE,
    )


def reject_resume_of_conflicts(
    *,
    request_id: str,
    resume_of: str | None,
    nest_under: str | None,
    reuse_thread: str | None,
) -> None:
    """Raise 422 when ``resume_of`` conflicts with nest or omits ``reuse_thread``."""
    resume = (resume_of or "").strip() or None
    if not resume:
        return
    if nest_under and str(nest_under).strip():
        raise FrontierEndpointError(
            request_id=request_id,
            field="resume_of",
            reason="resume_of and nest_under are mutually exclusive",
            status_code=422,
            code=RESUME_OF_XOR_NEST_UNDER_CODE,
        )
    if not reuse_thread or not str(reuse_thread).strip():
        raise FrontierEndpointError(
            request_id=request_id,
            field="reuse_thread",
            reason=(
                "reuse_thread is required when resume_of is set "
                "(same-thread resume; parent worker thread id)"
            ),
            status_code=422,
            code=RESUME_OF_REQUIRES_REUSE_THREAD_CODE,
        )
