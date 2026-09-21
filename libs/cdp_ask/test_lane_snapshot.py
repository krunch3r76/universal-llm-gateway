"""Control-plane CDP snapshot timeout for GIW background loops."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cdp_ask.client import CdpAskClient, CdpAskClientError
from cdp_ask.lane_snapshot import (
    CONTROL_PLANE_TIMEOUT_S,
    read_cdp_lane_snapshot_brief,
)

pytestmark = pytest.mark.offline


def test_brief_snapshot_uses_control_plane_timeout() -> None:
    captured: list[CdpAskClient] = []

    def _read(*, client: CdpAskClient | None = None) -> dict[str, object]:
        assert client is not None
        captured.append(client)
        return {"status": "ok"}

    with patch("cdp_ask.lane_snapshot.read_cdp_lane_snapshot", side_effect=_read):
        assert read_cdp_lane_snapshot_brief() == {"status": "ok"}
    assert captured[0].timeout_s == CONTROL_PLANE_TIMEOUT_S
    assert CONTROL_PLANE_TIMEOUT_S <= 2.0


def test_brief_snapshot_empty_on_unreachable() -> None:
    with patch(
        "cdp_ask.lane_snapshot.read_cdp_lane_snapshot",
        side_effect=CdpAskClientError("cdp-ask unreachable"),
    ):
        assert read_cdp_lane_snapshot_brief() == {}
