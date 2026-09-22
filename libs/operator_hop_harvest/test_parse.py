"""AC5 — parse closeout fixtures from agent-bus:12263 turns 7 and 11."""

from __future__ import annotations

from pathlib import Path

import pytest

from operator_hop_harvest.parse import (
    compute_next_admit_divergent,
    parse_conductor_closeout,
    parse_wait_block,
)

pytestmark = pytest.mark.offline

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_parse_turn7_row_hop_stop_tokens() -> None:
    body = _load("12263_turn7_closeout.md")
    out = parse_conductor_closeout(
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
    assert "ROW_HOP" in out["stop_tokens"]
    assert out.get("next_admit") is None or "G3" in str(out.get("next_admit"))


def test_parse_turn11_from_fixture_file() -> None:
    path = _FIXTURES / "12263_turn11_closeout.md"
    if not path.is_file():
        pytest.skip("turn 11 sidecar fixture unavailable")
    body = path.read_text(encoding="utf-8")
    out = parse_conductor_closeout(
        body=body,
        dispatch_id="0e048a74c604-eb383ae1",
        hop_seq=3,
        work_outcome=None,
        degraded_reason=None,
        closeout_turn=11,
        closeout_uri=None,
        usage=None,
        branch=None,
        head_sha=None,
        commits_ahead=None,
    )
    assert isinstance(out["stop_tokens"], list)


def test_consult_pending_wait_on_12291_fixture() -> None:
    root = Path(__file__).resolve().parents[2]
    body = (
        root
        / "services/git_integration_worker/tests/fixtures/operator_ear/12291_turn3_closeout.txt"
    ).read_text(encoding="utf-8")
    wait = parse_wait_block(closeout_body=body, recon_sidecar_body=None)
    assert wait["kind"] == "CONSULT_PENDING"
    assert wait["open"] is True


def test_next_admit_divergent() -> None:
    assert compute_next_admit_divergent(
        closeout_next_admit="harvest G2",
        scoreboard_next_admit="G3 densify",
        harvest_next_admit="harvest G2",
    )
