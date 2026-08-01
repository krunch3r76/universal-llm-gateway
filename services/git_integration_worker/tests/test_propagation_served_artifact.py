"""Unit tests for served_artifact propagation proof."""

from __future__ import annotations

from deploy_identity.code_ref_relation import code_ref_relation_from_observed
from implement_admission.propagation_row import PropagationRow

from charter_runner_store.propagation_ledger import list_open_rows, upsert_open_rows
from charter_runner_store.propagation_terminal import settle_open_row
from services.git_integration_worker.cursor_auto.propagation_probe import proof_observed
from services.git_integration_worker.cursor_auto.propagation_served_artifact import (
    SERVED_ARTIFACT_DESCRIPTORS,
    served_artifact_observed,
)


_SHA_A = "abc1230000000000000000000000000000000000"
_SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _row(code_ref: str = _SHA_A, *, service: str = "git_integration_worker") -> PropagationRow:
    return PropagationRow(
        service=service,
        code_ref=code_ref,
        safe_window="drain_required",
        proof="test",
        proof_class="served_artifact",
    )


def _pass_payload(*, code_ref: str, code_version: str | None, count: int = 9) -> dict:
    surface = {
        "url": "http://127.0.0.1:8091/api/v1/git/openapi.json",
        "x_mcp_count": count,
        "bytes_sha256": "deadbeef",
        "bytes_len": 100,
    }
    relation = code_ref_relation_from_observed(code_ref, code_version)
    return {
        "proof_class": "served_artifact",
        "surfaces": {
            "direct_8091": surface,
            "stargate_9999": {**surface},
        },
        "byte_identical": True,
        "x_mcp_count": count,
        "expected_x_mcp_count": 9,
        "code_version": code_version,
        "code_ref": code_ref,
        "code_ref_relation": relation,
    }


def test_served_artifact_pass() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=_SHA_A)
    assert served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert proof_observed(_row(_SHA_A), payload)


def test_served_artifact_unknown_version_settles_when_artifact_passes() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=None)
    assert payload["code_ref_relation"] == "unknown"
    assert served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert proof_observed(_row(_SHA_A), payload)


def test_served_artifact_unrelated_mismatch_blocks() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=_SHA_B)
    assert payload["code_ref_relation"] == "unrelated"
    assert not served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert not proof_observed(_row(_SHA_A), payload)


def test_served_artifact_unknown_does_not_bypass_artifact_failure() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=None, count=3)
    assert payload["code_ref_relation"] == "unknown"
    assert not served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert not proof_observed(_row(_SHA_A), payload)


def test_served_artifact_settlement_closes_unknown_version_row(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path))
    upsert_open_rows(
        [
            PropagationRow(
                service="rag",
                code_ref=_SHA_A,
                safe_window="harvest",
                proof="test",
                proof_class="served_artifact",
            )
        ]
    )
    row = list_open_rows()[0]
    payload = _pass_payload(code_ref=_SHA_A, code_version=None, count=7)

    monkeypatch.setattr(
        "charter_runner_store.propagation_terminal._probe_for_projection",
        lambda _row: payload,
    )

    from charter_runner_store.propagation_terminal import default_probe

    result = settle_open_row(row, default_probe)
    assert result.outcome == "closed"
    assert list_open_rows() == []


def test_served_artifact_fail_count_shortfall() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=_SHA_A, count=3)
    assert not served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert not proof_observed(_row(_SHA_A), payload)


def test_served_artifact_fail_surface_disagreement() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=_SHA_A)
    payload["byte_identical"] = False
    payload["surfaces"]["stargate_9999"]["bytes_sha256"] = "other000"
    assert not served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert not proof_observed(_row(_SHA_A), payload)


def test_probe_descriptors_cover_required_services() -> None:
    for service in ("git_integration_worker", "cortex_api", "agent_bus", "rag"):
        descriptor = SERVED_ARTIFACT_DESCRIPTORS[service]
        assert descriptor.surfaces
        assert descriptor.expected_x_mcp_count > 0


def test_code_ref_relation_from_observed_unknown_on_null() -> None:
    assert code_ref_relation_from_observed(_SHA_A, None) == "unknown"
    assert code_ref_relation_from_observed(_SHA_A, "") == "unknown"
