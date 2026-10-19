"""BW8 — packet bytes inside the named cap are counted before any GIW call."""

from __future__ import annotations

import pytest
from pipelines.team_dispatch_admit.v1.handlers.assemble import evaluate_assemble

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.conductor_admit_enrich import (
    CONDUCTOR_PACKET_MAX_BYTES,
    reject_oversized_conductor_packet,
)

_LARGEST_OBSERVED_PACKET = 17723


@pytest.mark.offline
def test_bw8_fixture_under_cap_counts_packet_bytes() -> None:
    assert CONDUCTOR_PACKET_MAX_BYTES == 262144
    packet = "p" * _LARGEST_OBSERVED_PACKET
    reject_oversized_conductor_packet(packet, request_id="bw8")
    plan = evaluate_assemble(
        {},
        {
            "admitted": True,
            "job": "conductor",
            "packet_markdown": packet,
            "policy_sha256": "bw8sha",
        },
    )
    budget = plan["turn0_budget"]
    assert budget["total_turn0_bytes"] == len(packet.encode("utf-8"))
    assert budget["total_turn0_bytes"] >= _LARGEST_OBSERVED_PACKET
    assert budget["policy_sha256"] == "bw8sha"
    with pytest.raises(FrontierEndpointError) as exc:
        reject_oversized_conductor_packet(
            "q" * (CONDUCTOR_PACKET_MAX_BYTES + 1),
            request_id="bw8-over",
        )
    assert exc.value.code == "conductor_packet_too_large"
    assert exc.value.status_code == 422
