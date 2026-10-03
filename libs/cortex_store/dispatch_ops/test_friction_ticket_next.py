"""Stale wire vocabulary must not survive on the friction ticket hint.

Breaks when a caller copies `_next` into team_dispatch: `job=` is not an MCP
param (contract forwards as the Stargate body job), and seat=cursor /
seat=web-anthropic are handoff-only (422 web_seat_not_generate_target).
A copied cursor-sdk generate that omits lane is 422 lane_required (a:37438).

The live `cortex(tool=frictions)` path is handler-set `_next` on `_op_frictions`,
which shadows `_WORKFLOW_HINTS["frictions"]`. Tests that only read the dict miss
the string agents copy (a:37439).
"""

from __future__ import annotations

import re

import pytest
from job_vocab import GENERATE_ADMITTED_JOBS
from systems.frontier_consult.cursor_sdk_lane_gate import (
    LANE_REQUIRED_CODE,
    FrontierEndpointError,
    require_cursor_sdk_checkout_lane,
)

from cortex_store.dispatch_ops.ops_assertions_friction import _op_frictions
from cortex_store.dispatch_ops.workflow_hints import _WORKFLOW_HINTS

_DISPATCH = re.compile(r"team_dispatch\(([^)]+)\)")
_EXECUTE = re.compile(
    r"execute default = team_dispatch\("
    r"op=generate, seat=(?P<seat>[^,]+), lane=\"B\", contract=(?P<contract>[^,]+),"
)


def _parse_dispatch_kwargs(inner: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in inner.split(","):
        key, _, raw = part.partition("=")
        parsed[key.strip()] = raw.strip().strip('"')
    return parsed


def _assert_copied_leg_admits(kwargs: dict[str, str]) -> None:
    assert kwargs.get("op") == "generate"
    assert kwargs.get("seat") == "cursor-sdk"
    assert kwargs.get("contract") in GENERATE_ADMITTED_JOBS
    require_cursor_sdk_checkout_lane(
        request_id="friction-ticket-next",
        lane=kwargs.get("lane"),
        nest_under=None,
        contract=kwargs.get("contract"),
    )


def _assert_ticket_next(nxt: str, *, require_friction_close: bool = False) -> None:
    assert "job=" not in nxt
    if require_friction_close:
        assert "friction_close" in nxt
    match = _EXECUTE.search(nxt)
    assert match is not None, nxt
    assert match.group("seat") == "cursor-sdk"
    assert match.group("contract") in GENERATE_ADMITTED_JOBS
    assert "contract=confer" in nxt
    assert "confer" in GENERATE_ADMITTED_JOBS
    legs = [_parse_dispatch_kwargs(inner) for inner in _DISPATCH.findall(nxt)]
    assert len(legs) == 2, nxt
    for kwargs in legs:
        _assert_copied_leg_admits(kwargs)


def test_friction_write_hint_uses_contract_and_generate_seat() -> None:
    _assert_ticket_next(_WORKFLOW_HINTS["friction"])


def test_copied_leg_without_lane_is_lane_required() -> None:
    inner = _DISPATCH.search(_WORKFLOW_HINTS["friction"])
    assert inner is not None
    kwargs = _parse_dispatch_kwargs(inner.group(1))
    kwargs.pop("lane", None)
    with pytest.raises(FrontierEndpointError) as exc:
        require_cursor_sdk_checkout_lane(
            request_id="friction-ticket-next-omit",
            lane=kwargs.get("lane"),
            nest_under=None,
            contract=kwargs.get("contract"),
        )
    assert exc.value.code == LANE_REQUIRED_CODE
    assert exc.value.status_code == 422


@pytest.mark.offline
def test_op_frictions_dispatched_next_uses_contract_not_job(monkeypatch) -> None:
    def fake_list(**kwargs: object) -> dict[str, object]:
        return {
            "intent": kwargs.get("intent"),
            "items": [
                {
                    "id": 1,
                    "claim": "[tool_error] x",
                    "confidence": "confirmed",
                }
            ],
        }

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_friction._list_assertions_impl",
        fake_list,
    )
    summary = _op_frictions(category="tool_error")
    _assert_ticket_next(summary["_next"], require_friction_close=True)
    assert "Deepen one row" in summary["_next"]
    full = _op_frictions(category="tool_error", intent="full")
    _assert_ticket_next(full["_next"], require_friction_close=True)
    assert "Deepen one row: cortex(tool=assertion_get" not in full["_next"]
