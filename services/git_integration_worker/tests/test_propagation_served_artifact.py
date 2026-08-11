"""Unit tests for served_artifact propagation proof."""

from __future__ import annotations

from charter_runner_store.propagation_ledger import list_open_rows, upsert_open_rows
from charter_runner_store.propagation_terminal import settle_open_row
from deploy_identity.code_ref_relation import code_ref_relation_from_observed
from implement_admission.propagation_row import PropagationRow

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


def _pass_payload(
    *,
    code_ref: str,
    code_version: str | None,
    count: int = 9,
    pid: int | None = 4242,
    process_start_time: str | None = "2026-08-11T01:00:00Z",
) -> dict:
    surface = {
        "url": "http://127.0.0.1:8091/api/v1/git/openapi.json",
        "x_mcp_count": count,
        "bytes_sha256": "deadbeef",
        "bytes_len": 100,
    }
    relation = code_ref_relation_from_observed(code_ref, code_version)
    liveness: dict = {"code_version": code_version}
    if pid is not None:
        liveness["pid"] = pid
    if process_start_time is not None:
        liveness["process_start_time"] = process_start_time
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
        "liveness": liveness,
    }


def _before_payload(*, code_version: str = _SHA_B) -> dict:
    return _pass_payload(
        code_ref=_SHA_A,
        code_version=code_version,
        pid=1111,
        process_start_time="2026-08-11T00:00:00Z",
    )


def test_served_artifact_pass() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=_SHA_A)
    before = _before_payload()
    assert served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert proof_observed(_row(_SHA_A), payload, before=before)


def test_served_artifact_requires_identity_delta() -> None:
    """Byte-identical OpenAPI alone must not close — all surfaces can be stale together."""
    payload = _pass_payload(code_ref=_SHA_A, code_version=_SHA_A)
    assert served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert proof_observed(_row(_SHA_A), payload) is False
    before_same_identity = _pass_payload(
        code_ref=_SHA_A,
        code_version=_SHA_B,
        pid=4242,
        process_start_time="2026-08-11T01:00:00Z",
    )
    assert (
        proof_observed(_row(_SHA_A), payload, before=before_same_identity) is False
    )


def test_served_artifact_unknown_version_settles_when_artifact_passes() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=None)
    before = _before_payload()
    assert payload["code_ref_relation"] == "unknown"
    assert served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert proof_observed(_row(_SHA_A), payload, before=before)


def test_served_artifact_unrelated_mismatch_blocks() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=_SHA_B)
    before = _before_payload(code_version="cccccccccccccccccccccccccccccccccccccccc")
    assert payload["code_ref_relation"] == "unrelated"
    assert not served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert not proof_observed(_row(_SHA_A), payload, before=before)


def test_served_artifact_unknown_does_not_bypass_artifact_failure() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=None, count=3)
    before = _before_payload()
    assert payload["code_ref_relation"] == "unknown"
    assert not served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert not proof_observed(_row(_SHA_A), payload, before=before)


def test_served_artifact_settlement_does_not_close_without_identity(
    tmp_path, monkeypatch,
) -> None:
    """Settle path shares proof_passes with client_visible — identity owed."""
    from deploy_identity import code_ref_relation as relation_mod

    real = relation_mod.resolve_commit_sha

    def _fake(value: object) -> str | None:
        token = str(value or "").strip().lower()
        if len(token) == 40 and all(c in "0123456789abcdef" for c in token):
            return token
        return real(value)

    monkeypatch.setattr(relation_mod, "resolve_commit_sha", _fake)
    monkeypatch.setattr(relation_mod, "_resolve_commit_sha", _fake)
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
    assert result.outcome != "closed"
    assert len(list_open_rows()) == 1


def test_served_artifact_fail_count_shortfall() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=_SHA_A, count=3)
    before = _before_payload()
    assert not served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert not proof_observed(_row(_SHA_A), payload, before=before)


def test_served_artifact_fail_surface_disagreement() -> None:
    payload = _pass_payload(code_ref=_SHA_A, code_version=_SHA_A)
    before = _before_payload()
    payload["byte_identical"] = False
    payload["surfaces"]["stargate_9999"]["bytes_sha256"] = "other000"
    assert not served_artifact_observed(payload, code_ref=_SHA_A, expected_x_mcp_count=9)
    assert not proof_observed(_row(_SHA_A), payload, before=before)


def test_probe_descriptors_cover_required_services() -> None:
    for service in ("git_integration_worker", "cortex_api", "agent_bus", "rag"):
        descriptor = SERVED_ARTIFACT_DESCRIPTORS[service]
        assert descriptor.surfaces
        assert descriptor.expected_x_mcp_count > 0


def test_code_ref_relation_from_observed_unknown_on_null() -> None:
    assert code_ref_relation_from_observed(_SHA_A, None) == "unknown"
    assert code_ref_relation_from_observed(_SHA_A, "") == "unknown"
