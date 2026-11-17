"""Pattern A impresario policy — forks 1, 8 flag, amendment 1."""

from __future__ import annotations

import copy

import pytest

from implement_admission.impresario_policy import (
    critic_for_role,
    load_impresario_policy,
    parse_impresario_policy,
    policy_snapshot_sha256,
    resolve_roles,
)
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import registry_errors

pytestmark = pytest.mark.offline


def test_default_critic_is_cdp_opus_high() -> None:
    roles = resolve_roles()
    assert roles.critic.model == "cdp/opus-5.5"
    assert roles.critic.effort_rung == "high"


def test_empty_skeptic_inherits_high_not_extra() -> None:
    roles = resolve_roles()
    assert roles.skeptic.model == "cdp/opus-5.5"
    assert roles.skeptic.effort_rung == "high"


def test_explicit_extra_on_critic_resolves() -> None:
    roles = resolve_roles(critic_effort="extra")
    assert roles.critic.model == "cdp/opus-5.5"


def test_hybrid_mechanical_binds_composer() -> None:
    policy = load_route_policy()
    mutated = copy.deepcopy(policy)
    mutated["impresario"] = copy.deepcopy(mutated["impresario"])
    mutated["impresario"]["under_substrate"] = "hybrid"
    parsed = parse_impresario_policy(mutated)
    roles = resolve_roles(parsed)
    assert roles.mechanical.model == "cursor/composer-2.5"
    assert roles.mechanical.seat == "cursor-sdk"


def test_invalid_under_substrate_registry_error() -> None:
    policy = load_route_policy()
    mutated = copy.deepcopy(policy)
    mutated["impresario"] = copy.deepcopy(mutated["impresario"])
    mutated["impresario"]["under_substrate"] = "invalid-substrate"
    errors = registry_errors(mutated)
    assert any("under_substrate" in e for e in errors)


def test_policy_snapshot_sha256_stable() -> None:
    a = policy_snapshot_sha256()
    b = load_impresario_policy().policy_sha256
    assert a == b


def test_cdp_reroute_for_critic_default_false() -> None:
    assert load_impresario_policy().cdp_reroute_for_critic is False


def test_grok_allowed_critic_in_policy() -> None:
    assert "cursor/grok-4.7" in load_impresario_policy().allowed_critics


def test_resolve_roles_grok_critic_model() -> None:
    roles = resolve_roles(critic_model="cursor/grok-4.7")
    assert roles.critic.model == "cursor/grok-4.7"
    assert roles.critic.effort_rung == "high"


def test_critic_for_role_review_grok_override() -> None:
    policy = load_route_policy()
    mutated = copy.deepcopy(policy)
    mutated["impresario"] = copy.deepcopy(mutated["impresario"])
    mutated["impresario"]["critic_roles"] = copy.deepcopy(
        mutated["impresario"]["critic_roles"]
    )
    mutated["impresario"]["critic_roles"]["review"] = {"model": "cursor/grok-4.7"}
    parsed = parse_impresario_policy(mutated)
    assert critic_for_role(parsed, "review").model == "cursor/grok-4.7"


def test_registry_errors_clean_after_grok_allowed_critic() -> None:
    errors = registry_errors(load_route_policy())
    assert not any("allowed critic" in e.lower() for e in errors)
    assert not any("impresario" in e.lower() for e in errors)


def test_default_critic_is_cdp_not_anthropic_credit_bucket() -> None:
    from anthropic_credit.config import load_credit_config
    from implement_admission.conductor_critic_render import _usage_channel

    critic_model = resolve_roles().critic.model
    cfg = load_credit_config()
    assert critic_model == "cdp/opus-5.5"
    assert critic_model.startswith("cdp/")
    assert critic_model not in cfg.standard_models
    assert critic_model not in cfg.high_cost_models
    assert _usage_channel(critic_model) == "Opus"
