"""Mechanical-land reactor tests (§13 stage 4)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from implement_admission.closeout_models import ImplementCloseout

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.mechanical_land.reactor import (
    ReactorDeps,
    ingest_terminal_closeout,
    maybe_fire_mechanical_land_reactor,
    mechanical_land_stamp,
    repost_blocked_if_needed,
    run_reactor_loop,
)
from services.git_integration_worker.mechanical_land.store import MechanicalLandStore

pytestmark = pytest.mark.offline

_POLICY = {"models": {"implement": {"model": "cursor/composer-2.5", "knobs": {}}}}


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


def _seed_run(store: MechanicalLandStore, *, run_id: str = "ml-react000001") -> str:
    with (
        patch.object(store, "_mint_lane_thread", return_value="9100"),
        patch.object(
            store,
            "_post_admit",
            return_value=(201, {"dispatch_id": "d-implement-1", "execution_id": "e1"}),
        ),
    ):
        result = store.birth_from_plan(_plan(), idempotency_key=run_id)
    assert result.run_id
    return result.run_id


def test_unstamped_dispatch_skips_journal_read() -> None:
    row = {"record_json": json.dumps({"generation_options": {}})}
    assert mechanical_land_stamp(row) is None
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            "status, record_json, source_repo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "d-unstamped",
                "fp-unstamped",
                "1",
                "d-unstamped",
                "cursor/composer-2.5",
                "completed",
                row["record_json"],
                "/tmp",
            ),
        )
        conn.commit()
    with patch.object(MechanicalLandStore, "get_run") as get_run:

        async def _run() -> None:
            await maybe_fire_mechanical_land_reactor(dispatch_id="d-unstamped")

        import asyncio

        asyncio.run(_run())
        get_run.assert_not_called()


def test_stamped_appends_leg_closed_from_structured_closeout(
    tmp_path: Path,
) -> None:
    store = MechanicalLandStore()
    run_id = _seed_run(store)
    dispatch_id = "d-close-1"
    closeout = ImplementCloseout(
        status="complete",
        work_outcome="shipped",
        summary="done",
        source_ref="todo:mechanical-land-pipeline-s4",
        head_sha="a" * 40,
        branch="cursor-sdk/lane-x",
        branch_point="b" * 40,
    )
    sidecar_dir = tmp_path / "tmp" / "reviews" / "closeouts"
    sidecar_dir.mkdir(parents=True)
    sidecar_path = sidecar_dir / f"{dispatch_id}.md"
    sidecar_path.write_text(
        f"## structured_closeout_full\n\n{closeout.model_dump_json()}",
        encoding="utf-8",
    )
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            "status, record_json, source_repo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dispatch_id,
                "fp-close-1",
                "9100",
                dispatch_id,
                "cursor/composer-2.5",
                "completed",
                json.dumps(
                    {
                        "generation_options": {
                            "mechanical_land": {
                                "run_id": run_id,
                                "step_id": "implement",
                                "attempt": 1,
                            }
                        }
                    }
                ),
                str(tmp_path),
            ),
        )
        conn.commit()
    deps = ReactorDeps(
        store=store,
        fetch_plan_fn=lambda **_k: None,
        post_admit_fn=lambda _b: (500, {}),
    )
    assert ingest_terminal_closeout(
        deps,
        run_id=run_id,
        dispatch_id=dispatch_id,
        stamp={"run_id": run_id, "step_id": "implement", "attempt": 1},
    )
    snap = store.get_run(run_id, event_limit=50)
    assert snap is not None
    events = [e["event"] for e in snap["events"]]
    assert events.count("leg_closed") == 1


def test_transition_fetched_via_mechanical_land_completions() -> None:
    store = MechanicalLandStore()
    run_id = _seed_run(store)
    captured: dict = {}

    def _fetch(**kwargs: object) -> dict:
        captured.update(kwargs)
        return {
            "transition": {
                "command": "wait",
                "reason": "awaiting_leg",
            }
        }

    deps = ReactorDeps(
        store=store,
        fetch_plan_fn=_fetch,
        post_admit_fn=lambda _b: (500, {}),
    )
    run_reactor_loop(deps, run_id=run_id, max_sync_steps=1)
    assert captured.get("run_id") == run_id
    assert isinstance(captured.get("journal"), list)


def test_admit_command_posts_once_and_appends_leg_admitted() -> None:
    store = MechanicalLandStore()
    run_id = _seed_run(store)
    posts: list[dict] = []

    def _fetch(**_k: object) -> dict:
        return {
            "transition": {
                "command": "admit",
                "step_id": "fix",
                "attempt": 1,
                "to_step": "fix",
                "reason": "gate_red",
            }
        }

    def _post(body: dict) -> tuple[int, dict]:
        posts.append(body)
        return 201, {"dispatch_id": "d-fix-1", "execution_id": "e-fix-1"}

    deps = ReactorDeps(store=store, fetch_plan_fn=_fetch, post_admit_fn=_post)
    run_reactor_loop(deps, run_id=run_id, max_sync_steps=1)
    assert len(posts) == 1
    snap = store.get_run(run_id, event_limit=50)
    assert snap is not None
    assert "leg_admitted" in [e["event"] for e in snap["events"]]


def test_blocked_emits_event_and_bus_turn() -> None:
    store = MechanicalLandStore()
    run_id = _seed_run(store)
    bus_posts: list[dict] = []

    def _fetch(**_k: object) -> dict:
        return {
            "transition": {
                "command": "block",
                "blocked": True,
                "step_id": "implement",
                "attempt": 1,
                "reason_code": "leg_failed",
                "detail": "failed closeout",
            }
        }

    def _bus(**kwargs: object) -> int:
        bus_posts.append(dict(kwargs))
        return 42

    deps = ReactorDeps(
        store=store,
        fetch_plan_fn=_fetch,
        post_admit_fn=lambda _b: (500, {}),
        bus_post_fn=_bus,
    )
    run_reactor_loop(deps, run_id=run_id, max_sync_steps=1)
    snap = store.get_run(run_id, event_limit=50)
    assert snap is not None
    blocked = [e for e in snap["events"] if e["event"] == "blocked"]
    assert len(blocked) == 1
    assert blocked[0]["payload"]["posted_turn"] == 42
    assert any(p.get("thread_id") == "12286" for p in bus_posts)


@pytest.mark.asyncio
async def test_reactor_exception_does_not_abort_caller() -> None:
    store = MechanicalLandStore()
    _seed_run(store)
    with patch(
        "services.git_integration_worker.mechanical_land.reactor.run_reactor_loop",
        side_effect=RuntimeError("boom"),
    ):
        ledger = CursorDispatchLedger.instance()
        with ledger._connect() as conn:
            conn.execute(
                "INSERT INTO cursor_sdk_dispatches "
                "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
                "status, record_json, source_repo) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "d-boom",
                    "fp-boom",
                    "1",
                    "d-boom",
                    "cursor/composer-2.5",
                    "completed",
                    json.dumps(
                        {
                            "generation_options": {
                                "mechanical_land": {
                                    "run_id": "ml-boom",
                                    "step_id": "implement",
                                }
                            }
                        }
                    ),
                    "/tmp",
                ),
            )
            conn.commit()
        await maybe_fire_mechanical_land_reactor(dispatch_id="d-boom")


def test_transition_failure_sets_admit_deferred() -> None:
    store = MechanicalLandStore()
    run_id = _seed_run(store)
    deps = ReactorDeps(
        store=store,
        fetch_plan_fn=lambda **_k: None,
        post_admit_fn=lambda _b: (500, {}),
    )
    run_reactor_loop(deps, run_id=run_id, max_sync_steps=1)
    meta = store.get_run(run_id)
    assert meta is not None
    with store._connect() as conn:
        row = conn.execute(
            "SELECT status FROM mechanical_land_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert row is not None
    assert str(row["status"]) == "admit_deferred"


def test_bus_unreachable_blocked_still_writes_event_and_sweep_reposts() -> None:
    store = MechanicalLandStore()
    run_id = _seed_run(store)

    def _fetch(**_k: object) -> dict:
        return {
            "transition": {
                "command": "block",
                "blocked": True,
                "step_id": "implement",
                "attempt": 1,
                "reason_code": "leg_failed",
                "detail": "x",
            }
        }

    deps = ReactorDeps(
        store=store,
        fetch_plan_fn=_fetch,
        post_admit_fn=lambda _b: (500, {}),
        bus_post_fn=lambda **_k: (_ for _ in ()).throw(OSError("bus down")),
    )
    run_reactor_loop(deps, run_id=run_id, max_sync_steps=1)
    snap = store.get_run(run_id, event_limit=20)
    assert snap is not None
    blocked = next(e for e in snap["events"] if e["event"] == "blocked")
    assert blocked["payload"]["posted_turn"] is None
    with patch(
        "services.git_integration_worker.mechanical_land.reactor._post_blocked_bus_only",
        return_value=99,
    ):
        repost_blocked_if_needed(store, run_id)
    with store._connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM mechanical_land_journal "
            "WHERE run_id=? AND event='blocked' ORDER BY seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload.get("posted_turn") == 99
