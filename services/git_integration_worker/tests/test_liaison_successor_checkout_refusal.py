"""Liaison successor closeout refuses a checkout commit; implement and conductor land."""

from __future__ import annotations

import json

from services.git_integration_worker.cursor_sdk_closeout.liaison_successor_checkout import (
    REFUSAL_REASON,
    apply_liaison_successor_checkout_refusal,
    liaison_successor_checkout_refusal,
)

_CHECKOUT_LAND = {
    "status": "complete",
    "work_outcome": "shipped",
    "landed": True,
    "commits_ahead": 1,
    "lane": "B",
    "isolation_materialized": True,
    "summary": "dispatch hop",
    "deviations": [],
}


def test_successor_closeout_with_checkout_commit_is_refused() -> None:
    reason = liaison_successor_checkout_refusal(
        caller_agent="liaison-ticker",
        contract="none",
        closeout=_CHECKOUT_LAND,
    )
    assert reason == REFUSAL_REASON
    body, applied = apply_liaison_successor_checkout_refusal(
        json.dumps(_CHECKOUT_LAND),
        caller_agent="liaison-ticker",
        contract="none",
    )
    assert applied == REFUSAL_REASON
    payload = json.loads(body)
    assert payload["status"] == "failed"
    assert payload["degraded_reason"] == REFUSAL_REASON
    assert payload["work_outcome"] == "not_shipped"
    assert f"refuse:{REFUSAL_REASON}" in payload["deviations"]


def test_successor_shared_checkout_commits_ahead_is_refused() -> None:
    """A non-isolated tree is the checkout; commits_ahead is the land."""
    closeout = {
        "status": "complete",
        "landed": False,
        "commits_ahead": 1,
        "lane": "A",
        "isolation_materialized": False,
    }
    assert (
        liaison_successor_checkout_refusal(
            caller_agent="liaison-ticker",
            contract="none",
            closeout=closeout,
        )
        == REFUSAL_REASON
    )


def test_successor_isolated_branch_commit_is_not_a_checkout_land() -> None:
    closeout = {
        "status": "partial",
        "landed": False,
        "commits_ahead": 1,
        "lane": "B",
        "isolation_materialized": True,
    }
    assert (
        liaison_successor_checkout_refusal(
            caller_agent="liaison-ticker",
            contract="none",
            closeout=closeout,
        )
        is None
    )


def test_implement_checkout_land_still_succeeds() -> None:
    body = json.dumps(_CHECKOUT_LAND)
    rewritten, reason = apply_liaison_successor_checkout_refusal(
        body,
        caller_agent="liaison-ticker",
        contract="implement",
    )
    assert reason is None
    assert rewritten == body
    assert json.loads(rewritten)["status"] == "complete"


def test_conductor_checkout_land_still_succeeds() -> None:
    body = json.dumps(_CHECKOUT_LAND)
    rewritten, reason = apply_liaison_successor_checkout_refusal(
        body,
        caller_agent="liaison-ticker",
        contract="conductor",
    )
    assert reason is None
    assert rewritten == body
    assert json.loads(rewritten)["status"] == "complete"


def test_other_none_caller_is_not_this_hop() -> None:
    assert (
        liaison_successor_checkout_refusal(
            caller_agent="cursor",
            contract="none",
            closeout=_CHECKOUT_LAND,
        )
        is None
    )
