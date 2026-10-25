"""Unit tests for mechanical-land journal fold (§13 stage 1)."""

from __future__ import annotations

import pytest

from mechanical_land.fold import fold, fresh_state
from mechanical_land.schema import (
    JOURNAL_EVENT_NAMES,
    PAYLOAD_TYPES,
    JournalEntry,
    JournalForkRefused,
    RunState,
    UnknownJournalEvent,
    parse_payload,
)

pytestmark = pytest.mark.offline

RUN_ID = "ml-20607replay"
HEAD_SHA = "54024c5c6" + "0" * 32
MASTER_BEFORE = "a" * 40
MASTER_AFTER = "b" * 40
IMPLEMENT_DISPATCH = "d-imp-20607"
FIX_DISPATCH = "d-fix-20607"
REVIEW_DISPATCH = "d-rev-20607"


def _entry(
    seq: int,
    *,
    prev_seq: int,
    step_id: str,
    attempt: int,
    flow: str,
    event: str,
    payload: dict,
    source: str = "reactor",
) -> JournalEntry:
    return JournalEntry(
        seq=seq,
        prev_seq=prev_seq,
        step_id=step_id,
        attempt=attempt,
        flow=flow,
        event=event,
        payload=payload,
        source=source,
    )


def test_fold_empty_is_fresh_state() -> None:
    assert fold([]) == fresh_state()
    fresh = fresh_state()
    assert fresh.status is None
    assert fresh.step is None
    assert fresh.attempt == 0
    assert fresh.flow == "main"
    assert fresh.legs == {}
    assert fresh.tip_seq == 0


def test_every_event_has_dataclass_and_parse() -> None:
    assert JOURNAL_EVENT_NAMES == frozenset(PAYLOAD_TYPES)
    for name in sorted(JOURNAL_EVENT_NAMES):
        assert name in PAYLOAD_TYPES


def test_prev_seq_mismatch_raises_journal_fork_refused() -> None:
    events = [
        _entry(
            1,
            prev_seq=0,
            step_id="run",
            attempt=1,
            flow="main",
            event="run_birthed",
            payload={
                "kind": "implement",
                "source_ref": "todo:x",
                "policy_sha256": "abc",
                "caller": "grok-bot-gc1",
            },
        ),
        _entry(
            2,
            prev_seq=0,
            step_id="run",
            attempt=1,
            flow="main",
            event="policy_stamped",
            payload={"policy": {}},
        ),
    ]
    with pytest.raises(JournalForkRefused):
        fold(events)


def test_unknown_event_raises() -> None:
    events = [
        _entry(
            1,
            prev_seq=0,
            step_id="run",
            attempt=1,
            flow="main",
            event="not_in_spec",
            payload={},
        ),
    ]
    with pytest.raises(UnknownJournalEvent):
        fold(events)


def test_duplicate_leg_closed_by_dispatch_id_is_noop() -> None:
    close_payload = {
        "dispatch_id": IMPLEMENT_DISPATCH,
        "closeout_status": "complete",
        "work_outcome": "landed",
        "head_sha": HEAD_SHA,
        "branch": "cursor-sdk/lane-20607",
        "branch_point": MASTER_BEFORE,
        "files_modified": ["a.py", "b.py", "c.py"],
    }
    events = [
        _entry(
            1,
            prev_seq=0,
            step_id="implement",
            attempt=1,
            flow="main",
            event="leg_admitted",
            payload={
                "step_id": "implement",
                "attempt": 1,
                "dispatch_id": IMPLEMENT_DISPATCH,
                "execution_id": "e1",
                "model": "cursor/composer-2.5",
                "contract": "implement",
                "lane": "B",
                "sdk_mode": None,
                "packet_sha256": "p1",
            },
        ),
        _entry(
            2,
            prev_seq=1,
            step_id="implement",
            attempt=1,
            flow="main",
            event="leg_closed",
            payload=close_payload,
        ),
        _entry(
            3,
            prev_seq=2,
            step_id="implement",
            attempt=1,
            flow="main",
            event="leg_closed",
            payload=dict(close_payload, closeout_status="partial"),
        ),
    ]
    once = fold(events[:2])
    twice = fold(events)
    assert twice.last_closeout is not None
    assert twice.last_closeout.closeout_status == "complete"
    assert twice.tip_seq == 3
    assert once.last_closeout == twice.last_closeout
    assert len(twice.legs["implement"]) == len(once.legs["implement"])


def test_flow_and_attempt_carried_through() -> None:
    events = [
        _entry(
            1,
            prev_seq=0,
            step_id="gate",
            attempt=2,
            flow="alt-a",
            event="transition_decided",
            payload={
                "from_step": "fix",
                "to_step": "gate",
                "attempt": 2,
                "reason": "fix closed ok",
            },
        ),
    ]
    state = fold(events)
    assert state.step == "gate"
    assert state.attempt == 2
    assert state.flow == "alt-a"


def test_all_payload_dataclasses_roundtrip_minimal() -> None:
    samples: dict[str, dict] = {
        "run_birthed": {
            "kind": "implement",
            "source_ref": "todo:t",
            "policy_sha256": "sha",
            "caller": "c",
        },
        "policy_stamped": {"policy": {"gate": {"ruff": True}}},
        "transition_decided": {
            "from_step": "a",
            "to_step": "b",
            "attempt": 1,
            "reason": "r",
        },
        "leg_admitted": {
            "step_id": "implement",
            "attempt": 1,
            "dispatch_id": "d1",
            "execution_id": "e1",
            "model": "m",
            "contract": "implement",
            "lane": "B",
            "packet_sha256": "ps",
        },
        "leg_admit_failed": {"status_code": 502, "error": "bad"},
        "leg_closed": {
            "dispatch_id": "d1",
            "closeout_status": "complete",
            "work_outcome": "ok",
            "head_sha": HEAD_SHA,
            "branch": "b",
            "branch_point": "bp",
        },
        "gate_ran": {
            "attempt": 1,
            "head_sha": HEAD_SHA,
            "worktree": "/tmp/wt",
            "verdict": "red",
            "ruff": {"exit": 1, "files": ["a.py"]},
            "pytest": {"exit": 0, "selected": []},
        },
        "review_admitted": {
            "dispatch_id": REVIEW_DISPATCH,
            "model": "cdp/opus-5.5",
            "range": f"{MASTER_BEFORE}..{HEAD_SHA}",
            "head": HEAD_SHA,
        },
        "review_verdict": {
            "tip_sha": HEAD_SHA,
            "verdict": "APPROVE",
            "review_dispatch_id": REVIEW_DISPATCH,
            "bind_status": "bound",
        },
        "review_fallback_admitted": {"model": "cursor/grok-4.7"},
        "land_attempted": {
            "tip_sha": HEAD_SHA,
            "before_sha": MASTER_BEFORE,
            "after_sha": MASTER_AFTER,
            "landed": True,
        },
        "restart_planned": {
            "land_sha": MASTER_AFTER,
            "restart_set": ["universal-stargate"],
        },
        "restart_issued": {
            "service": "universal-stargate",
            "intent_id": "ri-1",
            "state": "pending",
        },
        "probe_ran": {
            "service": "universal-stargate",
            "observed_code_version": MASTER_AFTER,
            "live": True,
        },
        "closed": {
            "todo_close_result": "ok",
            "friction_close_result": ["friction:40243"],
        },
        "evidence_harvested": {
            "sidecar_ref": "tmp/reviews/closeouts/x.md",
            "sha256": "s",
            "status": "complete",
            "excerpt_sha256": "e",
        },
        "ruling_folded": {
            "verb": "HOLD",
            "step": "gate",
            "reason": "wait",
            "bus_thread": "15851",
            "turn": 1,
        },
        "ruling_ignored": {"bus_thread": "15851", "turn": 2, "why": "bad grammar"},
        "blocked": {
            "step_id": "gate",
            "attempt": 2,
            "reason_code": "gate_red_after_fix",
            "detail": "still red",
        },
        "resumed": {"by_ruling_seq": 5},
        "flow_forked": {"from_seq": 10},
        "flow_joined": {"survivor_flow": "main"},
    }
    for event_name, payload in samples.items():
        parsed = parse_payload(event_name, payload)
        assert parsed is not None


def _synthetic_20607_journal() -> list[JournalEntry]:
    """30-event happy-path journal mirroring friction:40243 / §9 result shape."""
    rows: list[tuple[str, str, int, str, dict]] = [
        (
            "run",
            "run_birthed",
            1,
            "run",
            {
                "kind": "implement",
                "source_ref": "todo:mechanical-land-pipeline",
                "policy_sha256": "pol" + "0" * 61,
                "caller": "grok-bot-gc1",
            },
        ),
        ("run", "policy_stamped", 1, "run", {"policy": {"gate": {"ruff": True}}}),
        (
            "run",
            "transition_decided",
            1,
            "run",
            {
                "from_step": "run",
                "to_step": "implement",
                "attempt": 1,
                "reason": "birth",
            },
        ),
        (
            "implement",
            "leg_admitted",
            1,
            "implement",
            {
                "step_id": "implement",
                "attempt": 1,
                "dispatch_id": IMPLEMENT_DISPATCH,
                "execution_id": "ex-imp",
                "model": "cursor/composer-2.5",
                "contract": "implement",
                "lane": "B",
                "sdk_mode": None,
                "packet_sha256": "pkt-imp",
            },
        ),
        (
            "implement",
            "leg_closed",
            1,
            "implement",
            {
                "dispatch_id": IMPLEMENT_DISPATCH,
                "closeout_status": "complete",
                "work_outcome": "checks_failed",
                "head_sha": HEAD_SHA,
                "branch": "cursor-sdk/lane-20607",
                "branch_point": MASTER_BEFORE,
                "files_modified": [
                    "pipelines/writing/v1/handlers/foo.py",
                    "pipelines/writing/v1/handlers/bar.py",
                    "pipelines/writing/v1/handlers/baz.py",
                ],
            },
        ),
        (
            "gate",
            "transition_decided",
            1,
            "gate",
            {
                "from_step": "implement",
                "to_step": "gate",
                "attempt": 1,
                "reason": "implement closed",
            },
        ),
        (
            "gate",
            "gate_ran",
            1,
            "gate",
            {
                "attempt": 1,
                "head_sha": HEAD_SHA,
                "worktree": "/tmp/ml-gate-1",
                "verdict": "red",
                "ruff": {"exit": 1, "files": ["pipelines/writing/v1/handlers/foo.py"]},
                "pytest": {"exit": 0, "selected": []},
            },
        ),
        (
            "fix",
            "transition_decided",
            1,
            "fix",
            {
                "from_step": "gate",
                "to_step": "fix",
                "attempt": 1,
                "reason": "gate red",
            },
        ),
        (
            "fix",
            "leg_admitted",
            1,
            "fix",
            {
                "step_id": "fix",
                "attempt": 1,
                "dispatch_id": FIX_DISPATCH,
                "execution_id": "ex-fix",
                "model": "cursor/composer-2.5",
                "contract": "freeform",
                "lane": "B",
                "sdk_mode": None,
                "packet_sha256": "pkt-fix",
            },
        ),
        (
            "fix",
            "leg_closed",
            1,
            "fix",
            {
                "dispatch_id": FIX_DISPATCH,
                "closeout_status": "complete",
                "work_outcome": "landed",
                "head_sha": HEAD_SHA,
                "branch": "cursor-sdk/lane-20607",
                "branch_point": MASTER_BEFORE,
                "files_modified": ["pipelines/writing/v1/handlers/foo.py"],
            },
        ),
        (
            "gate",
            "transition_decided",
            2,
            "gate",
            {
                "from_step": "fix",
                "to_step": "gate",
                "attempt": 2,
                "reason": "fix closed",
            },
        ),
        (
            "gate",
            "gate_ran",
            2,
            "gate",
            {
                "attempt": 2,
                "head_sha": HEAD_SHA,
                "worktree": "/tmp/ml-gate-2",
                "verdict": "green",
                "ruff": {"exit": 0, "files": []},
                "pytest": {
                    "exit": 0,
                    "selected": ["pipelines/writing/v1/tests/test_foo.py"],
                },
            },
        ),
        (
            "review",
            "transition_decided",
            1,
            "review",
            {
                "from_step": "gate",
                "to_step": "review",
                "attempt": 1,
                "reason": "gate green",
            },
        ),
        (
            "review",
            "review_admitted",
            1,
            "review",
            {
                "dispatch_id": REVIEW_DISPATCH,
                "model": "cdp/opus-5.5",
                "range": f"{MASTER_BEFORE}..{HEAD_SHA}",
                "head": HEAD_SHA,
            },
        ),
        (
            "review",
            "review_verdict",
            1,
            "review",
            {
                "tip_sha": HEAD_SHA,
                "verdict": "APPROVE",
                "review_dispatch_id": REVIEW_DISPATCH,
                "bind_status": "bound",
            },
        ),
        (
            "land",
            "transition_decided",
            1,
            "land",
            {
                "from_step": "review",
                "to_step": "land",
                "attempt": 1,
                "reason": "review approve",
            },
        ),
        (
            "land",
            "land_attempted",
            1,
            "land",
            {
                "tip_sha": HEAD_SHA,
                "before_sha": MASTER_BEFORE,
                "after_sha": MASTER_AFTER,
                "landed": True,
            },
        ),
        (
            "restart",
            "transition_decided",
            1,
            "restart",
            {
                "from_step": "land",
                "to_step": "restart",
                "attempt": 1,
                "reason": "landed",
            },
        ),
        (
            "restart",
            "restart_planned",
            1,
            "restart",
            {
                "land_sha": MASTER_AFTER,
                "restart_set": ["universal-stargate"],
                "porcelain_block": [],
                "unmapped": [],
            },
        ),
        (
            "restart",
            "restart_issued",
            1,
            "restart",
            {
                "service": "universal-stargate",
                "intent_id": "intent-stargate-1",
                "state": "pending_drain",
            },
        ),
        (
            "probe",
            "transition_decided",
            1,
            "probe",
            {
                "from_step": "restart",
                "to_step": "probe",
                "attempt": 1,
                "reason": "restart issued",
            },
        ),
        (
            "probe",
            "probe_ran",
            1,
            "probe",
            {
                "service": "universal-stargate",
                "observed_code_version": MASTER_AFTER,
                "live": True,
            },
        ),
        (
            "close",
            "transition_decided",
            1,
            "close",
            {
                "from_step": "probe",
                "to_step": "close",
                "attempt": 1,
                "reason": "live",
            },
        ),
        (
            "close",
            "closed",
            1,
            "close",
            {
                "todo_close_result": "ok",
                "friction_close_result": ["friction:40243"],
            },
        ),
    ]
    # Pad to 30 journal rows with no-op ruling_ignored + transition bookkeeping.
    rows.extend(
        [
            (
                "run",
                "ruling_ignored",
                1,
                "run",
                {
                    "bus_thread": "20607",
                    "turn": 99,
                    "why": "prose only",
                },
            ),
            (
                "gate",
                "transition_decided",
                1,
                "gate",
                {
                    "from_step": "gate",
                    "to_step": "gate",
                    "attempt": 1,
                    "reason": "pipeline noop",
                },
            ),
            (
                "fix",
                "transition_decided",
                1,
                "fix",
                {
                    "from_step": "fix",
                    "to_step": "fix",
                    "attempt": 1,
                    "reason": "pipeline noop",
                },
            ),
            (
                "review",
                "transition_decided",
                1,
                "review",
                {
                    "from_step": "review",
                    "to_step": "review",
                    "attempt": 1,
                    "reason": "pipeline noop",
                },
            ),
            (
                "land",
                "transition_decided",
                1,
                "land",
                {
                    "from_step": "land",
                    "to_step": "land",
                    "attempt": 1,
                    "reason": "pipeline noop",
                },
            ),
            (
                "close",
                "transition_decided",
                1,
                "close",
                {
                    "from_step": "close",
                    "to_step": "close",
                    "attempt": 1,
                    "reason": "pipeline noop",
                },
            ),
        ]
    )
    assert len(rows) == 30
    out: list[JournalEntry] = []
    prev = 0
    for idx, (step_id, event, attempt, step_col, payload) in enumerate(rows, start=1):
        out.append(
            _entry(
                idx,
                prev_seq=prev,
                step_id=step_col,
                attempt=attempt,
                flow="main",
                event=event,
                payload=payload,
            )
        )
        prev = idx
    return out


def test_fold_synthetic_20607_sequence_matches_section9_shape() -> None:
    events = _synthetic_20607_journal()
    state = fold(events)
    state.run_id = RUN_ID
    result = state.result_json()
    expected = {
        "run_id": RUN_ID,
        "kind": "implement",
        "status": "closed",
        "source_ref": "todo:mechanical-land-pipeline",
        "legs": [
            {
                "step": "implement",
                "attempt": 1,
                "dispatch_id": IMPLEMENT_DISPATCH,
                "closeout_status": "complete",
                "head_sha": HEAD_SHA,
            },
            {
                "step": "fix",
                "attempt": 1,
                "dispatch_id": FIX_DISPATCH,
                "closeout_status": "complete",
                "head_sha": HEAD_SHA,
            },
        ],
        "gate": [
            {"attempt": 1, "verdict": "red", "ruff_exit": 1, "pytest_exit": 0},
            {"attempt": 2, "verdict": "green", "ruff_exit": 0, "pytest_exit": 0},
        ],
        "review": {
            "tip_sha": HEAD_SHA,
            "verdict": "APPROVE",
            "review_dispatch_id": REVIEW_DISPATCH,
        },
        "land": {"before_sha": MASTER_BEFORE, "after_sha": MASTER_AFTER},
        "restart": {
            "restart_set": ["universal-stargate"],
            "intents": [
                {
                    "service": "universal-stargate",
                    "intent_id": "intent-stargate-1",
                    "state": "pending_drain",
                }
            ],
        },
        "probe": {
            "universal-stargate": {
                "observed_code_version": MASTER_AFTER,
                "live": True,
            }
        },
        "close": {"todo_close": "ok", "friction_close": ["friction:40243"]},
        "evidence": None,
        "model_turns_in_control_flow": 0,
        "journal": f"cortex://notes/system/runs/mechanical-land/{RUN_ID}.md",
        "tip_seq": 30,
    }
    assert result == expected
    assert state.step == "close"
    assert state.attempt == 1
    assert state.flow == "main"


def test_fresh_state_type() -> None:
    assert isinstance(fold([]), RunState)
