"""Unit tests for structured propagation row parsing and coercion."""

from __future__ import annotations

import pytest

from implement_admission.propagation_row import (
    MissingProofTemplateError,
    PropagationRow,
    compose_proof,
    resolve_code_ref,
    rows_from_closeout_payload,
    rows_from_lib_consumers,
    rows_from_residue_lines,
)


def test_structured_propagation_wins_over_legacy():
    payload = {
        "propagation": [
            {
                "service": "mcp",
                "action": "sync_restart",
                "code_ref": "sha-structured",
                "safe_window": "standalone_ok",
                "proof": "GET /health → code_version == code_ref",
                "proof_class": "client_visible",
            }
        ],
        "propagation_residue": [
            'sync_restart: git_integration_worker — manage(action="sync_restart", service="git_integration_worker")'
        ],
    }
    rows, skipped, prose = rows_from_closeout_payload(payload)
    assert len(rows) == 1
    assert rows[0].service == "mcp"
    assert rows[0].code_ref == "sha-structured"
    assert skipped == []
    assert prose is False


def test_legacy_residue_coerces_to_rows():
    rows, skipped = rows_from_residue_lines(
        ['sync_restart: mcp — manage(action="sync_restart", service="mcp")'],
        code_ref="legacy-sha",
    )
    assert len(rows) == 1
    assert rows[0].service == "mcp"
    assert rows[0].code_ref == "legacy-sha"
    assert rows[0].safe_window == "standalone_ok"
    assert skipped == []


def test_prose_only_runtime_land_sets_advisory():
    payload = {
        "propagation_residue": ["libs_touched: libs/foo.py — lead decides"],
        "files_modified": ["libs/implement_admission/spec.py"],
        "evidence_uris": {"git_refs": ["sha-prose"]},
    }
    rows, _skipped, prose = rows_from_closeout_payload(payload)
    assert prose is True
    assert rows == []


def test_row_model_requires_service_and_code_ref():
    row = PropagationRow(service="mcp", code_ref="abc")
    assert row.proof_class == "client_visible"
    assert row.safe_window == "standalone_ok"


def test_rows_from_lib_consumers_skips_test_module():
    rows = rows_from_lib_consumers(
        ["libs/implement_admission/test_propagation_block_parser.py"],
        code_ref="sha-test-skip",
    )
    assert rows == []


def test_resolve_code_ref_prefers_explicit_git_refs_over_closeout_head():
    payload = {
        "evidence_uris": {"git_refs": ["explicit-sha"]},
        "closeout_head": "closeout-sha",
    }
    assert resolve_code_ref(payload) == "explicit-sha"


def test_resolve_code_ref_uses_closeout_head_when_git_refs_empty():
    payload = {"closeout_head": "closeout-sha"}
    assert resolve_code_ref(payload) == "closeout-sha"


def test_resolve_code_ref_unknown_without_sources():
    assert resolve_code_ref({}) == "unknown"
    assert resolve_code_ref({"closeout_head": ""}) == "unknown"


def test_rows_from_closeout_payload_ignores_deleted_lib_for_consumers():
    payload = {
        "files_deleted": ["libs/deploy_identity/__init__.py"],
        "evidence_uris": {"git_refs": ["delete-only-sha"]},
    }
    rows, skipped, prose = rows_from_closeout_payload(payload)
    assert rows == []
    assert skipped == []
    assert prose is False


def test_rows_from_closeout_payload_modified_lib_still_mints_consumers():
    payload = {
        "files_modified": ["libs/deploy_identity/__init__.py"],
        "evidence_uris": {"git_refs": ["land-sha"]},
    }
    rows, skipped, prose = rows_from_closeout_payload(payload)
    services = {row.service for row in rows}
    assert services == {"git_integration_worker", "mcp"}
    assert all(
        row.reason == "shared lib land: libs/deploy_identity/__init__.py"
        for row in rows
    )
    assert all(row.code_ref == "land-sha" for row in rows)
    assert skipped == []
    assert prose is False


def test_default_proof_strings_are_obligation_not_observation():
    from implement_admission.propagation_row import (
        default_proof,
        proof_claims_performed_ancestry,
    )

    for service in (
        "git_integration_worker",
        "mcp",
        "cortex_api",
        "agent_bus",
        "rag",
        "event_service",
    ):
        proof = default_proof(service)
        assert "AFTER restart VERIFY" in proof, service
        assert not proof_claims_performed_ancestry(proof), proof
        assert "ancestry satisfied" not in proof.lower(), proof


def test_compose_proof_process_live_giw_is_process_identity_not_openapi():
    expected = (
        "service health/liveness → AFTER restart VERIFY code_ref is "
        "ancestor-of-or-equal-to observed code_version AND VERIFY process identity "
        "changed (pid/process_start_time/process_age_s/uptime_s) since the "
        "pre-restart probe (git_integration_worker)"
    )
    assert compose_proof("git_integration_worker", "process_live") == expected
    row = PropagationRow(
        service="git_integration_worker",
        code_ref="abc123",
        proof_class="process_live",
    )
    assert row.proof == expected
    assert "OpenAPI" not in row.proof
    assert "x-mcp" not in row.proof


def test_compose_proof_missing_pair_raises_naming_both():
    with pytest.raises(
        MissingProofTemplateError,
        match=r"service='mcp'.*proof_class='served_artifact'",
    ):
        compose_proof("mcp", "served_artifact")


def test_compose_proof_matrix_present_and_absent_default_proof_services():
    """Each proof_class × service in/out of the old service-keyed table."""
    in_default = ("git_integration_worker", "mcp", "cortex_api", "agent_bus", "rag")
    absent_default = ("event_service", "gateway", "stargate", "cloud_proxy")

    for service in (*in_default, *absent_default):
        proof = compose_proof(service, "process_live")
        assert "process identity changed" in proof, service
        assert "OpenAPI" not in proof, service

    assert "client_visible" in compose_proof("mcp", "client_visible")
    for service in ("git_integration_worker", "cortex_api", "agent_bus", "rag"):
        proof = compose_proof(service, "served_artifact")
        assert "OpenAPI" in proof, service
        # null expected_x_mcp_count ⇒ no unbound ">= expected" clause
        assert ">= expected" not in proof, service
        assert "x-mcp count >=" not in proof, service

    bound = compose_proof(
        "git_integration_worker", "served_artifact", expected_x_mcp_count=9
    )
    assert "x-mcp count >= 9" in bound

    for service in absent_default:
        with pytest.raises(MissingProofTemplateError) as excinfo:
            compose_proof(service, "served_artifact")
        assert service in str(excinfo.value)
        assert "served_artifact" in str(excinfo.value)
        with pytest.raises(MissingProofTemplateError) as excinfo:
            compose_proof(service, "client_visible")
        assert service in str(excinfo.value)
        assert "client_visible" in str(excinfo.value)

    for service in ("git_integration_worker", "cortex_api", "agent_bus", "rag"):
        with pytest.raises(MissingProofTemplateError) as excinfo:
            compose_proof(service, "client_visible")
        assert f"service='{service}'" in str(excinfo.value)
        assert "client_visible" in str(excinfo.value)
