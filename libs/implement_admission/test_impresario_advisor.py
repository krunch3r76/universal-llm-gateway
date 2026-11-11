"""Advisor duty admission — K74 / disposition 16416#2841."""

from __future__ import annotations

import copy

import pytest

from implement_admission.impresario_policy import (
    ADVISOR_LEAN_DISPATCH_LINE,
    advisor_model_wire,
    evaluate_advisor_dispatch_admission,
    load_impresario_policy,
    parse_impresario_policy,
    refuse_argon_non_advisor_route,
    resolve_advisor_provider_fallback,
)
from implement_admission.routing import load_route_policy

pytestmark = pytest.mark.offline

_ARGON = "cursor/gemini-4-argon"

_TRIGGERED_PACKET = (
    "Intent to plan: how to land K74.\n"
    f"{ADVISOR_LEAN_DISPATCH_LINE}\n"
    "Scope: route policy only.\n"
)


def test_advisor_wire_is_cdp_fable() -> None:
    assert advisor_model_wire() == "cdp/fable"


def test_advisor_admits_when_unpaused_with_lean_dispatch_line() -> None:
    pol = load_impresario_policy()
    assert pol.advisor.paused is False
    result = evaluate_advisor_dispatch_admission(
        model=advisor_model_wire(),
        packet_body=_TRIGGERED_PACKET,
        item_author="cursor/grok-4.7",
        policy=pol,
    )
    assert result.admitted is True
    assert result.resolved_model == advisor_model_wire()


def test_advisor_refused_when_paused_true() -> None:
    policy = copy.deepcopy(load_route_policy())
    policy["impresario"] = copy.deepcopy(policy["impresario"])
    policy["impresario"]["advisor"] = copy.deepcopy(policy["impresario"]["advisor"])
    policy["impresario"]["advisor"]["paused"] = True
    pol = parse_impresario_policy(policy)
    result = evaluate_advisor_dispatch_admission(
        model=advisor_model_wire(),
        packet_body=_TRIGGERED_PACKET,
        policy=pol,
    )
    assert result.admitted is False
    assert result.code == "ADVISOR_PAUSED"


def test_advisor_rejects_packet_without_lean_dispatch_line() -> None:
    result = evaluate_advisor_dispatch_admission(
        model=advisor_model_wire(),
        packet_body="Intent to plan only.\n",
    )
    assert result.admitted is False
    assert result.code == "ADVISOR_LEAN_DISPATCH_REQUIRED"


def test_advisor_rejects_when_item_author_matches_advisor_wire() -> None:
    result = evaluate_advisor_dispatch_admission(
        model=advisor_model_wire(),
        packet_body=_TRIGGERED_PACKET,
        item_author=advisor_model_wire(),
    )
    assert result.admitted is False
    assert result.code == "ADVISOR_K40_AUTHOR"


@pytest.mark.parametrize(
    "role",
    ["critic", "ear", "implement", "sweep", "sketch", "skeptic", "review"],
)
def test_argon_barred_for_non_advisor_roles(role: str) -> None:
    result = refuse_argon_non_advisor_route(model=_ARGON, impresario_role=role)
    assert result is not None
    assert result.admitted is False
    assert result.code == "ARGON_ROLE_BARRED"


def test_argon_fast_suffix_barred_for_non_advisor() -> None:
    fast = "cursor/gemini-4-argon-fast"
    result = refuse_argon_non_advisor_route(
        model=fast,
        impresario_role="critic",
    )
    assert result is not None
    assert result.code == "ARGON_ROLE_BARRED"


def test_provider_error_falls_back_to_grok_with_event() -> None:
    err = "You've hit your usage limit ... reset when your monthly cycle ends on 11/13/2026"
    result = resolve_advisor_provider_fallback(provider_error=err)
    assert result.admitted is True
    assert result.resolved_model == "cursor/composer-2.5"
    assert result.provider_fallback is True
    assert result.event_signal == "impresario.advisor.provider_fallback"
    assert result.event_payload is not None
    assert result.event_payload["requested_model"] == advisor_model_wire()


def test_omitted_critic_model_defaults_to_opus_not_argon() -> None:
    policy = copy.deepcopy(load_route_policy())
    imp = copy.deepcopy(policy["impresario"])
    imp.pop("critic", None)
    policy["impresario"] = imp
    pol = parse_impresario_policy(policy)
    assert pol.critic.model == "anthropic/claude-sonnet-5-5"
