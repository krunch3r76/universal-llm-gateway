"""Import handlers and dry-run parse/assemble on fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from operator_hop_harvest.assemble import (
    assemble_operator_hop_view,
    cap_view_json_bytes,
)
from operator_hop_harvest.parse import parse_conductor_closeout, parse_scoreboard

pytestmark = pytest.mark.offline


def test_register_handlers_import() -> None:
    from pipelines.operator_hop_harvest.v1.handlers import register_handlers

    assert callable(register_handlers)


def test_fixture_assemble_smoke() -> None:
    fixture = (
        Path(__file__).resolve().parents[4]
        / "libs"
        / "operator_hop_harvest"
        / "fixtures"
        / "12263_turn7_closeout.md"
    )
    body = fixture.read_text(encoding="utf-8")
    conductor = parse_conductor_closeout(
        body=body,
        dispatch_id="aabc385580a4-daf128a9",
        hop_seq=2,
        work_outcome=None,
        degraded_reason=None,
        closeout_turn=7,
        closeout_uri=None,
        usage=None,
        branch=None,
        head_sha=None,
        commits_ahead=None,
    )
    scoreboard = parse_scoreboard(body="", uri="", sha256="")
    view = assemble_operator_hop_view(
        worker_thread={"id": "12263"},
        summoning_thread={"id": "12088"},
        conductor=conductor,
        scoreboard=scoreboard,
        wait={"open": False, "kind": "none"},
        job=None,
        harvest_recipe={"threads": []},
        continuation={"auto": False, "reason": "smoke"},
        next_admit_divergent=False,
    )
    assert cap_view_json_bytes(view)
