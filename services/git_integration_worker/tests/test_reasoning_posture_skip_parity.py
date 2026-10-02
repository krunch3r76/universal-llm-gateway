"""Parity lock between GIW and Stargate reasoning-posture skip contracts."""

from __future__ import annotations

from reasoning_posture_contracts import REASONING_POSTURE_SKIP_CONTRACTS
from systems.frontier_consult.handoff_reasoning_posture import (
    REASONING_POSTURE_SKIP_CONTRACTS as STARGATE_SKIP,
)

from services.git_integration_worker.cursor_sdk_packet import _POSTURE_SKIP_JOBS


def test_reasoning_posture_skip_contracts_parity() -> None:
    assert _POSTURE_SKIP_JOBS == REASONING_POSTURE_SKIP_CONTRACTS
    assert STARGATE_SKIP == REASONING_POSTURE_SKIP_CONTRACTS
