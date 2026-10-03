"""Stale wire vocabulary must not survive on the friction ticket hint.

Breaks when a caller copies `_next` into team_dispatch: `job=` is not an MCP
param (contract forwards as the Stargate body job), and seat=cursor /
seat=web-anthropic are handoff-only (422 web_seat_not_generate_target).
"""

from __future__ import annotations

import re

from job_vocab import GENERATE_ADMITTED_JOBS

from cortex_store.dispatch_ops.workflow_hints import _WORKFLOW_HINTS

_EXECUTE = re.compile(
    r"execute default = team_dispatch\("
    r"op=generate, seat=(?P<seat>[^,]+), contract=(?P<contract>[^,]+),"
)


def test_friction_next_uses_contract_and_generate_seat() -> None:
    for tool in ("friction", "frictions"):
        nxt = _WORKFLOW_HINTS[tool]
        assert "job=" not in nxt
        match = _EXECUTE.search(nxt)
        assert match is not None, nxt
        assert match.group("seat") == "cursor-sdk"
        assert match.group("contract") in GENERATE_ADMITTED_JOBS
        assert "contract=confer" in nxt
        assert "confer" in GENERATE_ADMITTED_JOBS
