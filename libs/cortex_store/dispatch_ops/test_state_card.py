"""Unit tests for work-item state card helpers."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops.state_card import (
    derive_next_action,
    merge_state_card,
    state_card_defaults,
)


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
