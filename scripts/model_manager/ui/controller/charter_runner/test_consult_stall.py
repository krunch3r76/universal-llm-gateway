"""Unit tests for consult-stall recovery (a:26131)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

# Phase 3: consult_stall deleted — skip until ported to consult_lane states.
pytestmark = pytest.mark.skip(reason="Phase 3: consult_stall deleted")

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore, WindowCaps
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.admission import Decision
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.kernel import (
    CharterRunnerTickLoop,
)

_PRIOR = """\
# CHECKPOINT — CONSULT_PENDING

## Steps
1. [x] G1 — Q
2. [x] G2 — A
3. [ ] G3 — R-admit
4. [ ] G4 — implement

## In-flight / WIP
none

## Next pickup
1. CONSULT_PENDING — G3 R-admit · consult_role: r_admit

## Frictions
_None this window._

Scoreboard: cortex://notes/system/threads/5693-charter-scoreboard.md

— RESUME (any seat, no command): load agent-bus-discipline → scoreboard.
"""


def test_default_consult_stale_shorter_than_hour() -> None:
    assert DEFAULT_CONSULT_STALE_S == 900.0
    assert DEFAULT_CONSULT_STALE_S < 3600.0


def test_find_r_admit_after_ignores_pre_admission() -> None:
    turns = [
        {"turn_number": 10, "subject": "R-ADMIT early"},
        {"turn_number": 13, "subject": "WIP charter-runner window 4"},
        {
            "turn_number": 14,
            "subject": "R-ADMIT (G3) — ADMIT_WITH_AMENDMENTS",
        },
    ]
    hit = find_r_admit_after(turns, after_n=13)
    assert hit is not None
    assert int(hit["turn_number"]) == 14
    assert find_r_admit_after(turns, after_n=14) is None


def test_find_r_admit_after_requires_admitting_verdict() -> None:
    assert (
        find_r_admit_after(
            [{"turn_number": 14, "subject": "R-ADMIT (G3) — REJECT"}],
            after_n=13,
        )
        is None
    )
    assert (
        find_r_admit_after(
            [{"turn_number": 14, "subject": "R-ADMIT (G3)"}],
            after_n=13,
        )
        is None
    )
    admitted = find_r_admit_after(
        [
            {
                "turn_number": 14,
                "subject": "R-ADMIT (G3)",
                "body": "Verdict: ADMIT",
            }
        ],
        after_n=13,
    )
    assert admitted is not None


def test_latest_reject_supersedes_earlier_admit() -> None:
    turns = [
        {"turn_number": 14, "subject": "R-ADMIT — ADMIT"},
        {"turn_number": 15, "subject": "R-ADMIT — REJECT"},
    ]
    assert find_r_admit_after(turns, after_n=13) is None


def test_build_r_admit_advance_clears_consult_without_adjudicating_g3() -> None:
    prior = parse_checkpoint(_PRIOR)
    assert prior.consult_pending
    r_turn = {
        "turn_number": 14,
        "subject": "R-ADMIT (G3) — ADMIT_WITH_AMENDMENTS",
    }
    subject, body = build_r_admit_advance_checkpoint(
        prior=prior,
        window_index=4,
        worker_thread="5754",
        r_admit_turn=r_turn,
        generation=2,
    )
    assert subject.startswith("CHECKPOINT")
    assert "r_admit_on_root" in subject
    assert "heal:consult_stall gen=2" in subject
    parsed = parse_checkpoint(body)
    assert not parsed.consult_pending
    assert parsed.wip_is_none
    assert any(s.ordinal == 3 and s.status == "pending" for s in parsed.steps)
    assert parsed.next_pickup_gated
    assert any(
        "G3" in item and "read R-ADMIT at turn 14" in item
        for item in parsed.next_pickup
    )
    assert "verdict: ADMIT_WITH_AMENDMENTS" in body


def test_consult_stall_heal_count_survives_reset(tmp_path) -> None:
    caps = CapStore(intent_dir=tmp_path / "intent")
    assert caps.increment_consult_stall_heal("root") == 1
    caps.reset("root")
    assert caps.get_consult_stall_heal_count("root") == 1
    assert caps.get_heal_count("root") == 0


def _decision() -> Decision:
    admission = {
        "turn_number": 13,
        "body": json.dumps(
            {
                "window": 4,
                "worker_thread": "5754",
                "admission_mode": "consult",
                "posted_at": "2000-01-01T00:00:00+00:00",
            }
        ),
    }
    return Decision(
        False,
        "window_in_flight",
        "root",
        checkpoint={"turn_number": 12, "body": _PRIOR},
        admission_turn=admission,
    )


def test_recovery_fences_before_checkpoint_and_tags_requeue(
    monkeypatch, tmp_path
) -> None:
    old = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    root_turns = [
        _decision().admission_turn,
        {
            "turn_number": 14,
            "subject": "cdp execution_id=exec-4 remained open",
            "created_at": old,
        },
    ]
    calls: list[str] = []
    posted: dict[str, str] = {}

    async def fetch_turns(thread_id: str) -> list[dict]:
        calls.append(f"fetch:{thread_id}")
        if thread_id == "5754":
            return [{"turn_number": 1, "created_at": old}]
        return root_turns

    async def close_worker(worker_thread: str, *, summary: str) -> dict:
        calls.append("close")
        assert "consult_stall_requeue" in summary
        return {}

    async def post_checkpoint(
        root_id: str, *, subject: str, body: str, to: str = "charter-runner"
    ) -> dict:
        calls.append("post")
        posted.update(subject=subject, body=body)
        return {}

    async def harvest(root_id: str, turns: list[dict]) -> None:
        calls.append("harvest")

    async def emit(**kwargs) -> None:
        calls.append("emit")

    monkeypatch.setattr(consult_stall.bus_client, "fetch_turns", fetch_turns)
    monkeypatch.setattr(consult_stall.bus_client, "close_worker_thread", close_worker)
    monkeypatch.setattr(
        consult_stall.bus_client, "post_root_checkpoint", post_checkpoint
    )
    monkeypatch.setattr(consult_stall, "harvest_completed_windows", harvest)
    monkeypatch.setattr(
        consult_stall,
        "file_charter_protocol_friction",
        lambda **_kwargs: 99999,
    )
    monkeypatch.setattr(
        consult_stall.events,
        "emit_manage_charter_tick_consult_stall_recovered",
        emit,
        raising=False,
    )

    caps = CapStore(
        WindowCaps(max_consecutive=1, max_per_hour=1),
        intent_dir=tmp_path / "intent",
    )
    caps.record_admit("root")
    recovered = asyncio.run(
        consult_stall.try_recover_consult_stall(
            _decision(),
            root_turns=root_turns,
            caps=caps,
            age_s=1200,
            admission_mode="consult",
        )
    )

    assert recovered
    assert calls.index("close") < calls.index("post") < calls.index("harvest")
    assert "heal:consult_stall gen=1" in posted["subject"]
    assert "abandoned_worker: 5754" in posted["body"]
    assert "supersedes_window: 4" in posted["body"]
    assert "supersedes:4" in posted["body"]
    assert "cdp execution_id=exec-4 remained open" in posted["body"]
    assert caps.get_consult_stall_heal_count("root") == 1
    assert caps.get_heal_count("root") == 0
    assert caps.check("root")[0]


def test_recent_worker_activity_blocks_recovery(monkeypatch, tmp_path) -> None:
    now = datetime.now(UTC).isoformat()
    calls: list[str] = []

    async def fetch_turns(thread_id: str) -> list[dict]:
        calls.append("fetch")
        return [{"turn_number": 1, "created_at": now}]

    async def close_worker(worker_thread: str, *, summary: str) -> dict:
        calls.append("close")
        return {}

    monkeypatch.setattr(consult_stall.bus_client, "fetch_turns", fetch_turns)
    monkeypatch.setattr(consult_stall.bus_client, "close_worker_thread", close_worker)
    recovered = asyncio.run(
        consult_stall.try_recover_consult_stall(
            _decision(),
            root_turns=[_decision().admission_turn],
            caps=CapStore(intent_dir=tmp_path / "intent"),
            age_s=1200,
            admission_mode="consult",
        )
    )
    assert not recovered
    assert calls == ["fetch"]


def test_hard_stale_prefers_self_heal_before_consult_stall(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []
    loop = CharterRunnerTickLoop(
        service_state=MagicMock(),
        shutdown_gate=MagicMock(),
        caps=CapStore(intent_dir=tmp_path / "intent"),
        unattended_stale_s=1,
    )

    async def self_heal(decision: Decision, turns: list[dict], env: EnvSnapshot) -> bool:
        calls.append("self_heal")
        return True

    async def consult_recovery(
        decision: Decision, turns: list[dict], env: EnvSnapshot
    ) -> bool:
        calls.append("consult_stall")
        return True

    empty_env = EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up", "project_ask": "up"},
        attendance_by_root={"5609": "attended"},
        scoreboard_pointer={},
        bus_tip_meta={},
    )

    monkeypatch.setattr(loop, "_try_self_heal", self_heal)
    monkeypatch.setattr(loop, "_try_consult_stall", consult_recovery)
    asyncio.run(loop._handle_waiting_open(_decision(), [], empty_env))
    assert calls == ["self_heal"]
