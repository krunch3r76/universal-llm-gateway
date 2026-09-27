"""bind_execution_lane routes operator-proxy non-hop births through ensure."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import bind_execution_lane

pytestmark = pytest.mark.offline


def test_bind_execution_lane_operator_proxy_uses_ensure() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="x",
        holder="op",
        purpose="operator-proxy",
        mission_kind="root",
        parent_thread="9497",
    )
    seat = MagicMock()
    seat.registration_id = "driving-root"
    with (
        patch(
            "cdp_ask.runner.ensure_driving_operator_seat",
            return_value=seat,
        ) as ensure,
        patch("cdp_ask.runner.cdp_registry.register_lane") as mint,
        patch(
            "claude_bundles.cdp_registry.session_address.retire_predecessor_identity",
        ) as retire,
    ):
        out = bind_execution_lane(req, holder="op")
    assert out is seat
    ensure.assert_called_once_with(
        holder="op",
        parent_thread="9497",
        purpose="operator-proxy",
        mission_kind="root",
    )
    mint.assert_not_called()
    retire.assert_called_once_with("driving-root", parent_thread="9497")


def test_bind_execution_lane_hop_still_mints() -> None:
    req = SubmitProjectAskRequest(
        prompt_text="x",
        holder="hop",
        purpose="operator-proxy",
        mission_kind="hop",
        parent_thread="9497",
    )
    minted = MagicMock()
    minted.registration_id = "hop-row"
    with (
        patch("cdp_ask.runner.ensure_driving_operator_seat") as ensure,
        patch(
            "cdp_ask.runner.cdp_registry.register_lane",
            return_value=minted,
        ) as mint,
        patch(
            "claude_bundles.cdp_registry.session_address.retire_predecessor_identity",
        ) as retire,
    ):
        out = bind_execution_lane(req, holder="hop")
    assert out is minted
    ensure.assert_not_called()
    mint.assert_called_once()
    retire.assert_called_once_with("hop-row", parent_thread="9497")
