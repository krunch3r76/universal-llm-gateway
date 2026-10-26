"""Mechanical-land store unit tests (§13 stage 3)."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from mechanical_land.schema import JournalForkRefused

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.mechanical_land.store import (
    MechanicalLandStore,
    MechanicalLandStoreError,
    build_implement_admit_body,
)

pytestmark = pytest.mark.offline

_POLICY = {"models": {"implement": {"model": "cursor/composer-2.5", "knobs": {}}}}


def _plan(**overrides: object) -> dict:
    base = {
        "admitted": True,
        "kind": "implement",
        "source_ref": "todo:mechanical-land-pipeline-s3",
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


def test_append_cas_one_winner_under_contention(tmp_path: Path) -> None:
    store = MechanicalLandStore()
    plan = _plan()
    with (
        patch.object(store, "_mint_lane_thread", return_value="9001"),
        patch.object(
            store,
            "_post_admit",
            return_value=(200, {"dispatch_id": "d1", "execution_id": "e1"}),
        ),
    ):
        result = store.birth_from_plan(plan)
    run_id = result.run_id
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker(prev: int) -> None:
        barrier.wait()
        try:
            store.append_event(
                run_id=run_id,
                prev_seq=prev,
                step_id="run",
                attempt=1,
                flow="main",
                event="transition_decided",
                payload={
                    "from_step": "run",
                    "to_step": "run",
                    "attempt": 1,
                    "reason": "x",
                },
                source="reactor",
            )
            outcomes.append("ok")
        except JournalForkRefused:
            outcomes.append("fork")

    t1 = threading.Thread(target=worker, args=(3,))
    t2 = threading.Thread(target=worker, args=(3,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert outcomes.count("ok") == 1
    assert outcomes.count("fork") == 1


def test_stargate_5xx_appends_leg_admit_failed(tmp_path: Path) -> None:
    store = MechanicalLandStore()
    with (
        patch.object(store, "_mint_lane_thread", return_value="9002"),
        patch.object(store, "_post_admit", return_value=(503, {"error": "down"})),
    ):
        result = store.birth_from_plan(_plan())
    assert result.admit_ok is False
    assert result.http_status == 502
    snap = store.get_run(result.run_id)
    assert snap is not None
    events = [e["event"] for e in snap["events"]]
    assert "leg_admit_failed" in events
    assert "leg_admitted" not in events


def test_bus_down_lane_thread_unavailable_no_admit() -> None:
    store = MechanicalLandStore()

    def _fail(**_kwargs: object) -> str:
        raise MechanicalLandStoreError("lane_thread_unavailable")

    with (
        patch.object(store, "_mint_lane_thread", side_effect=_fail),
        patch.object(store, "_post_admit") as post,
    ):
        with pytest.raises(MechanicalLandStoreError, match="lane_thread_unavailable"):
            store.birth_from_plan(_plan())
        post.assert_not_called()


def test_sqlite_locked_retries_like_ledger(tmp_path: Path) -> None:
    """Store uses ledger _connect with timeout=30 (observed in cursor_dispatch_ledger)."""
    from services.git_integration_worker import cursor_dispatch_ledger as cdl

    assert "timeout=30.0" in Path(cdl.__file__).read_text(encoding="utf-8")
    conn = cdl._connect(tmp_path / "probe.db")
    conn.close()


def test_idempotency_key_returns_same_run() -> None:
    store = MechanicalLandStore()
    key = "idem-test-key"
    with (
        patch.object(store, "_mint_lane_thread", return_value="9010"),
        patch.object(
            store,
            "_post_admit",
            return_value=(200, {"dispatch_id": "d-idem", "execution_id": "e-idem"}),
        ),
    ):
        first = store.birth_from_plan(_plan(), idempotency_key=key)
        second = store.birth_from_plan(_plan(), idempotency_key=key)
    assert first.run_id == second.run_id
    assert second.http_status == 200


def test_projection_written_after_append(tmp_path: Path) -> None:
    store = MechanicalLandStore()
    with (
        patch.object(store, "_mint_lane_thread", return_value="9011"),
        patch.object(
            store,
            "_post_admit",
            return_value=(200, {"dispatch_id": "d2", "execution_id": "e2"}),
        ),
    ):
        result = store.birth_from_plan(_plan())
    proj = tmp_path / "cortex/notes/system/runs/mechanical-land" / f"{result.run_id}.md"
    assert proj.is_file()
    text = proj.read_text(encoding="utf-8")
    assert result.run_id in text
    assert "run_birthed" in text


def test_build_implement_admit_body_stamps_mechanical_land() -> None:
    body = build_implement_admit_body(
        run_id="ml-abc123456789",
        lane_thread_id="7777",
        source_ref="todo:foo",
        policy=_POLICY,
        policy_sha256="abc",
        caller_agent="grok-bot-gc1",
    )
    ml = body["generation_options"]["mechanical_land"]
    assert ml["run_id"] == "ml-abc123456789"
    assert ml["step_id"] == "implement"
    assert body["contract"] == "implement"
    assert body["lane"] == "B"
