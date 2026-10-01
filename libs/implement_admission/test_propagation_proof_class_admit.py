"""Tests for propagation admit proof_class validation (arc 6637 AC3)."""

from __future__ import annotations

from implement_admission.propagation_admit_validation import (
    legal_proof_classes,
    validate_proof_class,
)
from implement_admission.propagation_row import rows_from_parsed_block


def test_rag_served_artifact_admits():
    rows, flags = rows_from_parsed_block(
        [
            {
                "service": "rag",
                "code_ref": "abc",
                "proof_class": "served_artifact",
            }
        ]
    )
    assert flags == []
    assert len(rows) == 1
    assert rows[0].proof_class == "served_artifact"


def test_legal_proof_classes_mcp_excludes_served_artifact():
    assert legal_proof_classes("mcp") == frozenset({"client_visible", "process_live"})
    assert validate_proof_class("mcp", "served_artifact") is not None
    assert validate_proof_class("mcp", "client_visible") is None


def test_legal_proof_classes_cortex_api_includes_served_artifact():
    legal = legal_proof_classes("cortex_api")
    assert "served_artifact" in legal
    assert "client_visible" not in legal
    assert validate_proof_class("cortex_api", "served_artifact") is None


def test_legal_proof_classes_unprobeable_excludes_process_live():
    """M2: process_live not legal when probe has no fetcher for the slug."""
    from services.git_integration_worker.relay.propagation_probe import (
        PROCESS_LIVE_EXCLUDED_SERVICES,
        process_live_probeable_services,
    )

    assert "email_bridge" in PROCESS_LIVE_EXCLUDED_SERVICES
    assert "email_bridge" not in process_live_probeable_services()
    legal = legal_proof_classes("email_bridge")
    assert "process_live" not in legal
    assert validate_proof_class("email_bridge", "process_live") is not None


def test_unknown_proof_class_names_legal_set_for_service():
    """AC1 (a:35350): unknown enum values must name legal classes, not refuse silently."""
    token = validate_proof_class("git_integration_worker", "code_version")
    assert token is not None
    assert "unknown_proof_class:code_version" in token
    assert "legal for git_integration_worker:" in token
    assert "process_live" in token
    assert "served_artifact" in token


def test_legal_proof_classes_probeable_includes_process_live():
    """M2: fetcher map keys keep process_live legal (oracle, not a frozen deny list)."""
    from services.git_integration_worker.relay.propagation_probe import (
        process_live_probeable_services,
    )

    for slug in process_live_probeable_services():
        assert "process_live" in legal_proof_classes(slug)
        assert validate_proof_class(slug, "process_live") is None
