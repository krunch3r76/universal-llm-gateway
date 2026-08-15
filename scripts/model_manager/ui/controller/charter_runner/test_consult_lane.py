"""Phase 2 consult lane — queue, cdp harvest, enrollment filter (P2-AC1…P2C-AC5)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.charter_runner_store.migrations.migration_001_root_ledger import migrate
from scripts.model_manager.ui.controller.charter_runner.admission import (
    CapsView,
    EnvFacts,
    decide,
)
from scripts.model_manager.ui.controller.charter_runner.consult_lane import (
    parse_cdp_consult_harvest,
    provenance_from_cdp_harvest,
    write_consult_provenance,
)
from scripts.model_manager.ui.controller.charter_runner.enrollment_filter import (
    MIGRATED_ROOTS,
    is_kernel_migrated,
    old_tick_admit_count,
    record_old_tick_admit_blocked,
    refresh_migrated_roots_cache,
)
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import (
    EnvSnapshot,
    build_env_snapshot,
)
from scripts.model_manager.ui.controller.charter_runner.kernel_tick import (
    KernelTickOutcome,
    apply_kernel_tick_for_root,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    SeedConfirm,
    Transition,
    seed_from_confirm,
)
from scripts.model_manager.ui.controller.charter_runner.test_env_predicates import (
    _giw_intent,
    _snapshot,
)


def _open_test_ledger(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "root-ledger.sqlite"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    migrate(conn)
    conn.commit()
    return conn


@pytest.fixture
def ledger_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data_dir = tmp_path / "charter-runner"
    data_dir.mkdir()
    db = data_dir / "root-ledger.sqlite"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    migrate(conn)
    seed_from_confirm(
        conn,
        SeedConfirm(
            root_id="5975",
            pickup_gid="G7",
            pickup_lane="judgment",
            attendance="autonomous",
            scoreboard_uri="cortex://notes/system/threads/5975-charter-scoreboard.md",
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data_dir))
    monkeypatch.setenv(
        "HOME",
        str(tmp_path / "home"),
    )
    (tmp_path / "home" / ".local" / "share").mkdir(parents=True)
    yield data_dir


@pytest.mark.offline
def test_migrated_roots_match_phase1_seeds() -> None:
    assert MIGRATED_ROOTS == frozenset({"5975", "5993", "5994", "6091", "6171"})
    assert is_kernel_migrated("5975")
    assert is_kernel_migrated("6091")
    assert is_kernel_migrated("6171")
    assert not is_kernel_migrated("5705")


@pytest.mark.offline
def test_refresh_migrated_roots_cache_reads_ledger(
    ledger_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refresh_migrated_roots_cache()
    assert is_kernel_migrated("5975")
    assert not is_kernel_migrated("6091")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_record_old_tick_admit_blocked_emits_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.model_manager.ui.controller.charter_runner.enrollment_filter as ef

    ef._migrated_cache = ef.MIGRATED_ROOTS  # prior ledger-only refresh may shrink cache
    emitted: list[dict] = []

    async def capture(**payload):
        emitted.append(payload)

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.enrollment_filter.emit_enrollment_filtered",
        capture,
    )
    await record_old_tick_admit_blocked("5993")
    assert old_tick_admit_count("5993") == 1
    assert emitted == [{"root": "5993", "reason": "old_tick_admit_blocked"}]
    await record_old_tick_admit_blocked("5705")
    assert old_tick_admit_count("5705") == 0
    ef._old_tick_violations["5993"] = 0


_GATED_TIP = """\
# CHECKPOINT — agent-bus:5975

## Next-pickup
- G7 — judgment consult · executor=cursor/grok-4.6

## Steps
1. [ ] Advance G7

## Frictions
_None this window._

— RESUME (any seat, no command): charter root.
"""

_IDLE_TIP = """\
# CHECKPOINT — agent-bus:5975

## Next-pickup
_None this window._

## Steps
1. [x] Arc complete

## Frictions
_None this window._

— RESUME (any seat, no command): charter root.
"""


@pytest.mark.offline
@pytest.mark.asyncio
async def test_queue_consult_idempotent_when_already_queued(
    ledger_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore

    queued: list[str] = []

    async def capture_queued(**payload):
        queued.append(payload["root"])

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.emit_consult_queued",
        capture_queued,
    )
    env = EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up", "project_ask": "up"},
        attendance_by_root={"5975": "autonomous"},
        scoreboard_pointer={"5975": "cortex://notes/system/threads/5975-charter-scoreboard.md"},
        bus_tip_meta={"5975": {"has_checkpoint": True, "turn_id": ""}},
    )
    turns = [{"turn_number": 1, "subject": "CHECKPOINT", "body": _GATED_TIP}]
    caps = CapStore(intent_dir=tmp_path / "intent")
    first = await apply_kernel_tick_for_root(
        "5975",
        turns,
        caps=caps,
        workspace_root=tmp_path / "ws",
        env=env,
    )
    assert first.old_decision_label == "kernel_queue_consult"
    assert queued == ["5975"]
    second = await apply_kernel_tick_for_root(
        "5975",
        turns,
        caps=caps,
        workspace_root=tmp_path / "ws",
        env=env,
    )
    assert queued == ["5975"]
    assert second.old_decision_label != "kernel_queue_consult"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_idle_tip_no_gated_pickup_skips_queue_consult(
    ledger_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """a:26596 — spent tip must not re-emit consult.queued every tick."""
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore

    queued: list[str] = []

    async def capture_queued(**payload):
        queued.append(payload["root"])

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.emit_consult_queued",
        capture_queued,
    )
    env = EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up", "project_ask": "up"},
        attendance_by_root={"5975": "autonomous"},
        scoreboard_pointer={"5975": "cortex://notes/system/threads/5975-charter-scoreboard.md"},
        bus_tip_meta={"5975": {"has_checkpoint": True, "turn_id": ""}},
    )
    turns = [{"turn_number": 1, "subject": "CHECKPOINT", "body": _IDLE_TIP}]
    outcome = await apply_kernel_tick_for_root(
        "5975",
        turns,
        caps=CapStore(intent_dir=tmp_path / "intent"),
        workspace_root=tmp_path / "ws",
        env=env,
    )
    assert outcome.skipped_reason == "no_gated_pickup"
    assert queued == []


@pytest.mark.offline
@pytest.mark.asyncio
async def test_queued_consult_defers_on_idle_tip_when_substrate_down(
    ledger_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """P4-AC1 / G4a — already-queued consult may DEFER without a gated tip."""
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
    from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
        load_root,
        open_default_ledger,
        upsert_root,
    )

    deferred: list[str] = []

    async def capture_deferred(**payload):
        deferred.append(payload["root"])

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.emit_consult_deferred",
        capture_deferred,
    )
    conn = open_default_ledger()
    try:
        row = load_root(conn, "5975")
        assert row is not None
        upsert_root(
            conn,
            RootLedgerRow(
                root_id=row.root_id,
                status=RootStatus.CONSULT_QUEUED,
                pickup_gid=row.pickup_gid or "G7",
                pickup_lane="judgment",
                pickup_executor=None,
                attendance="autonomous",
                scoreboard_uri=row.scoreboard_uri,
                consult_role="judgment_gap",
                consult_attempts=0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    env = EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "down", "project_ask": "down"},
        attendance_by_root={"5975": "autonomous"},
        scoreboard_pointer={"5975": "cortex://notes/system/threads/5975-charter-scoreboard.md"},
        bus_tip_meta={"5975": {"has_checkpoint": True, "turn_id": ""}},
    )
    outcome = await apply_kernel_tick_for_root(
        "5975",
        [{"turn_number": 1, "subject": "CHECKPOINT", "body": _IDLE_TIP}],
        caps=CapStore(intent_dir=tmp_path / "intent"),
        workspace_root=tmp_path / "ws",
        env=env,
    )
    assert outcome.old_decision_label == "kernel_defer_consult"
    assert deferred == ["5975"]
    conn = open_default_ledger()
    try:
        live = load_root(conn, "5975")
        assert live is not None
        assert live.status == RootStatus.CONSULT_DEFERRED
        assert live.consult_attempts == 1
        assert live.consult_next_retry is not None
    finally:
        conn.close()


@pytest.mark.offline
def test_old_tick_admit_counts_start_zero() -> None:
    for root in MIGRATED_ROOTS:
        assert old_tick_admit_count(root) == 0


@pytest.mark.offline
def test_idle_judgment_autonomous_queues_consult() -> None:
    row = RootLedgerRow(
        root_id="5975",
        status=RootStatus.IDLE,
        pickup_gid="G7",
        pickup_lane="judgment",
        pickup_executor=None,
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/5975-charter-scoreboard.md",
    )
    transition = decide(
        row,
        EnvFacts(substrate_up=True, has_wip=False, attendance="autonomous"),
        CapsView(True, None, None, True, None),
    )
    assert transition == Transition.QUEUE_CONSULT


@pytest.mark.offline
def test_consult_queued_admits_when_substrate_up() -> None:
    row = RootLedgerRow(
        root_id="5975",
        status=RootStatus.CONSULT_QUEUED,
        pickup_gid="G7",
        pickup_lane="judgment",
        pickup_executor=None,
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/5975-charter-scoreboard.md",
    )
    transition = decide(
        row,
        EnvFacts(substrate_up=True, has_wip=False, attendance="autonomous"),
        CapsView(True, None, None, True, None),
    )
    assert transition == Transition.ADMIT_CONSULT


@pytest.mark.offline
def test_propagation_residue_defers_restart_shaped_with_holder() -> None:
    row = RootLedgerRow(
        root_id="5975",
        status=RootStatus.IDLE,
        pickup_gid="G2",
        pickup_lane="mechanical",
        pickup_executor=None,
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/5975-charter-scoreboard.md",
    )
    transition = decide(
        row,
        EnvFacts(
            substrate_up=True,
            has_wip=False,
            attendance="autonomous",
            propagation_residue={
                "kind": "git_integration_worker",
                "detail": "sync_restart",
            },
            giw_holder_lease={"held": True, "holder": "6091", "residue": None},
            restart_shaped=True,
        ),
        CapsView(True, None, None, True, None),
    )
    assert transition == Transition.DEFER_CONSULT


@pytest.mark.offline
@pytest.mark.asyncio
async def test_env_snapshot_carries_giw_sync_restart_residue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(_root_id: str) -> str:
        return "attended"

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.env_snapshot.resolve_attendance",
        fake_resolve,
    )
    snap = await build_env_snapshot(
        root_ids=["5975"],
        env_half=_snapshot(intent=_giw_intent()),
    )
    assert snap.propagation_residue["kind"] == "git_integration_worker"
    assert snap.propagation_residue["detail"] == "sync_restart"


@pytest.mark.offline
def test_parse_cdp_harvest_primary_path() -> None:
    worker_turns = [
        {
            "turn_number": 2,
            "from": "cdp",
            "body": "Merits verdict: ADMIT\nScope is bounded.",
        },
        {
            "turn_number": 3,
            "body": json.dumps(
                {
                    "status": "complete",
                    "cdp_model": "cdp/opus-5",
                    "consult_thread": "agent-bus:9001",
                }
            ),
        },
    ]
    result = parse_cdp_consult_harvest(
        worker_turns,
        executor={"reviewer_model": "cdp/opus-5"},
        worker_thread="9001",
    )
    assert result is not None
    assert not result.escape_path
    assert result.model_id == "cdp/opus-5"
    prov = provenance_from_cdp_harvest(
        result, consultant_family="anthropic", consultant_substrate="cdp"
    )
    assert prov is not None
    assert prov.verdict == "ADMIT"
    assert prov.consultant_family == "anthropic"
    assert prov.consultant_substrate == "cdp"


@pytest.mark.offline
def test_parse_cdp_harvest_reads_root_delivery_turns() -> None:
    """5975-w13 geometry: CDP reply on root, worker closeout is machine JSON only."""
    worker_turns = [
        {
            "turn_number": 2,
            "from": "cursor-sdk",
            "body": json.dumps(
                {
                    "status": "complete",
                    "cdp_model": "cdp/opus-5",
                }
            ),
        },
    ]
    assert (
        parse_cdp_consult_harvest(
            worker_turns,
            executor={"reviewer_model": "cdp/opus-5"},
            worker_thread="6099",
        )
        is None
    )
    delivery_turns = [
        {
            "turn_number": 46,
            "from": "cdp",
            "body": (
                "Merits disposition — ADMIT_WITH_AMENDMENTS\n"
                "archive_uri: cortex://notes/system/threads/cdp-ask-archive-new-f1b6e554.md"
            ),
        },
    ]
    result = parse_cdp_consult_harvest(
        worker_turns,
        delivery_turns=delivery_turns,
        root_id="5975",
        executor={"reviewer_model": "cdp/opus-5"},
        worker_thread="6099",
    )
    assert result is not None
    assert not result.escape_path
    assert result.consult_thread == "agent-bus:5975"
    assert result.model_id == "cdp/opus-5"
    prov = provenance_from_cdp_harvest(
        result, consultant_family="anthropic", consultant_substrate="cdp"
    )
    assert prov is not None
    assert prov.verdict == "ADMIT_WITH_AMENDMENTS"
    assert prov.consultant_family == "anthropic"
    assert prov.consultant_substrate == "cdp"


@pytest.mark.offline
def test_parse_cdp_harvest_ignores_machine_closeout_summary() -> None:
    """Machine closeout summary must not mask a root CDP reply."""
    worker_turns = [
        {
            "turn_number": 2,
            "from": "cursor-sdk",
            "body": json.dumps(
                {
                    "status": "complete",
                    "cdp_model": "cdp/opus-5",
                    "summary": "dispatch abc: 41 tool calls, 334.9s",
                }
            ),
        },
    ]
    delivery_turns = [
        {
            "turn_number": 46,
            "from": "cdp",
            "body": "Merits disposition — ADMIT_WITH_AMENDMENTS",
        },
    ]
    result = parse_cdp_consult_harvest(
        worker_turns,
        delivery_turns=delivery_turns,
        root_id="5975",
        executor={"reviewer_model": "cdp/opus-5"},
        worker_thread="6099",
    )
    assert result is not None
    assert "ADMIT_WITH_AMENDMENTS" in result.harvest_text
    prov = provenance_from_cdp_harvest(
        result, consultant_family="anthropic", consultant_substrate="cdp"
    )
    assert prov is not None
    assert prov.verdict == "ADMIT_WITH_AMENDMENTS"


@pytest.mark.offline
def test_parse_cdp_harvest_project_ask_is_escape() -> None:
    worker_turns = [
        {
            "turn_number": 2,
            "body": json.dumps(
                {
                    "status": "complete",
                    "transport": "project_ask",
                    "project_ask_execution_id": "abc",
                }
            ),
        }
    ]
    result = parse_cdp_consult_harvest(worker_turns, worker_thread="9002")
    assert result is not None
    assert result.escape_path is True


@pytest.mark.offline
def test_write_consult_provenance_four_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    data_dir = tmp_path / "charter-runner"
    data_dir.mkdir()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex-files"))
    (tmp_path / "home" / ".local" / "share").mkdir(parents=True)

    from scripts.model_manager.ui.controller.charter_runner.consult_lane import (
        ConsultProvenanceRecord,
        load_consult_provenance,
    )

    uri = write_consult_provenance(
        ConsultProvenanceRecord(
            consult_thread="agent-bus:9003",
            verdict="ADMIT",
            consultant_family="anthropic",
            consultant_substrate="web-anthropic",
            consultant_model="cdp/opus-5",
        ),
        root_id="5975",
    )
    loaded = load_consult_provenance("5975")
    assert loaded is not None
    assert loaded["consult_thread"] == "agent-bus:9003"
    assert loaded["verdict"] == "ADMIT"
    assert loaded["consultant_family"] == "anthropic"
    assert loaded["consultant_substrate"] == "web-anthropic"
    assert uri.startswith("cortex://notes/system/threads/charter-consult-provenance/")


@pytest.mark.offline
def test_write_consult_provenance_home_only_quarantines_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "charter-runner"
    data_dir.mkdir()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.consult_lane._write_consult_provenance_to_shared_root",
        lambda _uri, _content: False,
    )

    from scripts.model_manager.ui.controller.charter_runner.consult_lane import (
        ConsultProvenanceRecord,
        load_consult_provenance,
    )

    uri = write_consult_provenance(
        ConsultProvenanceRecord(
            consult_thread="agent-bus:9004",
            verdict="ADMIT",
            consultant_family="anthropic",
            consultant_substrate="web-anthropic",
            consultant_model="cdp/opus-5",
        ),
        root_id="5976",
    )
    assert uri == ""
    assert load_consult_provenance("5976") is not None
    home_mirror = (
        tmp_path
        / "home"
        / ".local/share/cortex/notes/system/threads/charter-consult-provenance/5976.json"
    )
    assert home_mirror.is_file()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_kernel_queue_consult_on_idle(
    ledger_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Idle tip without a gated Next-pickup fails closed (a:26638 / no_gated_pickup)."""
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore

    env = EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up", "project_ask": "up"},
        attendance_by_root={"5975": "autonomous"},
        scoreboard_pointer={"5975": "cortex://notes/system/threads/5975-charter-scoreboard.md"},
        bus_tip_meta={"5975": {"has_checkpoint": True, "turn_id": ""}},
    )
    outcome = await apply_kernel_tick_for_root(
        "5975",
        [{"turn_number": 1, "subject": "CHECKPOINT", "body": "x"}],
        caps=CapStore(intent_dir=tmp_path / "intent"),
        workspace_root=tmp_path / "ws",
        env=env,
    )
    assert outcome.old_decision_label == "kernel_no_gated_pickup"
    assert outcome.skipped_reason == "no_gated_pickup"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_tick_loop_kernel_sole_admitter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Phase 3: every enrolled root routes through kernel_tick (no old admit)."""
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
    from scripts.model_manager.ui.controller.charter_runner.kernel import (
        CharterRunnerTickLoop,
    )
    from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
    from scripts.model_manager.ui.model.service_state import ServiceInfo, ServiceStatus

    kernel_calls: list[str] = []

    async def fake_kernel(root_id, *_a, **_k):
        kernel_calls.append(root_id)
        return KernelTickOutcome("kernel_queue_consult")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.apply_kernel_tick_for_root",
        fake_kernel,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.enrollment_filter.refresh_migrated_roots_cache",
        lambda: frozenset({"5975", "6091"}),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.host.bus_client.list_enrolled_roots",
        AsyncMock(return_value=[{"id": "5975"}, {"id": "6091"}]),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.host.bus_client.fetch_turns",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.host.harvest_completed_windows",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.host.build_tick_env_snapshot",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.env_snapshot.build_env_snapshot",
        AsyncMock(
            return_value=EnvSnapshot(
                giw_holder_lease={"held": False, "holder": None, "residue": None},
                propagation_residue={"kind": None, "detail": None},
                in_flight_windows=[],
                satellite_health={"cdp": "up", "project_ask": "up"},
                attendance_by_root={"5975": "autonomous", "6091": "autonomous"},
                scoreboard_pointer={},
                bus_tip_meta={},
            )
        ),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.shadow.record_shadow_pass",
        lambda *_a, **_k: MagicMock(rows=[], starved=False, bus_roots=2),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.host.events.emit_manage_charter_tick_scanned",
        AsyncMock(),
    )

    loop = CharterRunnerTickLoop(
        service_state=MagicMock(
            check_cortex_api=lambda: ServiceInfo("", ServiceStatus.RUNNING, ""),
            check_agent_bus=lambda: ServiceInfo("", ServiceStatus.RUNNING, ""),
        ),
        shutdown_gate=ManageShutdownGate(),
        workspace_root=tmp_path,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )
    await loop._tick_once()
    assert kernel_calls == ["5975", "6091"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_tip_declared_r_admit_queues_under_r_admit_despite_judgment_gap_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """a:26872 — stale judgment_gap row must not block tip-declared r_admit queue."""
    import time

    from scripts.model_manager.ui.controller.charter_runner import kernel_tick
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
    from scripts.model_manager.ui.controller.charter_runner.consult_lane import (
        load_queue_row,
    )
    from scripts.model_manager.ui.controller.charter_runner.env_snapshot import (
        EnvSnapshot,
    )
    from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
        RootLedgerRow,
        SeedConfirm,
        open_default_ledger,
        seed_from_confirm,
    )

    data_dir = tmp_path / "charter-runner"
    data_dir.mkdir()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data_dir))
    conn = open_default_ledger()
    seed_from_confirm(
        conn,
        SeedConfirm(
            root_id="6237",
            pickup_gid="G3",
            pickup_lane="mechanical",
            attendance="attended",
            scoreboard_uri="cortex://notes/system/threads/6237-charter-scoreboard.md",
        ),
    )
    now = time.time()
    conn.execute(
        """
        INSERT INTO consult_queue
          (root_id, gid, consult_role, corpus_sha, attempts, next_retry, status,
           created_at, updated_at)
        VALUES ('6237', 'G3', 'judgment_gap', NULL, 0, NULL, 'queued', ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    conn.close()
    assert load_queue_row(open_default_ledger(), "6237", "G3", "r_admit") is None

    queued_roles: list[str] = []

    async def capture_queued(**payload: object) -> None:
        queued_roles.append(str(payload.get("role") or ""))

    monkeypatch.setattr(
        kernel_tick,
        "emit_consult_queued",
        capture_queued,
    )
    monkeypatch.setattr(
        kernel_tick,
        "admit_worker_window",
        AsyncMock(side_effect=AssertionError("must not ADMIT_WORKER")),
    )

    tip_row = (
        "CONSULT_PENDING · consult_role: r_admit · G3 — R-admit · "
        "model=cdp/opus-5 · executor=pending · executor_lane: judgment"
    )
    tip_body = f"""\
# CHECKPOINT — CONSULT_PENDING

## Next-pickup
- {tip_row}

## Steps
1. [ ] G3 — R-admit · [consult:r_admit]

— RESUME (any seat, no command): charter root.
"""
    outcome = await apply_kernel_tick_for_root(
        "6237",
        [{"turn_number": 111, "subject": "CHECKPOINT wave 12", "body": tip_body}],
        caps=CapStore(intent_dir=tmp_path / "intent"),
        workspace_root=tmp_path,
        env=EnvSnapshot(
            giw_holder_lease={"held": False, "holder": None, "residue": None},
            propagation_residue={"kind": None, "detail": None},
            in_flight_windows=[],
            satellite_health={"cdp": "up"},
            attendance_by_root={"6237": "attended"},
            scoreboard_pointer={},
            bus_tip_meta={"6237": {}},
        ),
    )
    assert outcome.old_decision_label == "kernel_queue_consult"
    assert queued_roles == ["r_admit"]
    conn = open_default_ledger()
    try:
        assert load_queue_row(conn, "6237", "G3", "r_admit") is not None
        assert load_queue_row(conn, "6237", "G3", "judgment_gap") is not None
    finally:
        conn.close()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_enqueue_consult_syncs_ledger_consult_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a:26936 — queue insert and ledger CONSULT_QUEUED must be atomic."""
    from scripts.model_manager.ui.controller.charter_runner.consult_lane import (
        enqueue_consult,
    )
    from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
        RootStatus,
        SeedConfirm,
        load_root,
        open_default_ledger,
        seed_from_confirm,
    )

    data_dir = tmp_path / "charter-runner"
    data_dir.mkdir()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data_dir))
    conn = open_default_ledger()
    seed_from_confirm(
        conn,
        SeedConfirm(
            root_id="6237",
            pickup_gid="G3",
            pickup_lane="judgment",
            attendance="autonomous",
            scoreboard_uri="cortex://notes/system/threads/6237-charter-scoreboard.md",
        ),
    )
    row = load_root(conn, "6237")
    assert row is not None
    enqueue_consult(conn, row=row, consult_role="r_admit")
    conn.commit()
    synced = load_root(conn, "6237")
    conn.close()
    assert synced is not None
    assert synced.status == RootStatus.CONSULT_QUEUED
    assert synced.consult_role == "r_admit"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_queue_row_without_ledger_repair_desync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a:26936 — durable queue ahead of ledger must repair, not already_queued stall."""
    import time

    from scripts.model_manager.ui.controller.charter_runner import kernel_tick
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
    from scripts.model_manager.ui.controller.charter_runner.consult_lane import (
        load_queue_row,
    )
    from scripts.model_manager.ui.controller.charter_runner.env_snapshot import (
        EnvSnapshot,
    )
    from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
        RootStatus,
        SeedConfirm,
        load_root,
        open_default_ledger,
        seed_from_confirm,
    )

    data_dir = tmp_path / "charter-runner"
    data_dir.mkdir()
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data_dir))
    conn = open_default_ledger()
    seed_from_confirm(
        conn,
        SeedConfirm(
            root_id="6237",
            pickup_gid="G3",
            pickup_lane="mechanical",
            attendance="autonomous",
            scoreboard_uri="cortex://notes/system/threads/6237-charter-scoreboard.md",
        ),
    )
    now = time.time()
    conn.execute(
        """
        INSERT INTO consult_queue
          (root_id, gid, consult_role, corpus_sha, attempts, next_retry, status,
           created_at, updated_at)
        VALUES ('6237', 'G3', 'r_admit', NULL, 0, NULL, 'queued', ?, ?)
        """,
        (now, now),
    )
    conn.commit()
    assert load_root(conn, "6237").status == RootStatus.IDLE  # type: ignore[union-attr]
    conn.close()

    monkeypatch.setattr(kernel_tick, "emit_consult_queued", AsyncMock())
    monkeypatch.setattr(kernel_tick, "emit_tick_transition", AsyncMock())

    tip_row = (
        "CONSULT_PENDING · consult_role: r_admit · G3 — R-admit · "
        "executor=pending · executor_lane: judgment"
    )
    tip_body = f"""\
# CHECKPOINT — CONSULT_PENDING

## Next-pickup
- {tip_row}

## Steps
1. [ ] G3 — R-admit · [consult:r_admit]

— RESUME (any seat, no command): charter root.
"""
    outcome = await apply_kernel_tick_for_root(
        "6237",
        [{"turn_number": 150, "subject": "CHECKPOINT wave 20", "body": tip_body}],
        caps=CapStore(intent_dir=tmp_path / "intent"),
        workspace_root=tmp_path,
        env=EnvSnapshot(
            giw_holder_lease={"held": False, "holder": None, "residue": None},
            propagation_residue={"kind": None, "detail": None},
            in_flight_windows=[],
            satellite_health={"cdp": "up"},
            attendance_by_root={"6237": "autonomous"},
            scoreboard_pointer={},
            bus_tip_meta={"6237": {}},
        ),
    )
    assert outcome.old_decision_label == "kernel_queue_consult"
    conn = open_default_ledger()
    try:
        row = load_root(conn, "6237")
        assert row is not None
        assert row.status == RootStatus.CONSULT_QUEUED
        assert row.consult_role == "r_admit"
        assert load_queue_row(conn, "6237", "G3", "r_admit") is not None
    finally:
        conn.close()
