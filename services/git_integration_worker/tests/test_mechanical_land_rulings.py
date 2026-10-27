"""Mechanical-land RULING ingest and sweep tests (§13 stage 4)."""

# ruff: noqa: E402
from __future__ import annotations

pytest_plugins = ("services.git_integration_worker.tests.mechanical_land_isolation",)

from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.mechanical_land.reactor import run_reactor_loop
from services.git_integration_worker.mechanical_land.rulings import (
    ingest_rulings_for_run,
    parse_ruling_line,
)
from services.git_integration_worker.mechanical_land.store import MechanicalLandStore
from services.git_integration_worker.mechanical_land.sweep import (
    sweep_mechanical_land_runs,
)
from services.git_integration_worker.tests.mechanical_land_isolation import (
    empty_get_turns,
    reactor_deps,
)

pytestmark = pytest.mark.offline


_POLICY = {
    "models": {"implement": {"model": "cursor/composer-2.5", "knobs": {}}},
    "ruling_authors": ["web-anthropic"],
}


def _plan(**overrides: object) -> dict:
    base = {
        "admitted": True,
        "kind": "implement",
        "source_ref": "todo:mechanical-land-pipeline-s4",
        "policy": _POLICY,
        "policy_sha256": "pol" + "0" * 61,
        "dispatch_thread_id": "15851",
        "caller_agent": "grok-bot-gc1",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _seed_run(store: MechanicalLandStore) -> tuple[str, str]:
    with (
        patch.object(store, "_mint_lane_thread", return_value="9200"),
        patch.object(
            store,
            "_post_admit",
            return_value=(201, {"dispatch_id": "d1", "execution_id": "e1"}),
        ),
    ):
        run_id = store.birth_from_plan(_plan()).run_id
    return run_id, "9200"


def test_parse_ruling_line_canonical() -> None:
    text = "RULING: HOLD run=ml-abc step=gate attempt=1 reason=wait"
    parsed = parse_ruling_line(text, run_id="ml-abc")
    assert parsed is not None
    assert parsed["verb"] == "HOLD"


def test_canonical_ruling_folded() -> None:
    store = MechanicalLandStore()
    run_id, lane_thread = _seed_run(store)
    turns = [
        {
            "turn_number": 3,
            "from_agent": "web-anthropic",
            "body": "RULING: HOLD run=" + run_id,
        }
    ]

    def _get_turns(**_kwargs: object) -> list[dict]:
        return turns

    count = ingest_rulings_for_run(
        store,
        run_id=run_id,
        lane_thread_id=lane_thread,
        ruling_authors=["web-anthropic"],
        post_notice_fn=lambda **_k: (0, ""),
        get_turns_fn=_get_turns,
    )
    assert count >= 1
    snap = store.get_run(run_id, event_limit=30)
    assert snap is not None
    assert "ruling_folded" in [e["event"] for e in snap["events"]]


def test_prose_ruling_gets_ruling_ignored_and_notice() -> None:
    store = MechanicalLandStore()
    run_id, lane_thread = _seed_run(store)
    notices: list[str] = []

    def _notice(**kwargs: object) -> tuple[int, str]:
        notices.append(str(kwargs.get("run_id")))
        return 1, "nid"

    turns = [
        {
            "turn_number": 4,
            "from_agent": "web-anthropic",
            "body": f"Please HOLD run={run_id} thanks",
        }
    ]

    def _turns_for_lane(**kwargs: object) -> list[dict]:
        if kwargs.get("thread") == lane_thread:
            return turns
        return []

    ingest_rulings_for_run(
        store,
        run_id=run_id,
        lane_thread_id=lane_thread,
        ruling_authors=["web-anthropic"],
        post_notice_fn=_notice,
        get_turns_fn=_turns_for_lane,
    )
    snap = store.get_run(run_id, event_limit=30)
    assert snap is not None
    assert "ruling_ignored" in [e["event"] for e in snap["events"]]
    assert notices == [run_id]


def test_hold_stops_transition_admit() -> None:
    store = MechanicalLandStore()
    run_id, lane_thread = _seed_run(store)
    hold_turns = [
        {
            "turn_number": 1,
            "from_agent": "web-anthropic",
            "body": f"RULING: HOLD run={run_id}",
        }
    ]

    ingest_rulings_for_run(
        store,
        run_id=run_id,
        lane_thread_id=lane_thread,
        ruling_authors=["web-anthropic"],
        post_notice_fn=lambda **_k: (0, ""),
        get_turns_fn=lambda **_k: hold_turns,
    )

    admits: list[dict] = []

    def _fetch(**_k: object) -> dict:
        return {
            "transition": {
                "command": "admit",
                "step_id": "fix",
                "attempt": 1,
                "to_step": "fix",
            }
        }

    deps = reactor_deps(
        store,
        _fetch,
        post_admit_fn=lambda body: admits.append(body) or (201, {"dispatch_id": "d2"}),
        get_turns_fn=empty_get_turns,
    )
    run_reactor_loop(deps, run_id=run_id, max_sync_steps=1)
    assert admits == []


def test_resume_after_hold_allows_loop() -> None:
    store = MechanicalLandStore()
    run_id, lane_thread = _seed_run(store)
    seq = 0

    def _ingest(body: str) -> None:
        nonlocal seq

        def _get_turns(**_kwargs: object) -> list[dict]:
            return [
                {
                    "turn_number": seq + 1,
                    "from_agent": "web-anthropic",
                    "body": body,
                }
            ]

        ingest_rulings_for_run(
            store,
            run_id=run_id,
            lane_thread_id=lane_thread,
            ruling_authors=["web-anthropic"],
            post_notice_fn=lambda **_k: (0, ""),
            get_turns_fn=_get_turns,
        )
        seq += 1

    _ingest(f"RULING: HOLD run={run_id}")
    _ingest(f"RULING: RESUME run={run_id}")
    snap = store.get_run(run_id, event_limit=30)
    assert snap is not None
    events = [e["event"] for e in snap["events"]]
    assert "resumed" in events


def test_block_ruling_calls_steer_cancel() -> None:
    store = MechanicalLandStore()
    run_id, lane_thread = _seed_run(store)
    steers: list[str] = []
    ingest_rulings_for_run(
        store,
        run_id=run_id,
        lane_thread_id=lane_thread,
        ruling_authors=["web-anthropic"],
        post_notice_fn=lambda **_k: (0, ""),
        get_turns_fn=lambda **_k: [
            {
                "turn_number": 2,
                "from_agent": "web-anthropic",
                "body": f"RULING: BLOCK run={run_id} reason=stop",
            }
        ],
    )
    with patch(
        "services.git_integration_worker.mechanical_land.reactor._live_leg_dispatch_id",
        return_value="d1",
    ):
        deps = reactor_deps(
            store,
            lambda **_k: {"transition": {"command": "wait"}},
            post_admit_fn=lambda _b: (500, {}),
            post_steer_cancel_fn=lambda *, dispatch_id, reason: (
                steers.append(dispatch_id) or (True, {})
            ),
            get_turns_fn=empty_get_turns,
        )
        run_reactor_loop(deps, run_id=run_id, max_sync_steps=1)
    assert steers == ["d1"]


def test_sweep_hold_resume_without_terminal() -> None:
    store = MechanicalLandStore()
    run_id, lane_thread = _seed_run(store)
    store._set_run_status(run_id, "blocked_hold")

    def _ingest(body: str, turn_number: int) -> None:
        ingest_rulings_for_run(
            store,
            run_id=run_id,
            lane_thread_id=lane_thread,
            ruling_authors=["web-anthropic"],
            post_notice_fn=lambda **_k: (0, ""),
            get_turns_fn=lambda **_k: [
                {
                    "turn_number": turn_number,
                    "from_agent": "web-anthropic",
                    "body": body,
                }
            ],
        )

    _ingest(f"RULING: HOLD run={run_id}", 1)
    sweep_mechanical_land_runs(store=store)
    _ingest(f"RULING: RESUME run={run_id}", 2)
    sweep_mechanical_land_runs(store=store)
    snap = store.get_run(run_id, event_limit=40)
    assert snap is not None
    events = [e["event"] for e in snap["events"]]
    assert "ruling_folded" in events
    assert "resumed" in events


def test_reconcile_skips_re_admit_when_leg_exists() -> None:
    store = MechanicalLandStore()
    run_id, _lane = _seed_run(store)
    posts: list[dict] = []

    def _fetch(**_k: object) -> dict:
        return {
            "transition": {
                "command": "admit",
                "step_id": "implement",
                "attempt": 1,
                "to_step": "implement",
            }
        }

    deps = reactor_deps(
        store,
        _fetch,
        post_admit_fn=lambda body: posts.append(body) or (201, {"dispatch_id": "d2"}),
        get_turns_fn=empty_get_turns,
    )
    run_reactor_loop(deps, run_id=run_id, max_sync_steps=1)
    assert posts == []
