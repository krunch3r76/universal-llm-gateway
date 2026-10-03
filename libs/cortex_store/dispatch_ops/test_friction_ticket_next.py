"""Stale wire vocabulary must not survive on the friction ticket hint.

Breaks when a caller copies `_next` into team_dispatch: `job=` is not an MCP
param (contract forwards as the Stargate body job), and seat=cursor /
seat=web-anthropic are handoff-only (422 web_seat_not_generate_target).

The live `cortex(tool=frictions)` path is handler-set `_next` on `_op_frictions`,
which shadows `_WORKFLOW_HINTS["frictions"]`. Tests that only read the dict miss
the string agents copy (a:37439).
"""

from __future__ import annotations

import re

import pytest
from job_vocab import GENERATE_ADMITTED_JOBS

from cortex_store.dispatch_ops.ops_assertions_friction import _op_frictions
from cortex_store.dispatch_ops.workflow_hints import _WORKFLOW_HINTS

_EXECUTE = re.compile(
    r"execute default = team_dispatch\("
    r"op=generate, seat=(?P<seat>[^,]+), contract=(?P<contract>[^,]+),"
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


def test_friction_write_hint_uses_contract_and_generate_seat() -> None:
    _assert_ticket_next(_WORKFLOW_HINTS["friction"])


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
