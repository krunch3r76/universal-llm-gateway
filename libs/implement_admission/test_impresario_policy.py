"""Pattern A impresario policy — forks 1, 8 flag, amendment 1."""

from __future__ import annotations

import copy

import pytest

from implement_admission.impresario_policy import (
    load_impresario_policy,
    parse_impresario_policy,
    policy_snapshot_sha256,
    resolve_roles,
)
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import registry_errors

pytestmark = pytest.mark.offline


def test_default_critic_is_anthropic_sonnet_high() -> None:
    roles = resolve_roles()
    assert roles.critic.model == "anthropic/claude-sonnet-5-5"
    assert roles.critic.effort_rung == "high"


def test_empty_skeptic_inherits_high_not_extra() -> None:
    roles = resolve_roles()
    assert roles.skeptic.model == "anthropic/claude-sonnet-5-5"
    assert roles.skeptic.effort_rung == "high"


def test_explicit_extra_on_anthropic_critic_resolves() -> None:
    roles = resolve_roles(critic_effort="extra")
    assert roles.critic.model == "anthropic/claude-sonnet-5-5"


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


def test_default_critic_standard_anthropic_not_high_cost() -> None:
    from anthropic_credit.config import load_credit_config
    from systems.frontier_consult.anthropic_override_gate import (
        evaluate_anthropic_override,
    )

    critic_model = resolve_roles().critic.model
    cfg = load_credit_config()
    assert critic_model == "anthropic/claude-sonnet-5-5"
    assert critic_model in cfg.standard_models
    assert critic_model not in cfg.high_cost_models
    verdict = evaluate_anthropic_override(
        model=critic_model,
        profile_provider="cursor",
        profile_allowed_models=frozenset(),
        credit_healthy=True,
        standard_models=frozenset(cfg.standard_models),
    )
    assert verdict.admitted is True
    assert verdict.code is None
