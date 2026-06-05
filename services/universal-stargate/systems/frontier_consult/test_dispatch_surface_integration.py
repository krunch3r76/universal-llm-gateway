"""Opt-in integration tests — dispatch-surface-split Phase 5 gaps.

Unit coverage for the same contracts lives elsewhere:
- D3/D4: test_dispatch_surface.py (thread admission)
- D2/D6/E1 partial: test_async_tracker_delivery.py (on-behalf POST, no envelope)
- D6/D7: test_output_short_gating.py (output_short suppressed for thread contract)
- M1–M5: test_migration.py + test_frontier_registration.py

Run live slice:
  ULG_DISPATCH_INTEGRATION=1 pytest services/universal-stargate/systems/frontier_consult/test_dispatch_surface_integration.py -q

Requires: healthy Stargate, agent-bus, and a dispatchable model for the chosen role.
"""

from __future__ import annotations

import os

import pytest

_INTEGRATION = os.environ.get("ULG_DISPATCH_INTEGRATION") == "1"

pytestmark = pytest.mark.integration


def _skip_unless_integration() -> None:
    if not _INTEGRATION:
        pytest.skip(
            "Set ULG_DISPATCH_INTEGRATION=1 to run live dispatch-surface integration"
        )


@pytest.mark.asyncio
async def test_s4_to_thread_happy_path_live() -> None:
    """S4 — op=to_thread end-to-end: dispatch completes and bus turn is posted."""
    _skip_unless_integration()
    pytest.skip("TODO: wire open agent-bus thread + team_dispatch + pipeline poll")


@pytest.mark.asyncio
async def test_d1_tracker_running_before_completion_live() -> None:
    """D1 — immediate poll shows running before terminal completed."""
    _skip_unless_integration()
    pytest.skip("TODO: requires tracker introspection or timed poll loop")


@pytest.mark.asyncio
async def test_d5_cancel_mid_flight_live() -> None:
    """D5 — cancel is non-transactional; terminal status=cancelled."""
    _skip_unless_integration()
    pytest.skip("TODO: slow-reply fixture + POST /pipelines/<id>/cancel")


@pytest.mark.asyncio
async def test_e1_no_metadata_envelope_live() -> None:
    """E1 — to_thread yields one stargate-posted reply, no output_short hint."""
    _skip_unless_integration()
    pytest.skip("TODO: live gatherer to_thread + thread turn inspection")
