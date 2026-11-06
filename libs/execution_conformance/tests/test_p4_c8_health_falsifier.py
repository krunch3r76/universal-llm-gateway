"""C8 — health-alone must not imply reconcile readiness."""

from __future__ import annotations

from execution_conformance.adapters import AgentBusAdapter
from execution_conformance.reconciler import health_only_would_claim_ready


def test_p4_c8_health_alone_falsifier() -> None:
    adapter = AgentBusAdapter()
    assert health_only_would_claim_ready(adapter) is False
