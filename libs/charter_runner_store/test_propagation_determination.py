"""Unit tests for propagation probe classification and evaluability gates."""

from __future__ import annotations

from deploy_identity.code_ref_relation import code_ref_relation_from_observed

from charter_runner_store.propagation_determination import (
    proof_evaluable,
    proof_payload_requirements,
)

_SHA = "abc1230000000000000000000000000000000000"


def test_proof_payload_requirements_by_class() -> None:
    assert proof_payload_requirements("client_visible") == frozenset(
        {"mcp_health", "cortex_api"}
    )
    assert proof_payload_requirements("served_artifact") == frozenset({"surfaces"})
    assert proof_payload_requirements("process_live") == frozenset({"code_version"})


def test_proof_evaluable_process_live_flat_code_version() -> None:
    assert proof_evaluable({"code_version": _SHA}, proof_class="process_live")


def test_proof_evaluable_client_visible_flat_code_version_unevaluable() -> None:
    """Flat top-level code_version cannot run the client_visible predicate."""
    assert not proof_evaluable({"code_version": _SHA}, proof_class="client_visible")


def test_proof_evaluable_client_visible_composite_evaluable() -> None:
    payload = {
        "mcp_health": {"code_version": _SHA},
        "cortex_api": {"code_version": _SHA},
    }
    assert proof_evaluable(payload, proof_class="client_visible")


def test_proof_evaluable_served_artifact_null_code_version_evaluable() -> None:
    """rag:d3e17d54-class: surfaces + byte_identical with code_version=null is valid."""
    relation = code_ref_relation_from_observed(_SHA, None)
    payload = {
        "proof_class": "served_artifact",
        "surfaces": {
            "uds": {"x_mcp_count": 7, "bytes_sha256": "deadbeef"},
        },
        "byte_identical": True,
        "x_mcp_count": 7,
        "code_version": None,
        "code_ref_relation": relation,
    }
    assert proof_evaluable(payload, proof_class="served_artifact")


def test_proof_evaluable_served_artifact_flat_code_version_only_unevaluable() -> None:
    """code_version alone does not satisfy served_artifact — predicate needs surfaces."""
    assert not proof_evaluable({"code_version": _SHA}, proof_class="served_artifact")
