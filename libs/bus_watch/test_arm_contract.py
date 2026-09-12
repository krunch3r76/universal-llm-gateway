"""Arm-contract tests: producer linkage must be declared one way or the other."""

from __future__ import annotations

import pytest

from bus_watch.arm_contract import require_producer_declaration


@pytest.mark.offline
def test_execution_id_alone_is_admitted() -> None:
    assert require_producer_declaration("b9d3505f", no_producer=False) is None


@pytest.mark.offline
def test_no_producer_alone_is_admitted() -> None:
    assert require_producer_declaration("", no_producer=True) is None


@pytest.mark.offline
def test_silence_is_a_mis_arm() -> None:
    message = require_producer_declaration("   ", no_producer=False)
    assert message is not None
    assert message.startswith("mis-arm:")
    assert "--execution-id" in message and "--no-producer" in message


@pytest.mark.offline
def test_both_flags_is_a_mis_arm() -> None:
    message = require_producer_declaration("b9d3505f", no_producer=True)
    assert message is not None
    assert "mutually exclusive" in message
