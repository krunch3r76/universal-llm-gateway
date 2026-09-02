"""Offline tests — workflow registry loader and conformance (P0)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import (
    AUTO_JUDGMENT_WORKFLOW,
    AUTO_OMIT_CONTRACTS,
    CHECK_REVIEW_WORKFLOW,
    MECHANICAL_WORKFLOW,
    assert_workflow_registry_boot_conformance,
    embed_workflow_registry_block,
    load_workflow_registry,
    parse_workflow_registry,
    registry_errors,
    render_workflow_registry_block,
    verify_seat_default_parity,
    verify_workflow_registry_conformance,
    verify_workflow_registry_drift,
)
from services.git_integration_worker.cursor_auto.wire_map import (
    resolve_desired_effort,
    resolve_desired_model,
)

pytestmark = pytest.mark.offline


def test_live_file_conformance() -> None:
    assert verify_workflow_registry_conformance() == []


def test_verify_seat_default_parity_live() -> None:
    assert verify_seat_default_parity() == []


def test_r9_seat_default_mismatch(tmp_path: Path) -> None:
    policy = _base_policy()
    policy["workflows"][AUTO_JUDGMENT_WORKFLOW]["model"] = "cursor/gpt-5.6-terra"
    agents = tmp_path / "agents.yaml"
    agents.write_text(
        "profiles:\n  cursor/sdk:\n    default_model: cursor/composer-2.5\n",
        encoding="utf-8",
    )
    errors = verify_seat_default_parity(policy=policy, agents_path=agents)
    assert any("R9:" in e and "cursor/composer-2.5" in e for e in errors)


def test_assert_workflow_registry_boot_conformance_passes() -> None:
    assert_workflow_registry_boot_conformance()


def test_committed_skill_block_is_drift_free() -> None:
    skill = (
        Path(__file__).resolve().parents[2]
        / "cursor-plugins/ulg-ecosystem/skills/consult-routing/SKILL.md"
    )
    assert skill.is_file()
    assert verify_workflow_registry_drift(skill) is True


def test_load_live_registry_slots_and_roaming() -> None:
    reg = load_workflow_registry()
    assert set(reg.workflows) == {
        MECHANICAL_WORKFLOW,
        CHECK_REVIEW_WORKFLOW,
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
    assert any(
        "workflows.investigate.seat" in e and "unknown-seat" in e for e in errors
    )


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
    assert any(
        "allowed_workflows references unknown workflow 'ghost-slot'" in e
        for e in errors
    )


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
    assert any(
        f"workflows must include {MECHANICAL_WORKFLOW!r} slot" in e for e in errors
    )


def test_r8_contract_effort_missing_block() -> None:
    policy = _base_policy()
    del policy["contract_effort"]
    errors = registry_errors(policy)
    assert any("contract_effort must be a non-empty mapping" in e for e in errors)


def test_r8_contract_effort_unknown_contract() -> None:
    policy = _base_policy()
    policy["contract_effort"]["not-a-contract"] = "medium"
    errors = registry_errors(policy)
    assert any("contract_effort.'not-a-contract'" in e for e in errors)


def test_r8_contract_effort_invalid_rung() -> None:
    policy = _base_policy()
    policy["contract_effort"]["answer"] = "bogus"
    errors = registry_errors(policy)
    assert any("contract_effort.answer='bogus'" in e for e in errors)


def test_r8_contract_effort_unclaimed_canonical() -> None:
    policy = _base_policy()
    del policy["contract_effort"]["answer"]
    errors = registry_errors(policy)
    assert any("contract 'answer' missing from contract_effort" in e for e in errors)


def test_contract_effort_live_defaults_match_omit_path() -> None:
    reg = load_workflow_registry()
    assert reg.default_effort_for_contract("investigate") == "xhigh"
    assert reg.default_effort_for_contract("answer") == "medium"
    out = resolve_desired_effort(None, contract="investigate", registry=reg)
    assert out["resolved_effort"] == "xhigh"
    assert "via contract_effort" in out["notes"]


def test_falsifier_contract_effort_yaml_without_wire_map_edit() -> None:
    policy = _base_policy()
    policy["contract_effort"]["answer"] = "high"
    reg = parse_workflow_registry(policy)
    out = resolve_desired_effort(None, contract="answer", registry=reg)
    assert out["resolved_effort"] == "high"
    assert (
        resolve_desired_effort(None, contract="implement", registry=reg)[
            "resolved_effort"
        ]
        == "medium"
    )


def test_render_workflow_registry_block_lists_live_slots() -> None:
    block = render_workflow_registry_block()
    assert "| check_review |" in block
    assert "| auto_judgment |" in block
    assert "workflows.auto_judgment.model" in block
    assert "not folded" in block
    assert "| answer | medium |" in block


def test_embed_and_drift_roundtrip(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "# Consult Routing\n\n## cursor-sdk model name surfaces\n\n| Surface | SoT |\n"
        "|---|---|\n| foo | bar |\n\n## Next\n",
        encoding="utf-8",
    )
    patched = embed_workflow_registry_block(skill.read_text(encoding="utf-8"))
    skill.write_text(patched, encoding="utf-8")
    assert verify_workflow_registry_drift(skill) is True


def test_workflow_registry_drift_fails_on_divergence(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    block = render_workflow_registry_block().replace(
        "auto_judgment", "auto_judgment-stale"
    )
    skill.write_text(f"# skill\n\n{block}\n", encoding="utf-8")
    assert verify_workflow_registry_drift(skill) is False
