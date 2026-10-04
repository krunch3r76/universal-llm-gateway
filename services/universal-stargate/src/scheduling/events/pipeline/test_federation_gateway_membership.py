"""Tests for federation.gateway.membership event factory."""

from __future__ import annotations

import pytest
from universal_event_bus import Event

from src.scheduling.events.pipeline.factories import (
    FEDERATION_GATEWAY_MEMBERSHIP,
    federation_gateway_membership,
)


def test_federation_gateway_membership_factory_signal_and_payload() -> None:
    membership = {"gateway_ids": ["gw-a"], "pipeline_ids": ["pipe-1"]}
    event = federation_gateway_membership(membership)
    assert event.signal == FEDERATION_GATEWAY_MEMBERSHIP
    assert event.payload == membership
    assert event.scope == "global"


def test_direct_federation_gateway_membership_event_raises() -> None:
    with pytest.raises(RuntimeError, match="Direct Event"):
        Event(signal="federation.gateway.membership", payload={})
