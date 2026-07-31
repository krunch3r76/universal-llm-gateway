"""S3 birth atomicity — birth-grace guard (typed-valid never state-close)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner.admission import (
    TYPED_GRACE_REASON,
    CapStore,
    Decision,
    evaluate_root,
)
from scripts.model_manager.ui.controller.charter_runner.admission.typed_work_item import (
    TypedWorkItemAdmit,
)
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.kernel_tick import (
    apply_kernel_tick_for_root,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootLedgerRow,
    RootStatus,
    admit_work_item,
    load_all_roots,
    upsert_root,
)
from scripts.model_manager.ui.controller.charter_runner.state_close import (
    emit_skip_and_maybe_state_close,
    maybe_state_close_root,
)


def _ungated_prose_tip() -> str:
    return """\
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


def _turn(n: int, subject: str, body: str) -> dict[str, Any]:
    return {"turn_number": n, "subject": subject, "body": body, "from_agent": "cursor"}


@pytest.fixture
def ledger_dir(tmp_path: Path) -> Path:
    data = tmp_path / "ledger"
    data.mkdir()
    return data


def _ledger_conn(ledger_dir: Path):
    return open_ledger_db(ledger_dir / "root-ledger.sqlite")


def _seed_valid_row(conn, root_id: str = "7200") -> RootLedgerRow:
    return admit_work_item(
        conn,
        TypedWorkItemAdmit(
            root_id=root_id,
            pickup_gid="G1",
            pickup_lane="judgment",
            attendance="autonomous",
            scoreboard_uri=f"cortex://notes/system/threads/{root_id}-charter-scoreboard.md",
        ),
    )


def _seed_invalid_row(conn, root_id: str = "7201") -> None:
    upsert_root(
        conn,
        RootLedgerRow(
            root_id=root_id,
            status=RootStatus.IDLE,
            pickup_gid="G1",
            pickup_lane="judgment",
            pickup_executor=None,
            attendance="autonomous",
            scoreboard_uri="",
        ),
    )


@pytest.fixture
def ledger_patch(ledger_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.open_default_ledger",
        lambda: _ledger_conn(ledger_dir),
    )


@pytest.fixture
def event_collector(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(signal: str, payload: dict[str, Any]) -> None:
        events.append((signal, dict(payload)))

    monkeypatch.setattr(
        "scripts.model_manager.observation_event_charter._emit",
        capture,
    )
    return events


@pytest.fixture
def bus_recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def close_root_thread(root_id: str, **kwargs: Any) -> None:
        calls.append(("close_root_thread", {"root_id": root_id, **kwargs}))

    async def unenroll_root(root_id: str) -> dict[str, Any]:
        calls.append(("unenroll_root", {"root_id": root_id}))
        return {"tags": [], "unenrolled": True}

    async def fetch_thread(_root_id: str) -> dict[str, Any]:
        return {"status": "active"}

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.bus_client.close_root_thread",
        close_root_thread,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.bus_client.unenroll_root",
        unenroll_root,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.bus_client.fetch_thread",
        fetch_thread,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.prepare_state_close_summary",
        AsyncMock(return_value=("summary", "uri")),
    )
    return calls


def _decision(
    root_id: str = "7200",
    *,
    reason: str = "no_gated_pickup",
) -> Decision:
    body = _ungated_prose_tip()
    return Decision(
        False,
        reason,
        root_id,
        checkpoint=_turn(2, "CHECKPOINT wave 2", body),
        half="body",
    )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac1_chokepoint_falsifier_typed_valid_no_close(
    ledger_dir: Path,
    ledger_patch: None,
    bus_recorder: list,
    event_collector: list,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_valid_row(conn)
    finally:
        conn.close()

    count = await maybe_state_close_root(
        _decision(),
        reason="no_gated_pickup",
        state_closes_this_tick=0,
    )
    assert count == 0
    assert bus_recorder == []
    assert not any(sig == "manage.charter.tick.root_closed" for sig, _ in event_collector)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac2_gate_skip_without_root_closed(
    ledger_dir: Path,
    ledger_patch: None,
    event_collector: list,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_valid_row(conn)
    finally:
        conn.close()

    skipped: dict[str, int] = {}
    await emit_skip_and_maybe_state_close(
        _decision(),
        state_closes_this_tick=0,
        skipped_by_reason=skipped,
    )
    assert skipped.get("no_gated_pickup") == 1
    assert any(sig == "manage.charter.tick.root_skipped" for sig, _ in event_collector)
    assert not any(sig == "manage.charter.tick.root_closed" for sig, _ in event_collector)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac3_a4_budget_not_consumed(
    ledger_dir: Path,
    ledger_patch: None,
    bus_recorder: list,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_valid_row(conn, root_id="7200")
    finally:
        conn.close()

    count = 0
    count = await maybe_state_close_root(
        _decision("7200"),
        reason="no_gated_pickup",
        state_closes_this_tick=count,
        max_state_closes=1,
    )
    assert count == 0

    count = await maybe_state_close_root(
        _decision("9999"),
        reason="no_gated_pickup",
        state_closes_this_tick=count,
        max_state_closes=1,
    )
    assert count == 1
    assert any(c[0] == "close_root_thread" and c[1]["root_id"] == "9999" for c in bus_recorder)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac4_no_row_and_invalid_row_still_close(
    ledger_dir: Path,
    ledger_patch: None,
    bus_recorder: list,
    event_collector: list,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_invalid_row(conn, root_id="7201")
    finally:
        conn.close()

    for root_id in ("8888", "7201"):
        bus_recorder.clear()
        event_collector.clear()
        count = await maybe_state_close_root(
            _decision(root_id),
            reason="no_gated_pickup",
            state_closes_this_tick=0,
        )
        assert count == 1
        assert any(c[0] == "close_root_thread" for c in bus_recorder)
        assert any(c[0] == "unenroll_root" for c in bus_recorder)
        closed = [
            p for sig, p in event_collector if sig == "manage.charter.tick.root_closed"
        ]
        assert closed and closed[0]["closed"] is True


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac5_stale_window_scope_preserved(
    ledger_dir: Path,
    ledger_patch: None,
    bus_recorder: list,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_valid_row(conn)
    finally:
        conn.close()

    count = await maybe_state_close_root(
        _decision(),
        reason="stale_window",
        state_closes_this_tick=0,
    )
    assert count == 1
    assert any(c[0] == "close_root_thread" for c in bus_recorder)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac6_ledger_read_failure_non_destructive(
    ledger_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    bus_recorder: list,
    event_collector: list,
) -> None:
    def boom() -> None:
        raise OSError("ledger unavailable")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.open_default_ledger",
        boom,
    )

    count = await maybe_state_close_root(
        _decision(),
        reason="no_gated_pickup",
        state_closes_this_tick=0,
    )
    assert count == 0
    assert bus_recorder == []
    guarded = [
        p for sig, p in event_collector if sig == "manage.charter.tick.root_close_guarded"
    ]
    assert len(guarded) == 1
    assert guarded[0]["guard"] == "ledger_read_failed"
    assert not any(sig == "manage.charter.tick.root_closed" for sig, _ in event_collector)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac7_root_close_guarded_signal(
    ledger_dir: Path,
    ledger_patch: None,
    event_collector: list,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_valid_row(conn)
    finally:
        conn.close()

    await maybe_state_close_root(
        _decision(),
        reason="no_gated_pickup",
        state_closes_this_tick=0,
    )
    guarded = [
        (sig, p)
        for sig, p in event_collector
        if sig == "manage.charter.tick.root_close_guarded"
    ]
    assert len(guarded) == 1
    sig, payload = guarded[0]
    assert sig != "manage.charter.tick.root_closed"
    assert payload == {
        "root": "7200",
        "reason": "no_gated_pickup",
        "guard": "typed_record_valid",
        "checkpoint_turn": 2,
    }


@pytest.mark.offline
def test_ac8_evaluate_root_typed_grace_reason() -> None:
    body = _ungated_prose_tip()
    turns = [_turn(2, "CHECKPOINT wave 2", body)]
    valid_row = RootLedgerRow(
        root_id="7200",
        status=RootStatus.IDLE,
        pickup_gid="G1",
        pickup_lane="judgment",
        pickup_executor=None,
        attendance="autonomous",
        scoreboard_uri="cortex://notes/system/threads/7200-charter-scoreboard.md",
    )

    grace = evaluate_root("7200", turns, CapStore(), ledger_row=valid_row)
    assert grace.eligible is False
    assert grace.reason == TYPED_GRACE_REASON
    assert grace.reason != "no_gated_pickup"

    blind = evaluate_root("7200", turns, CapStore())
    assert blind.eligible is False
    assert blind.reason == "no_gated_pickup"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac9_kernel_producer_fence(
    ledger_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_id = "7300"
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
    finally:
        conn.close()

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
    body = _ungated_prose_tip()
    outcome = await apply_kernel_tick_for_root(
        root_id,
        [_turn(2, "CHECKPOINT wave 2", body)],
        caps=CapStore(intent_dir=tmp_path / "intent"),
        workspace_root=tmp_path / "ws",
        env=env,
    )
    assert outcome.skipped_reason != "no_gated_pickup"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_ac10_read_only_ledger(
    ledger_dir: Path,
    ledger_patch: None,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_valid_row(conn)
        before = load_all_roots(conn)
    finally:
        conn.close()

    await maybe_state_close_root(
        _decision(),
        reason="no_gated_pickup",
        state_closes_this_tick=0,
    )

    conn = _ledger_conn(ledger_dir)
    try:
        after = load_all_roots(conn)
    finally:
        conn.close()
    assert before == after


@pytest.mark.offline
def test_ac11_docstring_floor() -> None:
    from scripts.model_manager import observation_event_charter as charter_events
    from scripts.model_manager.ui.controller.charter_runner import state_close

    for obj in (
        state_close.maybe_state_close_root,
        state_close._birth_grace_verdict,
        charter_events.emit_manage_charter_tick_root_close_guarded,
        evaluate_root,
        TYPED_GRACE_REASON,
    ):
        doc = getattr(obj, "__doc__", None) if not isinstance(obj, str) else obj
        if isinstance(obj, str):
            assert obj  # constant name is self-documenting via module doc
            continue
        assert doc and len(doc.strip()) >= 40, f"missing docstring floor for {obj!r}"
        lowered = doc.lower()
        if obj is state_close.maybe_state_close_root:
            assert "ledger" in lowered
            assert "budget" in lowered or "a4" in lowered
        if obj is state_close._birth_grace_verdict:
            assert "fail" in lowered or "not closing" in lowered or "read" in lowered
