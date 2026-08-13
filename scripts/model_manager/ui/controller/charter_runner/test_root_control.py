"""Offline tests for per-root charter-runner block/unblock control plane."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner import (
    bus_client,
    consult_lane,
    kernel_tick,
    root_control,
    window_sequence,
)
from scripts.model_manager.ui.controller.charter_runner.admission import (
    ENROLLMENT_TAG,
    CapStore,
    CapsView,
    EnvFacts,
    decide,
)
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.kernel import hold
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    SeedConfirm,
    Transition,
    load_root,
    seed_from_confirm,
    upsert_root,
)
from scripts.model_manager.ui.controller.charter_runner.worker_failed_release import (
    maybe_release_failed_window_wip,
)
from scripts.model_manager.ui.controller.service_ctl.core import ServiceController


def _idle_row(
    root_id: str = "r1", *, executor: str | None = "cursor/grok-4.6"
) -> RootLedgerRow:
    return RootLedgerRow(
        root_id=root_id,
        status=RootStatus.IDLE,
        pickup_gid="G1",
        pickup_lane="judgment",
        pickup_executor=executor,
        attendance="autonomous",
        scoreboard_uri=f"cortex://notes/system/threads/{root_id}-charter-scoreboard.md",
    )


def _env(*, attendance: str = "autonomous") -> EnvFacts:
    return EnvFacts(substrate_up=True, has_wip=False, attendance=attendance)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "charter-runner"
    d.mkdir()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(d))
    return d


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.default_ledger_path",
        lambda: db,
    )
    for module in (root_control, kernel_tick, consult_lane, window_sequence):
        monkeypatch.setattr(module, "write_cortex_mirror", lambda _row: "")
    conn = open_ledger_db(db)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def events_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    monkeypatch.setattr(
        "scripts.model_manager.observation_event_charter._emit", _fake_emit
    )
    return log


@pytest.fixture
def bus_recorder(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {
        "post_root_turn": [],
        "unenroll_root": [],
        "enroll_root": [],
        "fetch_thread": [],
        "live_dispatches": [],
    }
    order: list[str] = []

    async def _post(
        root_id: str, *, subject: str, body: str, to: str = "charter-runner"
    ):
        calls["post_root_turn"].append(
            {"root_id": root_id, "subject": subject, "body": body, "to": to}
        )
        order.append("post_root_turn")
        return {"turn_number": len(calls["post_root_turn"])}

    async def _unenroll(root_id: str):
        calls["unenroll_root"].append(root_id)
        order.append("unenroll_root")
        return {"tags": [], "unenrolled": True}

    async def _enroll(root_id: str):
        calls["enroll_root"].append(root_id)
        order.append("enroll_root")
        return {"tags": [ENROLLMENT_TAG], "enrolled": True}

    async def _fetch(root_id: str):
        calls["fetch_thread"].append(root_id)
        return {"tags": [ENROLLMENT_TAG]}

    async def _live():
        return list(calls["live_dispatches"])

    monkeypatch.setattr(bus_client, "post_root_turn", _post)
    monkeypatch.setattr(bus_client, "unenroll_root", _unenroll)
    monkeypatch.setattr(bus_client, "enroll_root", _enroll)
    monkeypatch.setattr(bus_client, "fetch_thread", _fetch)
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_control.tick_hold.list_live_charter_dispatches",
        _live,
    )
    calls["order"] = order
    return calls


def _open_caps() -> CapsView:
    return CapsView(
        allowed=True,
        skip_reason=None,
        stopped_reason=None,
        revise_ok=True,
        revise_reason=None,
    )


def _seed(conn, root_id: str = "r1") -> RootLedgerRow:
    return seed_from_confirm(
        conn,
        SeedConfirm(
            root_id=root_id,
            pickup_gid="G1",
            pickup_lane="judgment",
            attendance="autonomous",
            scoreboard_uri=f"cortex://notes/system/threads/{root_id}-charter-scoreboard.md",
        ),
    )


# AC1 — block stops admission
@pytest.mark.offline
@pytest.mark.asyncio
async def test_block_stops_admission(ledger, bus_recorder, events_log) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    upsert_root(ledger, replace(row, pickup_executor="cursor/grok-4.6"))
    await root_control.block_root("r1", reason="6489 refire")
    row = load_root(ledger, "r1")
    assert row is not None
    assert row.status == RootStatus.BLOCKED
    assert row.last_error == "operator_hold:6489 refire"
    idle = replace(row, status=RootStatus.IDLE, last_error=None)
    assert decide(idle, _env(), _open_caps()) == Transition.ADMIT_WORKER
    assert decide(row, _env(), _open_caps()) == Transition.NOOP


# AC2 — unblock re-admits
@pytest.mark.offline
@pytest.mark.asyncio
async def test_unblock_re_admits(ledger, bus_recorder, events_log) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    upsert_root(ledger, replace(row, pickup_executor="cursor/grok-4.6"))
    await root_control.block_root("r1", reason="hold")
    await root_control.unblock_root("r1")
    row = load_root(ledger, "r1")
    assert row is not None
    assert row.status == RootStatus.IDLE
    assert row.last_error is None
    assert decide(row, _env(), _open_caps()) == Transition.ADMIT_WORKER


# AC3 — ordering ledger → tip → unenroll → event
@pytest.mark.offline
@pytest.mark.asyncio
async def test_block_ordering(ledger, bus_recorder, events_log, monkeypatch) -> None:
    _seed(ledger, "r1")
    ledger_times: list[float] = []

    original_apply = root_control._apply_block

    def _wrap_apply(conn, row, **kwargs):
        ledger_times.append(time.time())
        return original_apply(conn, row, **kwargs)

    monkeypatch.setattr(root_control, "_apply_block", _wrap_apply)
    await root_control.block_root("r1", reason="order")
    assert ledger_times
    assert bus_recorder["post_root_turn"]
    assert bus_recorder["unenroll_root"]
    assert events_log
    assert events_log[-1][0] == "manage.charter.root.blocked"
    order = bus_recorder["order"]
    assert order.index("post_root_turn") < order.index("unenroll_root")


# AC4 — tip class BLOCKED vs NOTE
@pytest.mark.offline
@pytest.mark.asyncio
async def test_tip_class_blocked_when_clean(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    result = await root_control.block_root("r1", reason="clean")
    assert result["tip_class"] == "BLOCKED"
    assert bus_recorder["post_root_turn"][0]["subject"].startswith(
        "BLOCKED — operator hold:"
    )
    assert result["tip_turn"] is not None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_tip_class_note_when_wip(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    upsert_root(
        ledger,
        replace(row, wip_window_id="charter-r1-w3"),
    )
    result = await root_control.block_root("r1", reason="live wip")
    assert result["tip_class"] == "NOTE"
    assert bus_recorder["post_root_turn"][0]["subject"].startswith(
        "NOTE — operator hold:"
    )
    assert result["tip_turn"] is not None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_tip_class_note_when_live_dispatch(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    bus_recorder["live_dispatches"].append(
        {"subject": "cursor-sdk generate — r1", "thread_id": "t1"}
    )
    result = await root_control.block_root("r1", reason="live dispatch")
    assert result["tip_class"] == "NOTE"


# AC5 — WIP disposition
@pytest.mark.offline
@pytest.mark.asyncio
async def test_wip_preserved_by_default(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    upsert_root(ledger, replace(row, wip_window_id="charter-r1-w2"))
    await root_control.block_root("r1", reason="keep wip")
    stored = load_root(ledger, "r1")
    assert stored is not None
    assert stored.wip_window_id == "charter-r1-w2"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_wip_cleared_when_flag(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    upsert_root(ledger, replace(row, wip_window_id="charter-r1-w2"))
    await root_control.block_root("r1", reason="clear", clear_wip=True)
    stored = load_root(ledger, "r1")
    assert stored is not None
    assert stored.wip_window_id is None


# AC6 — consult sticky cleared, queue rows kept
@pytest.mark.offline
@pytest.mark.asyncio
async def test_consult_sticky_cleared_queue_kept(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    upsert_root(
        ledger,
        replace(
            row,
            consult_role="judgment_gap",
            consult_next_retry=123.0,
            consult_poll_from="poll-1",
            consult_attempts=2,
        ),
    )
    ledger.execute(
        """
        INSERT INTO consult_queue
          (root_id, gid, consult_role, corpus_sha, attempts, next_retry, status,
           created_at, updated_at)
        VALUES ('r1', 'G1', 'judgment_gap', NULL, 0, NULL, 'queued', 1, 1)
        """
    )
    ledger.commit()
    await root_control.block_root("r1", reason="sticky")
    stored = load_root(ledger, "r1")
    assert stored is not None
    assert stored.consult_role is None
    assert stored.consult_next_retry is None
    assert stored.consult_poll_from is None
    assert stored.consult_attempts == 0
    q = ledger.execute("SELECT status FROM consult_queue WHERE root_id='r1'").fetchone()
    assert q is not None
    assert q["status"] == "queued"


# AC7 — harvest preserve BLOCKED
@pytest.mark.offline
def test_harvest_preserves_blocked(ledger) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    upsert_root(
        ledger,
        replace(
            row,
            status=RootStatus.BLOCKED,
            wip_window_id="charter-r1-w5",
            last_error="operator_hold:test",
        ),
    )
    assert window_sequence.release_window_on_harvest("r1", 5) is True
    stored = load_root(ledger, "r1")
    assert stored is not None
    assert stored.status == RootStatus.BLOCKED
    assert stored.wip_window_id is None


# AC8 — worker-failed preserve BLOCKED
@pytest.mark.offline
@pytest.mark.asyncio
async def test_worker_failed_preserves_blocked(ledger, monkeypatch) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    upsert_root(
        ledger,
        replace(
            row,
            status=RootStatus.BLOCKED,
            wip_window_id="charter-r1-w1",
            last_error="operator_hold:test",
        ),
    )

    async def _fail(_thread: str) -> str:
        return "CURSOR_SDK_SLOT_ACQUIRE_TIMEOUT"

    monkeypatch.setattr(bus_client, "worker_failure_reason", _fail)
    turns = [
        {
            "turn_number": 1,
            "subject": "WIP charter-runner window 1",
            "body": '{"charter_runner":true,"window":1,"worker_thread":"w1"}',
        },
    ]
    live, reason = await maybe_release_failed_window_wip(
        ledger, load_root(ledger, "r1"), turns
    )
    assert reason is None
    assert live.status == RootStatus.BLOCKED
    assert live.consult_next_retry is None


# AC9 — mid-pass clobber guards
@pytest.mark.offline
def test_kernel_tick_preserves_blocked_mid_pass(ledger) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    upsert_root(
        ledger,
        replace(row, status=RootStatus.BLOCKED, last_error="operator_hold:test"),
    )
    updated = kernel_tick._ledger_row_from_state(
        ledger,
        "r1",
        status=RootStatus.ADMITTED,
        transition=Transition.ADMIT_WORKER,
        wip="charter-r1-w4",
        last_window="charter-r1-w4",
    )
    assert updated.status == RootStatus.BLOCKED
    assert updated.wip_window_id == "charter-r1-w4"
    assert updated.last_window_id == "charter-r1-w4"
    assert updated.last_transition == Transition.ADMIT_WORKER.value


@pytest.mark.offline
def test_consult_lane_preserves_blocked_mid_pass(ledger) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    upsert_root(
        ledger,
        replace(row, status=RootStatus.BLOCKED, last_error="operator_hold:test"),
    )
    updated = consult_lane.sync_ledger_consult_queued(
        ledger, row=row, consult_role="judgment_gap"
    )
    assert updated.status == RootStatus.BLOCKED
    assert updated.last_transition == Transition.QUEUE_CONSULT.value


# AC10 — enrollment
@pytest.mark.offline
@pytest.mark.asyncio
async def test_unenroll_on_block(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    result = await root_control.block_root("r1", reason="strip", unenroll=True)
    assert bus_recorder["unenroll_root"] == ["r1"]
    assert result["unenrolled"] is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_no_unenroll_when_disabled(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    await root_control.block_root("r1", reason="keep", unenroll=False)
    assert bus_recorder["unenroll_root"] == []


@pytest.mark.offline
@pytest.mark.asyncio
async def test_reenroll_only_when_requested(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    await root_control.block_root("r1", reason="hold")
    await root_control.unblock_root("r1", reenroll=False)
    assert bus_recorder["enroll_root"] == []
    await root_control.block_root("r1", reason="hold again")
    await root_control.unblock_root("r1", reenroll=True)
    assert bus_recorder["enroll_root"] == ["r1"]


# AC11 — prose is not a control surface
@pytest.mark.offline
@pytest.mark.asyncio
async def test_prose_does_not_block(ledger, tmp_path: Path, monkeypatch) -> None:
    _seed(ledger, "r1")
    env = EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up"},
        attendance_by_root={"r1": "autonomous"},
        scoreboard_pointer={},
        bus_tip_meta={"r1": {}},
    )
    caps = CapStore(intent_dir=tmp_path / "intent")

    async def _noop(*_a, **_k):
        return None

    before = load_root(ledger, "r1")
    assert before is not None
    for subject in ("NOTE — please stop", "WAKE"):
        turns = [{"turn_number": 1, "subject": subject, "body": "operator prose"}]
        await kernel_tick.apply_kernel_tick_for_root(
            "r1",
            turns,
            caps=caps,
            workspace_root=tmp_path,
            env=env,
            on_admit=_noop,
        )
    row = load_root(ledger, "r1")
    assert row is not None
    assert row.status != RootStatus.BLOCKED
    await root_control.block_root("r1", reason="real hold", unenroll=False)
    row = load_root(ledger, "r1")
    assert row is not None
    assert row.status == RootStatus.BLOCKED


# AC12 — read-only status
@pytest.mark.offline
@pytest.mark.asyncio
async def test_root_status_read_only(ledger, bus_recorder, events_log) -> None:
    _seed(ledger, "r1")
    row = load_root(ledger, "r1")
    assert row is not None
    before = row.updated_at
    snapshot = await root_control.root_status("r1")
    assert snapshot["found"] is True
    assert snapshot["status"] == RootStatus.IDLE.value
    assert snapshot["enrolled"] is True
    assert snapshot["updated_at"] == before
    assert not events_log
    missing = await root_control.root_status("missing-root")
    assert missing == {"found": False}


# AC13 — idempotency
@pytest.mark.offline
@pytest.mark.asyncio
async def test_block_idempotent(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    await root_control.block_root("r1", reason="once")
    second = await root_control.block_root("r1", reason="again")
    assert second["already"] == "blocked"
    assert second["tip_turn"] is None
    assert len(bus_recorder["post_root_turn"]) == 1
    assert len(bus_recorder["unenroll_root"]) == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_unblock_idempotent(ledger, bus_recorder) -> None:
    _seed(ledger, "r1")
    first = await root_control.unblock_root("r1")
    assert first["already"] == "unblocked"
    await root_control.block_root("r1", reason="hold")
    await root_control.unblock_root("r1")
    second = await root_control.unblock_root("r1")
    assert second["already"] == "unblocked"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_unknown_root_raises(ledger, bus_recorder) -> None:
    with pytest.raises(ValueError, match="unknown root"):
        await root_control.block_root("nope", reason="x")
    with pytest.raises(ValueError, match="unknown root"):
        await root_control.unblock_root("nope")


# AC14 — op surface
@pytest.mark.offline
def test_manage_valid_actions_include_root_ops() -> None:
    manage_path = (
        Path(__file__).resolve().parents[5]
        / "services"
        / "mcp-server"
        / "tools"
        / "manage.py"
    )
    text = manage_path.read_text(encoding="utf-8")
    for name in (
        "charter_block_root",
        "charter_unblock_root",
        "charter_root_status",
    ):
        assert name in text


@pytest.mark.offline
@pytest.mark.asyncio
async def test_api_dispatch_requires_root_id() -> None:
    from scripts.model_manager.ui import api_dispatch

    ctl = MagicMock(spec=ServiceController)
    for method in (
        "charter_block_root",
        "charter_unblock_root",
        "charter_root_status",
    ):
        with pytest.raises(ValueError, match="requires 'root_id'"):
            await api_dispatch.execute(ctl, method, "", {})


# AC15 — orthogonality to global pause
@pytest.mark.offline
@pytest.mark.asyncio
async def test_block_orthogonal_to_global_pause(
    data_dir: Path, ledger, bus_recorder, monkeypatch
) -> None:
    _seed(ledger, "r1")
    assert hold.read_hold(data_dir=data_dir) is None
    ctl = ServiceController(Path("/tmp"))

    async def _no_live():
        return hold.LiveCharterDispatchProbe(probe_status="ok", dispatches=[])

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.hold.list_live_charter_dispatches",
        _no_live,
    )
    status_before = await ctl.charter_hold_status()
    await root_control.block_root("r1", reason="per-root")
    assert hold.read_hold(data_dir=data_dir) is None
    status_after = await ctl.charter_hold_status()
    assert status_after["held"] == status_before["held"]
    assert status_after["pause_drain_clear"] == status_before["pause_drain_clear"]


# AC16 — events
@pytest.mark.offline
@pytest.mark.asyncio
async def test_block_event_payload(ledger, bus_recorder, events_log) -> None:
    _seed(ledger, "r1")
    await root_control.block_root("r1", reason="evt", set_by="test")
    signal, payload = events_log[-1]
    assert signal == "manage.charter.root.blocked"
    assert payload["root"] == "r1"
    assert payload["reason"] == "evt"
    assert payload["set_by"] == "test"
    assert payload["prior_status"] == RootStatus.IDLE.value
    assert "unenrolled" in payload
    assert "tip_class" in payload
    assert "wip_window_id" in payload


@pytest.mark.offline
@pytest.mark.asyncio
async def test_unblock_event_payload(ledger, bus_recorder, events_log) -> None:
    _seed(ledger, "r1")
    await root_control.block_root("r1", reason="evt")
    await root_control.unblock_root("r1", set_by="test", reenroll=True)
    signal, payload = events_log[-1]
    assert signal == "manage.charter.root.unblocked"
    assert payload["root"] == "r1"
    assert payload["set_by"] == "test"
    assert payload["prior_status"] == RootStatus.BLOCKED.value
    assert payload["reenrolled"] is True
