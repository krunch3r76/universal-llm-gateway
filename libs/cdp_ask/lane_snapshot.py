"""Shared CDP active-work lane snapshot reader.

Hop cadence, auto handler, and Stargate generate gate all probe
``GET /v1/project-ask/active-work`` through ``read_cdp_lane_snapshot`` so
transport and seated-row attach stay one contract. Callers choose fail-open
vs fail-closed around this reader; the GET itself is shared.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from claude_bundles.hop_cadence_seat_snap import attach_registry_seated_rows

from cdp_ask.client import CdpAskClient, CdpAskClientError

# GIW background loops share the asyncio thread with /health. A 30s connect
# to a down cdp-ask starves the socket (Recv-Q climb) and fails fleet drain.
CONTROL_PLANE_TIMEOUT_S = 2.0


def _stamp_snap_read(snap: dict[str, Any]) -> dict[str, Any]:
    """Copy ``snap`` and stamp ``observed_at`` at GET-return time if absent.

    Capture runs after commission; the stamp must be the read clock, not the
    capture clock, or LOOKUP_FAILED cannot recover the fire-time membership gap.
    """
    out = dict(snap)
    if not out.get("observed_at"):
        out["observed_at"] = datetime.now(UTC).isoformat()
    return out


def read_cdp_lane_snapshot(*, client: CdpAskClient | None = None) -> dict[str, Any]:
    """Return active-work plus CSE-registry seated rows, stamped at read.

    Non-dict responses stay ``{}`` (falsy) so existing callers that treat an
    empty mapping as a failed probe do not flip. A successful dict always
    carries ``observed_at`` — server value if present, else this call's clock.
    ``seated_rows`` come from the CSE session registry so hop identity can
    see a seated operator with no in-flight project-ask. Admission scalars
    stay execution-store-only.
    """
    http = client or CdpAskClient()
    snap = http._request("GET", "/v1/project-ask/active-work")
    if not isinstance(snap, dict):
        return {}
    return attach_registry_seated_rows(_stamp_snap_read(snap))


def read_cdp_lane_snapshot_brief() -> dict[str, Any]:
    """Active-work GET with a control-plane timeout for GIW background loops.

    Empty mapping on transport/config failure so callers keep fail-open /
    fail-closed around a falsy snap. Never use the 30s default client timeout
    on the GIW asyncio thread.
    """
    try:
        return read_cdp_lane_snapshot(
            client=CdpAskClient(timeout_s=CONTROL_PLANE_TIMEOUT_S)
        )
    except CdpAskClientError:
        return {}
