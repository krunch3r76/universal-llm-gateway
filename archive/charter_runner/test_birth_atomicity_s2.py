"""S2 birth atomicity — birth_work_item ceremony (mint → seed → tag-commit)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner import birth
from scripts.model_manager.ui.controller.charter_runner.admission.typed_work_item import (
    TypedAdmitError,
    TypedWorkItemAdmit,
    typed_record_valid,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_admit_gate import (
    validate_admit_eligibility,
)
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.kernel_tick import (
    apply_kernel_tick_for_root,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    admit_work_item,
    load_root,
    upsert_root,
)


@pytest.fixture
def ledger_dir(tmp_path: Path) -> Path:
    data = tmp_path / "ledger"
    data.mkdir()
    return data


def _ledger_conn(ledger_dir: Path):
    return open_ledger_db(ledger_dir / "root-ledger.sqlite")


def _birth_kwargs(**overrides: Any) -> dict[str, Any]:
    base = {
        "slug": "fresh-root",
        "pickup_gid": "G1",
        "pickup_lane": "judgment",
        "attendance": "autonomous",
        "scoreboard_uri": "cortex://notes/system/threads/fresh-root-scoreboard.md",
    }
    base.update(overrides)
    return base


class _BusRecorder:
    """Collect async bus/ledger call order for ordering assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.enroll_count = 0
        self._next_id = "7001"

    async def find_thread_id_by_slug(self, slug: str) -> str | None:
        self.calls.append(("find_thread_id_by_slug", {"slug": slug}))
        return None

    async def create_thread(self, **kwargs: Any) -> str:
        self.calls.append(("create_thread", dict(kwargs)))
        root_id = self._next_id
        self._next_id = str(int(self._next_id) + 1)
        return root_id

    async def enroll_root(self, root_id: str) -> dict[str, Any]:
        self.enroll_count += 1
        self.calls.append(("enroll_root", {"root_id": root_id}))
        return {"tags": ["charter-runner"], "enrolled": True}

    async def post_root_checkpoint(self, root_id: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("post_root_checkpoint", {"root_id": root_id, **kwargs}))
        return {}


@pytest.fixture
def bus_recorder(monkeypatch: pytest.MonkeyPatch) -> _BusRecorder:
    rec = _BusRecorder()

    async def reclaim(slug: str) -> str | None:
        rec.calls.append(("find_thread_id_by_slug", {"slug": slug}))
        return "6999"

    monkeypatch.setattr(birth.bus_client, "find_thread_id_by_slug", rec.find_thread_id_by_slug)
    monkeypatch.setattr(birth.bus_client, "create_thread", rec.create_thread)
    monkeypatch.setattr(birth.bus_client, "enroll_root", rec.enroll_root)
    monkeypatch.setattr(birth.bus_client, "post_root_checkpoint", rec.post_root_checkpoint)
    return rec


@pytest.fixture
def ledger_patch(
    ledger_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        birth,
        "open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.write_cortex_mirror",
        lambda _row: None,
    )


@pytest.fixture
def event_collector(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(signal: str, payload: dict[str, Any]) -> None:
        events.append((signal, dict(payload)))

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.telemetry._emit",
        capture,
    )
    return events


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac1_create_thread_never_enrolls_on_mint(
    bus_recorder: _BusRecorder,
    ledger_patch: None,
    event_collector: list,
) -> None:
    await birth.birth_work_item(**_birth_kwargs())
    mint_calls = [
        kw for name, kw in bus_recorder.calls if name == "create_thread"
    ]
    assert len(mint_calls) == 1
    assert not mint_calls[0].get("enroll_charter_runner")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac2_mint_seed_commit_order(
    bus_recorder: _BusRecorder,
    ledger_patch: None,
    event_collector: list,
) -> None:
    await birth.birth_work_item(**_birth_kwargs())
    names = [name for name, _ in bus_recorder.calls]
    assert names == [
        "find_thread_id_by_slug",
        "create_thread",
        "enroll_root",
    ]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac3_seed_fail_no_enroll(
    ledger_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_collector: list,
) -> None:
    rec = _BusRecorder()
    monkeypatch.setattr(birth.bus_client, "find_thread_id_by_slug", rec.find_thread_id_by_slug)
    monkeypatch.setattr(birth.bus_client, "create_thread", rec.create_thread)
    monkeypatch.setattr(birth.bus_client, "enroll_root", rec.enroll_root)
    monkeypatch.setattr(
        birth,
        "open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )

    def boom(_conn, _admit: TypedWorkItemAdmit) -> RootLedgerRow:
        raise TypedAdmitError(detail="bad seed", field="pickup_lane")

    monkeypatch.setattr(birth, "admit_work_item", boom)

    with pytest.raises(birth.BirthError) as exc:
        await birth.birth_work_item(**_birth_kwargs())
    assert exc.value.step == "seed"
    assert rec.enroll_count == 0


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac4_reclaim_no_duplicate_thread(
    ledger_patch: None,
    monkeypatch: pytest.MonkeyPatch,
    event_collector: list,
) -> None:
    rec = _BusRecorder()

    async def reclaim(slug: str) -> str | None:
        rec.calls.append(("find_thread_id_by_slug", {"slug": slug}))
        return "6999"

    monkeypatch.setattr(birth.bus_client, "find_thread_id_by_slug", reclaim)
    monkeypatch.setattr(birth.bus_client, "create_thread", rec.create_thread)
    monkeypatch.setattr(birth.bus_client, "enroll_root", rec.enroll_root)

    outcome = await birth.birth_work_item(**_birth_kwargs())
    assert outcome.reclaimed is True
    assert outcome.minted is False
    assert outcome.root_id == "6999"
    assert not any(name == "create_thread" for name, _ in rec.calls)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac5_preserve_on_rerun(
    ledger_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_collector: list,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        admit_work_item(
            conn,
            TypedWorkItemAdmit(
                root_id="6999",
                pickup_gid="G2",
                pickup_lane="mechanical",
                attendance="autonomous",
                scoreboard_uri="cortex://notes/system/threads/6999-charter-scoreboard.md",
            ),
        )
        row = load_root(conn, "6999")
        assert row is not None
        upsert_root(
            conn,
            RootLedgerRow(
                root_id=row.root_id,
                status=RootStatus.ADMITTED,
                pickup_gid=row.pickup_gid,
                pickup_lane=row.pickup_lane,
                pickup_executor=row.pickup_executor,
                attendance=row.attendance,
                scoreboard_uri=row.scoreboard_uri,
                wip_window_id="6999-w3",
            ),
        )
    finally:
        conn.close()

    rec = _BusRecorder()

    async def reclaim(slug: str) -> str | None:
        rec.calls.append(("find_thread_id_by_slug", {"slug": slug}))
        return "6999"

    monkeypatch.setattr(birth.bus_client, "find_thread_id_by_slug", reclaim)
    monkeypatch.setattr(birth.bus_client, "create_thread", rec.create_thread)
    monkeypatch.setattr(birth.bus_client, "enroll_root", rec.enroll_root)
    monkeypatch.setattr(
        birth,
        "open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.write_cortex_mirror",
        lambda _row: None,
    )

    await birth.birth_work_item(
        **_birth_kwargs(
            pickup_gid="G9",
            pickup_lane="judgment",
            attendance="attended",
        )
    )
    conn = _ledger_conn(ledger_dir)
    try:
        row = load_root(conn, "6999")
        assert row is not None
        assert row.pickup_gid == "G2"
        assert row.pickup_lane == "mechanical"
        assert row.status == RootStatus.ADMITTED
        assert row.wip_window_id == "6999-w3"
    finally:
        conn.close()

    await birth.birth_work_item(
        **_birth_kwargs(
            pickup_gid="G9",
            pickup_lane="judgment",
            attendance="attended",
            on_existing="readmit",
        )
    )
    conn = _ledger_conn(ledger_dir)
    try:
        row = load_root(conn, "6999")
        assert row is not None
        assert row.pickup_gid == "G9"
        assert row.pickup_lane == "judgment"
        assert row.status == RootStatus.ADMITTED
        assert row.wip_window_id == "6999-w3"
    finally:
        conn.close()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac6_enroll_idempotent(
    bus_recorder: _BusRecorder,
    ledger_patch: None,
    event_collector: list,
) -> None:
    first = await birth.birth_work_item(**_birth_kwargs())
    second = await birth.birth_work_item(**_birth_kwargs())
    assert first.enrolled is True
    assert second.enrolled is True
    assert bus_recorder.enroll_count == 2


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac6_double_run_one_ledger_row(
    ledger_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_collector: list,
) -> None:
    rec = _BusRecorder()
    minted_id: list[str] = []

    async def find_or_reclaim(slug: str) -> str | None:
        rec.calls.append(("find_thread_id_by_slug", {"slug": slug}))
        return minted_id[0] if minted_id else None

    async def mint_once(**kwargs: Any) -> str:
        rec.calls.append(("create_thread", dict(kwargs)))
        root_id = "7001"
        minted_id.append(root_id)
        return root_id

    monkeypatch.setattr(birth.bus_client, "find_thread_id_by_slug", find_or_reclaim)
    monkeypatch.setattr(birth.bus_client, "create_thread", mint_once)
    monkeypatch.setattr(birth.bus_client, "enroll_root", rec.enroll_root)
    monkeypatch.setattr(
        birth,
        "open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.write_cortex_mirror",
        lambda _row: None,
    )

    await birth.birth_work_item(**_birth_kwargs())
    await birth.birth_work_item(**_birth_kwargs())
    conn = _ledger_conn(ledger_dir)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM root_ledger").fetchone()
        assert int(rows[0]) == 1
        assert minted_id == ["7001"]
    finally:
        conn.close()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac7_valid_row_after_success(
    bus_recorder: _BusRecorder,
    ledger_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_collector: list,
) -> None:
    monkeypatch.setattr(
        birth,
        "open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.write_cortex_mirror",
        lambda _row: None,
    )
    outcome = await birth.birth_work_item(**_birth_kwargs())
    conn = _ledger_conn(ledger_dir)
    try:
        row = load_root(conn, outcome.root_id)
        assert typed_record_valid(row)
    finally:
        conn.close()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac8_typed_admit_tip_optional(
    ledger_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.model_manager.ui.controller.charter_runner.admission import CapStore

    root_id = "7100"
    conn = _ledger_conn(ledger_dir)
    try:
        admit_work_item(
            conn,
            TypedWorkItemAdmit(
                root_id=root_id,
                pickup_gid="G1",
                pickup_lane="judgment",
                attendance="autonomous",
                scoreboard_uri=f"cortex://notes/system/threads/{root_id}-charter-scoreboard.md",
            ),
        )
        row = load_root(conn, root_id)
        assert row is not None
    finally:
        conn.close()

    verdict = validate_admit_eligibility("", ledger_row=row)
    assert verdict.ok is True
    assert verdict.reason == "typed_admit"

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.write_cortex_mirror",
        lambda _row: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.emit_consult_queued",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.emit_tick_transition",
        AsyncMock(),
    )

    env = EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up", "project_ask": "up"},
        attendance_by_root={root_id: "autonomous"},
        scoreboard_pointer={
            root_id: f"cortex://notes/system/threads/{root_id}-charter-scoreboard.md"
        },
        bus_tip_meta={root_id: {"has_checkpoint": False, "turn_id": ""}},
    )
    with caplog.at_level("INFO"):
        outcome = await apply_kernel_tick_for_root(
            root_id,
            [],
            caps=CapStore(intent_dir=tmp_path / "intent"),
            workspace_root=tmp_path / "ws",
            env=env,
        )
    assert outcome.old_decision_label != "kernel_unseeded"
    assert outcome.skipped_reason != "migrate_typed_admit"
    assert any("typed_admit_dispatch" in rec.message for rec in caplog.records)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac9_birth_events_success(
    bus_recorder: _BusRecorder,
    ledger_patch: None,
    event_collector: list[tuple[str, dict[str, Any]]],
) -> None:
    await birth.birth_work_item(**_birth_kwargs())
    step_events = [p for sig, p in event_collector if sig == "manage.charter.birth.step"]
    completed = [p for sig, p in event_collector if sig == "manage.charter.birth.completed"]
    assert len(step_events) >= 4
    assert len(completed) == 1
    assert completed[0]["minted"] is True
    assert completed[0]["reclaimed"] is False
    assert completed[0]["enrolled"] is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac9_birth_events_failure_no_completed(
    ledger_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_collector: list[tuple[str, dict[str, Any]]],
) -> None:
    rec = _BusRecorder()
    monkeypatch.setattr(birth.bus_client, "find_thread_id_by_slug", rec.find_thread_id_by_slug)
    monkeypatch.setattr(birth.bus_client, "create_thread", rec.create_thread)
    monkeypatch.setattr(birth.bus_client, "enroll_root", rec.enroll_root)
    monkeypatch.setattr(
        birth,
        "open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )

    def boom(_conn, _admit: TypedWorkItemAdmit) -> RootLedgerRow:
        raise TypedAdmitError(detail="bad seed", field="pickup_lane")

    monkeypatch.setattr(birth, "admit_work_item", boom)

    with pytest.raises(birth.BirthError):
        await birth.birth_work_item(**_birth_kwargs())
    failed_steps = [
        p
        for sig, p in event_collector
        if sig == "manage.charter.birth.step" and p.get("outcome") == "failed"
    ]
    assert failed_steps
    assert not any(sig == "manage.charter.birth.completed" for sig, _ in event_collector)
