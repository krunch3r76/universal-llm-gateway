"""Unit tests for work-item state card helpers and D4 route totality."""

from __future__ import annotations

import itertools

import pytest

from cortex_store.dispatch_ops.state_card import (
    derive_next_action,
    derive_work_item_route,
    merge_state_card,
    state_card_defaults,
)

_BIND_STATUSES = ("unsettled", "settled", "shipping", "deferred")
_TRIAGE_VALUES = ("judgment_required", "recon_pending", "mechanical")


@pytest.mark.parametrize(
    ("workflow", "stage", "bind_status", "expected"),
    [
        ("path_sim", "recon", "unsettled", "run_recon_or_path_sim"),
        ("path_sim", "implement", "deferred", "await_unblock"),
        ("path_sim", "implement", "settled", "run_address_or_ship"),
        ("address", "pickup", "settled", "advance_address"),
        ("address", "advance", "shipping", "verify_and_close"),
        ("address", "pickup", "deferred", "await_unblock"),
        ("path_sim", "q", "unsettled", "inspect_card_and_route"),
    ],
)
def test_derive_next_action(
    workflow: str, stage: str, bind_status: str, expected: str
) -> None:
    assert derive_next_action(workflow, stage, bind_status) == expected


def test_state_card_defaults_match_promote_contract() -> None:
    defaults = state_card_defaults()
    assert defaults == {
        "workflow": "path_sim",
        "stage": "recon",
        "bind_status": "unsettled",
        "next_action": "run_recon_or_path_sim",
    }


def test_merge_state_card_fills_missing_and_recomputes_next_action() -> None:
    merged = merge_state_card({"workflow": "address", "stage": "pickup"})
    assert merged["bind_status"] == "unsettled"
    assert merged["next_action"] == "inspect_card_and_route"


def test_merge_state_card_bind_status_change_refreshes_next_action() -> None:
    base = merge_state_card({})
    settled = merge_state_card({**base, "bind_status": "settled"})
    assert settled["next_action"] == "run_address_or_ship"


def test_merge_state_card_stage_change_refreshes_next_action() -> None:
    base = merge_state_card({"bind_status": "settled", "workflow": "address"})
    advanced = merge_state_card({**base, "stage": "pickup"})
    assert advanced["next_action"] == "advance_address"


def test_promote_card_routes_path_sim() -> None:
    defaults = state_card_defaults()
    route = derive_work_item_route(
        bind_status=defaults["bind_status"],
        density_triage="recon_pending",
    )
    assert route == "PATH-SIM"


def test_unsettled_judgment_required_not_address() -> None:
    assert (
        derive_work_item_route(
            bind_status="unsettled",
            density_triage="judgment_required",
        )
        == "PATH-SIM"
    )


def test_settled_not_default_path_sim() -> None:
    assert (
        derive_work_item_route(
            bind_status="settled",
            density_triage="judgment_required",
        )
        == "ADDRESS"
    )


@pytest.mark.parametrize(
    ("bind_status", "density_triage", "implement_ready", "expected"),
    [
        ("deferred", "judgment_required", False, "held"),
        ("settled", "judgment_required", False, "ADDRESS"),
        ("shipping", "mechanical", True, "ADDRESS"),
        ("unsettled", "recon_pending", False, "PATH-SIM"),
        ("unsettled", "judgment_required", False, "PATH-SIM"),
        ("unsettled", "mechanical", False, "DISPATCH"),
        ("unsettled", "mechanical", True, "DISPATCH"),
        ("settled", "recon_pending", False, "PATH-SIM"),
        ("unsettled", "unknown", False, "PATH-SIM"),
    ],
)
def test_d4_route_cases(
    bind_status: str,
    density_triage: str,
    implement_ready: bool,
    expected: str,
) -> None:
    route = derive_work_item_route(
        bind_status=bind_status,
        density_triage=density_triage,
        implement_ready=implement_ready,
    )
    assert route == expected


def test_d4_totality_bind_status_by_triage() -> None:
    """Every v0 bind_status × triage combo yields exactly one route bucket."""
    allowed = {"ADDRESS", "PATH-SIM", "DISPATCH", "held"}
    for bind_status, triage in itertools.product(_BIND_STATUSES, _TRIAGE_VALUES):
        for implement_ready in (False, True):
            route = derive_work_item_route(
                bind_status=bind_status,
                density_triage=triage,
                implement_ready=implement_ready,
            )
            assert route in allowed
