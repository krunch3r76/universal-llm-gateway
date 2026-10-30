"""Advisor duty admission — K74 / disposition 16416#2841."""

from __future__ import annotations

import copy

import pytest

from implement_admission.impresario_policy import (
    ADVISOR_LEAN_DISPATCH_LINE,
    ADVISOR_MODEL,
    evaluate_advisor_dispatch_admission,
    load_impresario_policy,
    parse_impresario_policy,
    refuse_argon_non_advisor_route,
    resolve_advisor_provider_fallback,
)
from implement_admission.routing import load_route_policy

pytestmark = pytest.mark.offline

_TRIGGERED_PACKET = (
    "Intent to plan: how to land K74.\n"
    f"{ADVISOR_LEAN_DISPATCH_LINE}\n"
    "Scope: route policy only.\n"
)


def test_advisor_admits_when_unpaused_with_lean_dispatch_line() -> None:
    pol = load_impresario_policy()
    assert pol.advisor.paused is False
    result = evaluate_advisor_dispatch_admission(
        model=ADVISOR_MODEL,
        packet_body=_TRIGGERED_PACKET,
        item_author="cursor/grok-4.7",
        policy=pol,
    )
    assert result.admitted is True
    assert result.resolved_model == ADVISOR_MODEL


def test_advisor_refused_when_paused_true() -> None:
    policy = copy.deepcopy(load_route_policy())
    policy["impresario"] = copy.deepcopy(policy["impresario"])
    policy["impresario"]["advisor"] = copy.deepcopy(policy["impresario"]["advisor"])
    policy["impresario"]["advisor"]["paused"] = True
    pol = parse_impresario_policy(policy)
    result = evaluate_advisor_dispatch_admission(
        model=ADVISOR_MODEL,
        packet_body=_TRIGGERED_PACKET,
        policy=pol,
    )
    assert result.admitted is False
    assert result.code == "ADVISOR_PAUSED"


def test_advisor_rejects_packet_without_lean_dispatch_line() -> None:
    result = evaluate_advisor_dispatch_admission(
        model=ADVISOR_MODEL,
        packet_body="Intent to plan only.\n",
    )
    assert result.admitted is False
    assert result.code == "ADVISOR_LEAN_DISPATCH_REQUIRED"


def test_advisor_rejects_when_item_author_is_argon() -> None:
    result = evaluate_advisor_dispatch_admission(
        model=ADVISOR_MODEL,
        packet_body=_TRIGGERED_PACKET,
        item_author=ADVISOR_MODEL,
    )
    assert result.admitted is False
    assert result.code == "ADVISOR_K40_AUTHOR"


@pytest.mark.parametrize(
    "role",
    ["critic", "ear", "implement", "sweep", "sketch", "skeptic", "review"],
)
def test_argon_barred_for_non_advisor_roles(role: str) -> None:
    result = refuse_argon_non_advisor_route(model=ADVISOR_MODEL, impresario_role=role)
    assert result is not None
    assert result.admitted is False
    assert result.code == "ARGON_ROLE_BARRED"


def test_provider_error_falls_back_to_grok_with_event() -> None:
    err = "You've hit your usage limit ... reset when your monthly cycle ends on 11/13/2026"
    result = resolve_advisor_provider_fallback(provider_error=err)
    assert result.admitted is True
    assert result.resolved_model == "cursor/composer-2.5"
    assert result.provider_fallback is True
    assert result.event_signal == "impresario.advisor.provider_fallback"
    assert result.event_payload is not None
    assert result.event_payload["requested_model"] == ADVISOR_MODEL


def test_omitted_critic_model_defaults_to_opus_not_argon() -> None:
    policy = copy.deepcopy(load_route_policy())
    imp = copy.deepcopy(policy["impresario"])
    imp.pop("critic", None)
    policy["impresario"] = imp
    pol = parse_impresario_policy(policy)
    assert pol.critic.model == "anthropic/claude-sonnet-5-5"
