"""Offline tests — workflow registry loader and conformance (P0)."""

from __future__ import annotations

import copy

import pytest

from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import (
    AUTO_OMIT_CONTRACTS,
    MECHANICAL_WORKFLOW,
    load_workflow_registry,
    parse_workflow_registry,
    registry_errors,
    verify_workflow_registry_conformance,
)
from services.git_integration_worker.cursor_auto.wire_map import resolve_desired_model

pytestmark = pytest.mark.offline


def test_live_file_conformance() -> None:
    assert verify_workflow_registry_conformance() == []


def test_load_live_registry_slots_and_roaming() -> None:
    reg = load_workflow_registry()
    assert set(reg.workflows) == {
        MECHANICAL_WORKFLOW,
        "investigate",
        "auto_judgment",
    }
    assert reg.roaming_bare_models() == frozenset(
        {"composer-2.5", "composer-2.5-fast", "grok-4.6"}
    )


def test_parse_raises_on_errors() -> None:
    with pytest.raises(ValueError, match="workflows must be a non-empty mapping"):
        parse_workflow_registry({})


@pytest.mark.parametrize("contract", sorted(AUTO_OMIT_CONTRACTS))
def test_auto_resolves_via_workflow_slug(contract: str) -> None:
    reg = load_workflow_registry()
    binding = reg.workflow_for_contract(contract)
    assert binding is not None
    out = resolve_desired_model("auto", contract=contract, registry=reg)
    assert out["resolved_model_id"] == binding.model
    assert f"via workflows.{binding.slug}" in out["notes"]


def test_falsifier_investigate_grok_implement_stays_composer() -> None:
    policy = copy.deepcopy(load_route_policy())
    policy["workflows"]["investigate"] = {
        **policy["workflows"]["investigate"],
        "model": "cursor/grok-4.6",
    }
    reg = parse_workflow_registry(policy)
    inv = resolve_desired_model("auto", contract="investigate", registry=reg)
    impl = resolve_desired_model("auto", contract="implement", registry=reg)
    assert inv["resolved_model_id"] == "cursor/grok-4.6"
    assert impl["resolved_model_id"] == "cursor/composer-2.5"
    assert "via workflows.investigate" in inv["notes"]


def _base_policy() -> dict:
    return copy.deepcopy(load_route_policy())


def test_r1_workflows_must_be_nonempty_mapping() -> None:
    policy = _base_policy()
    del policy["workflows"]
    errors = registry_errors(policy)
    assert any("workflows must be a non-empty mapping" in e for e in errors)


def test_r1_workflow_entry_must_be_mapping() -> None:
    policy = _base_policy()
    policy["workflows"]["bad"] = "not-a-mapping"
    errors = registry_errors(policy)
    assert any("workflows.bad must be a mapping" in e for e in errors)


def test_r2_invalid_seat() -> None:
    policy = _base_policy()
    policy["workflows"]["investigate"]["seat"] = "unknown-seat"
    errors = registry_errors(policy)
    assert any("workflows.investigate.seat" in e and "unknown-seat" in e for e in errors)


def test_r3_invalid_model_for_cursor_sdk_seat() -> None:
    policy = _base_policy()
    policy["workflows"]["investigate"]["model"] = "openai/gpt-5.6-terra"
    errors = registry_errors(policy)
    assert any("workflows.investigate.model" in e for e in errors)


def test_r4_unknown_contract_in_workflow() -> None:
    policy = _base_policy()
    policy["workflows"]["investigate"]["contracts"].append("not-a-contract")
    errors = registry_errors(policy)
    assert any("unknown contract 'not-a-contract'" in e for e in errors)


def test_r4_duplicate_contract_claim() -> None:
    policy = _base_policy()
    policy["workflows"]["auto_judgment"]["contracts"].append("investigate")
    errors = registry_errors(policy)
    assert any("contract 'investigate' claimed by both" in e for e in errors)


def test_r4_unclaimed_contract() -> None:
    policy = _base_policy()
    policy["workflows"]["investigate"]["contracts"] = ["recon", "seed"]
    errors = registry_errors(policy)
    assert any("contract 'investigate' is not claimed" in e for e in errors)


def test_r5_unknown_model_bare_id() -> None:
    policy = _base_policy()
    policy["models"]["not-a-real-model"] = {"roaming": True}
    errors = registry_errors(policy)
    assert any("models.not-a-real-model not a known cursor" in e for e in errors)


def test_r5_allowed_workflows_unknown_slot() -> None:
    policy = _base_policy()
    policy["models"]["composer-2.5"]["allowed_workflows"] = ["ghost-slot"]
    errors = registry_errors(policy)
    assert any("allowed_workflows references unknown workflow 'ghost-slot'" in e for e in errors)


def test_r6_deprecated_model_without_allowed_workflow() -> None:
    policy = _base_policy()
    policy["models"]["composer-2.5"] = {
        "roaming": True,
        "deprecated": True,
        "allowed_workflows": [],
    }
    errors = registry_errors(policy)
    assert any("deprecated bare id 'composer-2.5'" in e for e in errors)


def test_r7_mechanical_workflow_required() -> None:
    policy = _base_policy()
    del policy["workflows"][MECHANICAL_WORKFLOW]
    errors = registry_errors(policy)
    assert any(f"workflows must include {MECHANICAL_WORKFLOW!r} slot" in e for e in errors)
