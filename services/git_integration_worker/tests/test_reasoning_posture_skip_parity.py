"""Parity lock between GIW and Stargate reasoning-posture skip contracts."""

from __future__ import annotations

from reasoning_posture_contracts import REASONING_POSTURE_SKIP_CONTRACTS

from services.git_integration_worker.cursor_sdk_packet import (
    _REASONING_POSTURE_SKIP_CONTRACTS,
)
from systems.frontier_consult.handoff_reasoning_posture import (
    REASONING_POSTURE_SKIP_CONTRACTS as STARGATE_SKIP,
)


def test_reasoning_posture_skip_contracts_parity() -> None:
    assert _REASONING_POSTURE_SKIP_CONTRACTS == REASONING_POSTURE_SKIP_CONTRACTS
    assert STARGATE_SKIP == REASONING_POSTURE_SKIP_CONTRACTS
