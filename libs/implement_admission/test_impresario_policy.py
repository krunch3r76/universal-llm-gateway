"""Pattern A impresario policy — forks 1, 8 flag, amendment 1."""

from __future__ import annotations

import copy

import pytest
from universal_protocol.errors import ProtocolError

from implement_admission.impresario_policy import (
    load_impresario_policy,
    parse_impresario_policy,
    policy_snapshot_sha256,
    resolve_roles,
)
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import registry_errors

pytestmark = pytest.mark.offline


def test_default_critic_is_gemini_argon_high() -> None:
    roles = resolve_roles()
    assert roles.critic.model == "cursor/gemini-4-argon"
    assert roles.critic.effort_rung == "high"


def test_empty_skeptic_inherits_high_not_extra() -> None:
    roles = resolve_roles()
    assert roles.skeptic.model == "cursor/gemini-4-argon"
    assert roles.skeptic.effort_rung == "high"


def test_explicit_extra_on_argon_fails_closed() -> None:
    with pytest.raises(ProtocolError) as excinfo:
        resolve_roles(critic_effort="extra")
    assert excinfo.value.code == "CRITIC_EFFORT_UNSUPPORTED"


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
