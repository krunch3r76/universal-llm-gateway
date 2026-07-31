"""Preflight gate-13 recon waiver payload tests."""

from __future__ import annotations

import json

import pytest

from implement_admission.implement_ready_preflight import GateStatus, preflight_implement_ready
from implement_admission.recon_waiver import build_structured_waiver
from implement_admission.test_implement_ready_preflight import BASE, _ready_evidence, make


@pytest.mark.offline
def test_gate13_waiver_pass_includes_recon_waiver_payload() -> None:
    waiver = build_structured_waiver(
        reason_code="design_pre_adjudicated",
        reason="fork 4509 accepted",
        waived_by="claude-cursor",
        spec_sha256="spec_sha256:deadbeef",
        waived_at="2026-07-06T00:00:00+00:00",
    )
    assertion = {**BASE["assertion"], "evidence_uris": _ready_evidence()}
    args = make(
        assertion=assertion,
        skeptic_ratified=False,
        recon_waived=True,
        recon_waiver=waiver.to_gate_sibling(),
    )
    report = preflight_implement_ready(**args)
    gate13 = report.gates[13]
    assert gate13.status == GateStatus.PASSED
    assert gate13.recon_waiver == waiver.to_gate_sibling()
    payload = report.to_dict()
    assert payload["recon_waived"] is True
    assert payload["recon_waiver"]["reason_code"] == "design_pre_adjudicated"
    assert json.loads(json.dumps(payload["recon_waiver"])) == payload["recon_waiver"]
