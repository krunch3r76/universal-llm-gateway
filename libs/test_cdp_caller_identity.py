"""Deliverable ``caller_agent`` grammar — libs-only (MCP + Stargate share)."""

from __future__ import annotations

import pytest

from cdp_caller_identity import is_valid_cdp_caller_agent


@pytest.mark.offline
def test_caller_agent_casefold_still_rejects_dispatch() -> None:
    assert is_valid_cdp_caller_agent("DISPATCH") is False


@pytest.mark.offline
def test_cursor_mailbox_is_valid_caller_agent() -> None:
    assert is_valid_cdp_caller_agent("cursor") is True


@pytest.mark.offline
def test_cursor_sdk_is_valid_caller_agent() -> None:
    assert is_valid_cdp_caller_agent("cursor-sdk") is True


@pytest.mark.offline
def test_conductor_hop_remapped_wake_head_refused() -> None:
    assert is_valid_cdp_caller_agent("conductor-hop") is False


@pytest.mark.offline
def test_normalize_bus_address_alias_refused() -> None:
    assert is_valid_cdp_caller_agent("claude-web") is False