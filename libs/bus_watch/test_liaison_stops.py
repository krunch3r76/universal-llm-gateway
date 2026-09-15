"""Typed operator gate provenance (a:34092)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bus_watch.hop_qualify import hop_qualifies
from bus_watch.liaison_stops import (
    apply_policy_set,
    operator_gate_armed,
    read_operator_gate,
    stamp_operator_gate,
)

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TICK_SCRIPT = _REPO_ROOT / "scripts" / "liaison-tick.py"


def test_operator_sourced_gate_refuses_hop() -> None:
    policy = stamp_operator_gate(True, as_of="2026-09-15T12:00:00Z")
    q = hop_qualifies(row="R16 implement packet", policy={"operator_gate": policy})
    assert q == {"ok": False, "reason": "operator_gate"}


def test_gate_text_in_now_row_without_operator_source_qualifies() -> None:
    q = hop_qualifies(row="R15 3c wake parked OPERATOR_GATE")
    assert q == {"ok": True, "reason": "dispatchable_now"}


def test_now_row_with_gate_text_does_not_arm_operator_gate() -> None:
    policy = apply_policy_set(
        {},
        {"now_row": "R15 parked OPERATOR_GATE credentials"},
        as_of="2026-09-15T12:00:00Z",
    )
    assert policy["now_row"] == "R15 parked OPERATOR_GATE credentials"
    assert not operator_gate_armed(policy)
    assert read_operator_gate(policy) is None


def test_arm_via_operator_path_records_source_and_as_of() -> None:
    rec = stamp_operator_gate(
        {"row": "credentials for deploy"}, as_of="2026-09-15T12:00:00Z"
    )
    assert rec["value"] is True
    assert rec["source"] == "operator"
    assert rec["as_of"] == "2026-09-15T12:00:00Z"
    assert rec["row"] == "credentials for deploy"
    policy = apply_policy_set({}, {"operator_gate": True}, as_of="2026-09-15T13:00:00Z")
    assert operator_gate_armed(policy)
    stored = read_operator_gate(policy)
    assert stored is not None
    assert stored["source"] == "operator"
    assert stored["as_of"] == "2026-09-15T13:00:00Z"


def test_clear_operator_gate() -> None:
    policy = apply_policy_set(
        {"operator_gate": stamp_operator_gate(True, as_of="2026-09-15T12:00:00Z")},
        {"operator_gate": "clear"},
        as_of="2026-09-15T13:00:00Z",
    )
    rec = read_operator_gate(policy)
    assert rec is not None
    assert rec["value"] is False
    assert rec["source"] == "operator"
    assert not operator_gate_armed(policy)


def test_hold_merge_and_land_owed_regression() -> None:
    q = hop_qualifies(row="HOLD_MERGE 10561 6eb762bf + 10567 bb1ca6bd; 3b parked")
    assert q == {"ok": False, "reason": "hold_merge"}
    q = hop_qualifies(row="LAND OWED 10561 after AC-9")
    assert q["ok"] is False
    assert q["reason"] == "hold_merge"


def test_liaison_tick_now_row_set_does_not_arm_gate(tmp_path: Path) -> None:
    state_path = tmp_path / "liaison-99999.tick.json"
    state_path.write_text(
        json.dumps({"policy": {}, "register": "autonomous"}), encoding="utf-8"
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(_TICK_SCRIPT),
            "--root",
            "99999",
            "--state-file",
            str(state_path),
            "--set",
            "now_row=R15 parked OPERATOR_GATE credentials",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    stdout = proc.stdout.strip().splitlines()[-1]
    payload = json.loads(stdout)
    policy = payload["policy"]
    assert "OPERATOR_GATE" in policy.get("now_row", "")
    assert not operator_gate_armed(policy)
