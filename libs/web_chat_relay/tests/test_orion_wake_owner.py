"""Contract tests for the shared wake-owner seam (integration dependency: 20995)."""

from __future__ import annotations

import pytest
from web_chat_relay.orion_wake import PINNED_CONVERSATION_URL
from web_chat_relay.orion_wake_owner import (
    FailClosedWakeDeliveryOwner,
    InMemoryWakeDeliveryOwner,
    WakeClaimContext,
    load_shared_wake_delivery_owner,
)

pytestmark = pytest.mark.offline


def test_fail_closed_owner_rejects_claims() -> None:
    claim = WakeClaimContext(
        wake_id="w1",
        agent="orion",
        thread_id="21207",
        turn_ids=(1,),
        turn_numbers=(1,),
        endpoint_url=PINNED_CONVERSATION_URL,
    )
    out = FailClosedWakeDeliveryOwner().validate_claim_for_delivery(claim)
    assert out["ok"] is False
    assert out["reason"] == "wake_owner_unconfigured"


def test_load_shared_owner_is_fail_closed_without_adapter() -> None:
    owner = load_shared_wake_delivery_owner()
    claim = WakeClaimContext(
        wake_id="w1",
        agent="orion",
        thread_id="21207",
        turn_ids=(1,),
        turn_numbers=(1,),
        endpoint_url=PINNED_CONVERSATION_URL,
    )
    out = owner.validate_claim_for_delivery(claim)
    assert out["ok"] is False


def test_in_memory_owner_rejects_endpoint_mismatch() -> None:
    owner = InMemoryWakeDeliveryOwner()
    claim = WakeClaimContext(
        wake_id="w1",
        agent="orion",
        thread_id="21207",
        turn_ids=(1,),
        turn_numbers=(1,),
        endpoint_url=PINNED_CONVERSATION_URL,
    )
    owner.register_claim(claim)
    bad = WakeClaimContext(
        wake_id="w1",
        agent="orion",
        thread_id="21207",
        turn_ids=(1,),
        turn_numbers=(1,),
        endpoint_url="https://chatgpt.com/c/11111111-2222-3333-4444-555555555555",
    )
    assert owner.validate_claim_for_delivery(bad)["reason"] == "wake_claim_endpoint_mismatch"
