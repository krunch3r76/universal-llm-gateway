"""Hermetic tests for commit-to-activation attribution."""

from __future__ import annotations

from charter_runner_store import propagation_validation
from charter_runner_store.propagation_liveness import CodeRefLiveness


def test_validation_record_and_current_projection(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    validation_id = propagation_validation.record_validation(
        service="agent_bus",
        code_ref="a" * 40,
        row_id="row-1",
        pre_observation={"pid": 1},
        post_observation={"pid": 2, "code_version": "a" * 40},
        observed_code_version="a" * 40,
        code_ref_relation="equal",
        identity_measurement="changed",
        outcome="validated",
    )

    assert validation_id
    monkeypatch.setattr(
        propagation_validation,
        "observe_code_ref_live",
        lambda service, code_ref: CodeRefLiveness(
            answer="yes",
            service=service,
            code_ref=code_ref,
            observed_code_version=code_ref,
            relation="equal",
            observation={"probe_reachable": True},
            reason="test",
        ),
    )
    result = propagation_validation.current_validation("agent_bus", "a" * 40)
    assert result["verdict"] == "running_committed_code"
    assert result["activation"]["validation_id"] == validation_id


def test_unknown_probe_never_promotes_stale_record(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    propagation_validation.record_validation(
        service="agent_bus",
        code_ref="b" * 40,
        outcome="validated",
        identity_measurement="changed",
    )
    monkeypatch.setattr(
        propagation_validation,
        "observe_code_ref_live",
        lambda service, code_ref: CodeRefLiveness(
            answer="unknown",
            service=service,
            code_ref=code_ref,
            observed_code_version=None,
            relation=None,
            observation={"probe_reachable": False},
            reason="unreachable",
        ),
    )
    assert propagation_validation.current_validation(
        "agent_bus", "b" * 40
    )["verdict"] == "unknown"
