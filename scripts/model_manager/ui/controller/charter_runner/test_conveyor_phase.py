"""Structured conveyor dormancy — §7.2 acceptance tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner import (
    conveyor_phase,
    kernel_tick,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_admit_gate import (
    validate_checkpoint_for_admit,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.conveyor_phase import (
    bootstrap_seed_pickup_matches_tip,
    pickup_append_is_fresh,
    structured_pickup_append_high_water,
)
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    SeedConfirm,
    load_root,
    seed_from_confirm,
    upsert_root,
)
from scripts.model_manager.ui.controller.charter_runner.admission import CapStore


def _dormant_body(*, rows: list[str] | None = None) -> str:
    pickup = rows or ["_None — queue drained._"]
    block = "\n".join(f"- {row}" for row in pickup)
    return f"""\
# CHECKPOINT — conveyor idle

## In-flight / WIP
_None this window._

## Next-pickup
{block}

## Steps
1. [x] prior work done

## Frictions
_None this window._

## Sidecars
_None this window._

— RESUME (any seat, no command): charter conveyor dormant wait.
"""


def _work_body(row: str) -> str:
    return f"""\
# CHECKPOINT — conveyor work

## In-flight / WIP
_None this window._

## Next-pickup
- {row}

## Steps
1. [ ] process follow-on

## Frictions
_None this window._

## Sidecars
_None this window._

— RESUME (any seat, no command): charter conveyor process pickup.
"""


def _turn(n: int, subject: str, body: str) -> dict[str, Any]:
    return {"turn_number": n, "subject": subject, "body": body, "from_agent": "cursor"}


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.default_ledger_path",
        lambda: db,
    )
    for module in (kernel_tick, conveyor_phase):
        monkeypatch.setattr(module, "write_cortex_mirror", lambda _row: "")
    conn = open_ledger_db(db)
    try:
        yield conn
    finally:
        conn.close()


def _seed_conveyor(
    conn,
    *,
    phase: str = "dormant",
    cursor: int = 1,
    pickup_gid: str = "G1",
) -> RootLedgerRow:
    row = seed_from_confirm(
        conn,
        SeedConfirm(
            root_id="6171",
            pickup_gid=pickup_gid,
            pickup_lane="judgment",
            pickup_executor="cursor/grok-4.5",
            attendance="attended",
            scoreboard_uri="cortex://notes/system/threads/6171-charter-scoreboard.md",
        ),
    )
    updated = RootLedgerRow(
        root_id=row.root_id,
        status=row.status,
        pickup_gid=row.pickup_gid,
        pickup_lane=row.pickup_lane,
        pickup_executor=row.pickup_executor,
        attendance=row.attendance,
        scoreboard_uri=row.scoreboard_uri,
        conveyor_phase=phase,  # type: ignore[arg-type]
        pickup_append_cursor=cursor,
    )
    upsert_root(conn, updated)
    return load_root(conn, "6171") or updated


def _env(root_id: str = "6171") -> EnvSnapshot:
    return EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up"},
        attendance_by_root={root_id: "attended"},
        scoreboard_pointer={},
        bus_tip_meta={root_id: {}},
    )


def _tick(conn, tmp_path: Path, turns: list[dict[str, Any]]):
    return asyncio.run(
        kernel_tick.apply_kernel_tick_for_root(
            "6171",
            turns,
            caps=CapStore(intent_dir=tmp_path / "intent"),
            workspace_root=tmp_path,
            env=_env(),
        )
    )


@pytest.mark.offline
def test_d5_pickup_append_is_fresh_predicate() -> None:
    assert pickup_append_is_fresh(append_high_water=2, last_admit_cursor=1) is True
    assert pickup_append_is_fresh(append_high_water=1, last_admit_cursor=1) is False
    assert pickup_append_is_fresh(append_high_water=0, last_admit_cursor=0) is False


@pytest.mark.offline
def test_checkpoint_admit_gate_dormant_update_exempts_ungated(
    ledger,
) -> None:
    body = _dormant_body()
    blocked = validate_checkpoint_for_admit(body)
    assert blocked.ok is False
    assert blocked.reason == "no_gated_pickup"
    exempt = validate_checkpoint_for_admit(body, conveyor_phase="dormant")
    assert exempt.ok is True
    assert exempt.reason == "dormant_update"


@pytest.mark.offline
def test_dormant_survival_three_ticks_no_admit(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_conveyor(ledger, phase="dormant", cursor=1)
    turns = [_turn(5, "CHECKPOINT — idle", _dormant_body())]

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("dormant must not admit")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    monkeypatch.setattr(kernel_tick, "admit_consult_window", refuse_admit)
    outcomes = [_tick(ledger, tmp_path, turns) for _ in range(3)]
    assert all(o.skipped_reason == "dormant" for o in outcomes)
    assert all(not o.admitted for o in outcomes)
    row = load_root(ledger, "6171")
    assert row is not None
    assert row.conveyor_phase == "dormant"


@pytest.mark.offline
def test_wake_on_fresh_append_then_admit(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_conveyor(ledger, phase="dormant", cursor=0)
    row_text = (
        "G2 — `todo:friction-99` follow-on from root 100 "
        "(spawned_by_friction=99) · note · executor=cursor/grok-4.5"
    )
    turns = [_turn(6, "CHECKPOINT — conveyor", _work_body(row_text))]
    fired: list[bool] = []

    async def fake_admit(**_kw: Any) -> bool:
        fired.append(True)
        return True

    monkeypatch.setattr(kernel_tick, "admit_worker_window", fake_admit)
    outcome = _tick(ledger, tmp_path, turns)
    row = load_root(ledger, "6171")
    assert row is not None
    assert row.conveyor_phase == "active"
    assert outcome.admitted is True
    assert fired == [True]
    assert row.pickup_append_cursor == 1


@pytest.mark.offline
def test_wake_test_fails_without_wake_edge(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC(i): must fail if wake edge is missed — fresh append while dormant."""
    _seed_conveyor(ledger, phase="dormant", cursor=0)
    row_text = (
        "G3 — `todo:friction-100` follow-on · executor=cursor/grok-4.5"
    )
    turns = [_turn(7, "CHECKPOINT — conveyor", _work_body(row_text))]

    with patch.object(
        kernel_tick, "wake_conveyor_if_fresh_append", side_effect=lambda _c, row, _p: row
    ):
        outcome = _tick(ledger, tmp_path, turns)
    row = load_root(ledger, "6171")
    assert row is not None
    assert row.conveyor_phase == "dormant"
    assert outcome.skipped_reason == "dormant"
    assert not outcome.admitted


@pytest.mark.offline
def test_host_emits_root_skipped_dormant_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.model_manager.ui.controller.charter_runner.admission import (
        ENROLLMENT_TAG,
    )
    from scripts.model_manager.ui.controller.charter_runner.kernel import (
        host as tick_host,
    )
    from scripts.model_manager.ui.controller.charter_runner.kernel_tick import (
        KernelTickOutcome,
    )
    from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
    from scripts.model_manager.ui.model.service_state import ServiceInfo, ServiceStatus

    events_log: list[tuple[str, dict[str, Any]]] = []

    async def _fake_emit(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        events_log.append((signal, payload))

    monkeypatch.setattr("scripts.model_manager.observation_event._emit", _fake_emit)
    monkeypatch.setattr(
        "scripts.model_manager.observation_event_charter._emit", _fake_emit
    )
    monkeypatch.setattr(
        "scripts.model_manager.observation_event.emit_manage_charter_tick_scanned",
        AsyncMock(),
    )

    async def fake_roots() -> list[dict[str, Any]]:
        return [{"id": "6171", "tags": [ENROLLMENT_TAG]}]

    async def fake_turns(_root_id: str) -> list[dict[str, Any]]:
        return [_turn(8, "CHECKPOINT — idle", _dormant_body())]

    async def fake_kernel(*_a: Any, **_kw: Any) -> KernelTickOutcome:
        return KernelTickOutcome("kernel_dormant", skipped_reason="dormant")

    monkeypatch.setattr(tick_host.bus_client, "list_enrolled_roots", fake_roots)
    monkeypatch.setattr(tick_host.bus_client, "fetch_turns", fake_turns)
    monkeypatch.setattr(tick_host, "harvest_completed_windows", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        tick_host,
        "build_tick_env_snapshot",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.env_snapshot.build_env_snapshot",
        AsyncMock(return_value=_env()),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.apply_kernel_tick_for_root",
        fake_kernel,
    )

    class _Healthy:
        def check_cortex_api(self):
            return ServiceInfo(name="Cortex", status=ServiceStatus.RUNNING)

        def check_agent_bus(self):
            return ServiceInfo(name="AgentBus", status=ServiceStatus.RUNNING)

    loop = tick_host.CharterRunnerTickLoop(
        service_state=_Healthy(),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=tmp_path,
        tick_interval_s=0.01,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )

    async def _exercise() -> None:
        await loop._tick_once()

    asyncio.run(_exercise())
    skipped = [p for s, p in events_log if s == "manage.charter.tick.root_skipped"]
    assert skipped and skipped[0]["reason"] == "dormant"
    assert skipped[0]["root"] == "6171"


@pytest.mark.offline
def test_open_gated_g_rows_structured() -> None:
    parsed = parse_checkpoint(
        _work_body("G1 — follow-on · executor=cursor/grok-4.5")
    )
    assert len(conveyor_phase.open_gated_g_rows(parsed)) == 1
    empty = parse_checkpoint(_dormant_body())
    assert conveyor_phase.open_gated_g_rows(empty) == []


@pytest.mark.offline
def test_ordinary_idle_ungated_active_phase_state_closes_via_no_gated_pickup(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC(vi): non-dormant idle root with ungated tip still hits no_gated_pickup."""
    upsert_root(
        ledger,
        RootLedgerRow(
            root_id="5705",
            status=RootStatus.IDLE,
            pickup_gid="G1",
            pickup_lane="judgment",
            pickup_executor="cursor/grok-4.5",
            attendance="attended",
            scoreboard_uri="cortex://notes/system/threads/5705-charter-scoreboard.md",
            conveyor_phase="active",
            pickup_append_cursor=2,
        ),
    )
    body = """\
# CHECKPOINT — idle ungated

## In-flight / WIP
_None this window._

## Next-pickup
- finish the thing
- also this

## Steps
1. [ ] work

## Frictions
_None this window._

## Sidecars
_None this window._

— RESUME (any seat, no command): test.
"""
    outcome = asyncio.run(
        kernel_tick.apply_kernel_tick_for_root(
            "5705",
            [_turn(2, "CHECKPOINT wave 2", body)],
            caps=CapStore(intent_dir=tmp_path / "intent"),
            workspace_root=tmp_path,
            env=_env("5705"),
        )
    )
    assert outcome.skipped_reason == "no_gated_pickup"
    assert outcome.old_decision_label == "kernel_no_gated_pickup"


@pytest.mark.offline
def test_bootstrap_wake_cursor_independent_at_birth_cursor_zero(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.2(iv): cursor=0 at birth — bootstrap path does not read pickup_append_cursor.

    Fable (0,0) row: D5 ``pickup_append_is_fresh(0,0)`` is false; wake must not
    depend on cursor when ``bootstrap_seed_pickup_matches_tip`` matches the tip G1.
    True (cursor=0, high_water=0) at tick time is unreachable: birth CHECKPOINT
    always precedes first tick with ≥1 structured Next-pickup row (high_water≥1).
    """
    _seed_conveyor(ledger, phase="dormant", cursor=0, pickup_gid="G1")
    row = load_root(ledger, "6171")
    assert row is not None
    assert row.pickup_append_cursor == 0
    row_text = (
        "G1 — `todo:friction-followon-tick-enrollment` birth work "
        "· executor=cursor/composer-2.5"
    )
    parsed = parse_checkpoint(_work_body(row_text))
    assert structured_pickup_append_high_water(parsed) == 1
    assert pickup_append_is_fresh(
        append_high_water=1, last_admit_cursor=row.pickup_append_cursor
    )
    assert bootstrap_seed_pickup_matches_tip(row, parsed) is True
    turns = [_turn(11, "CHECKPOINT — birth cursor=0", _work_body(row_text))]
    fired: list[bool] = []

    async def fake_admit(**_kw: Any) -> bool:
        fired.append(True)
        return True

    monkeypatch.setattr(kernel_tick, "admit_worker_window", fake_admit)
    outcome = _tick(ledger, tmp_path, turns)
    row = load_root(ledger, "6171")
    assert row is not None
    assert row.conveyor_phase == "active"
    assert outcome.admitted is True
    assert fired == [True]


@pytest.mark.offline
def test_conveyor_wake_is_due_false_at_cursor_zero_high_water_zero(
    ledger,
) -> None:
    """§7.2(iv): (cursor=0, high_water=0) — neither D5 nor bootstrap fires without tip."""
    from scripts.model_manager.ui.controller.charter_runner.conveyor_phase import (
        bootstrap_seed_pickup_matches_tip,
        conveyor_wake_is_due,
        structured_pickup_append_high_water,
    )

    row = _seed_conveyor(ledger, phase="dormant", cursor=0, pickup_gid="G1")
    assert row.pickup_append_cursor == 0
    assert structured_pickup_append_high_water(None) == 0
    assert pickup_append_is_fresh(
        append_high_water=0, last_admit_cursor=row.pickup_append_cursor
    ) is False
    assert bootstrap_seed_pickup_matches_tip(row, None) is False
    assert conveyor_wake_is_due(row, None) is False


@pytest.mark.offline
def test_bootstrap_seed_admits_g1_when_cursor_pre_synced(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.2(iv): birth G1 row must wake+admit even when cursor equals high-water."""
    _seed_conveyor(ledger, phase="dormant", cursor=1, pickup_gid="G1")
    row_text = (
        "G1 — `todo:friction-followon-tick-enrollment` birth work "
        "· executor=cursor/composer-2.5"
    )
    turns = [_turn(9, "CHECKPOINT — birth", _work_body(row_text))]
    fired: list[bool] = []

    async def fake_admit(**_kw: Any) -> bool:
        fired.append(True)
        return True

    monkeypatch.setattr(kernel_tick, "admit_worker_window", fake_admit)
    outcome = _tick(ledger, tmp_path, turns)
    row = load_root(ledger, "6171")
    assert row is not None
    assert row.conveyor_phase == "active"
    assert outcome.admitted is True
    assert fired == [True]


@pytest.mark.offline
def test_active_phase_zero_delta_readmits_open_row(
    ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§7.2 / forbid §2: active-phase window loop re-admits without fresh delta."""
    row_text = (
        "G1 — `todo:friction-42` follow-on · executor=cursor/composer-2.5"
    )
    upsert_root(
        ledger,
        RootLedgerRow(
            root_id="6171",
            status=RootStatus.IDLE,
            pickup_gid="G1",
            pickup_lane="mechanical",
            pickup_executor="cursor/composer-2.5",
            attendance="attended",
            scoreboard_uri="cortex://notes/system/threads/6171-charter-scoreboard.md",
            conveyor_phase="active",
            pickup_append_cursor=1,
            last_window_id="6171-w3",
        ),
    )
    turns = [_turn(10, "CHECKPOINT — continue", _work_body(row_text))]
    fired: list[bool] = []

    async def fake_admit(**_kw: Any) -> bool:
        fired.append(True)
        return True

    monkeypatch.setattr(kernel_tick, "admit_worker_window", fake_admit)
    outcome = _tick(ledger, tmp_path, turns)
    assert outcome.skipped_reason != "dormant"
    assert outcome.skipped_reason != "no_gated_pickup"
    assert outcome.admitted is True
    assert fired == [True]


@pytest.mark.offline
def test_maybe_set_dormant_on_window_close(ledger) -> None:
    _seed_conveyor(ledger, phase="active", cursor=1)
    conveyor_phase.maybe_set_dormant_on_window_close(
        ledger, "6171", _dormant_body()
    )
    row = load_root(ledger, "6171")
    assert row is not None
    assert row.conveyor_phase == "dormant"
    assert row.pickup_append_cursor == 1
