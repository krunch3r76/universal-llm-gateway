"""Exhausted-hopper fence — typed DONE tip closes instead of re-admit thrash (a:27285)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner.admission import (
    CapStore,
    Decision,
)
from scripts.model_manager.ui.controller.charter_runner.admission.typed_work_item import (
    TypedWorkItemAdmit,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    EMPTY_GATED_PICKUP_SENTINEL,
    emit_footer,
    is_exhausted_hopper_footer,
)
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.kernel.skip_side_effects import (
    apply_skip_side_effects,
)
from scripts.model_manager.ui.controller.charter_runner.kernel_tick import (
    apply_kernel_tick_for_root,
)
from scripts.model_manager.ui.controller.charter_runner.root_health import (
    AdmitResult,
    FireAttemptOutcome,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootStatus,
    admit_work_item,
    load_root,
)
from scripts.model_manager.ui.controller.charter_runner.state_close import (
    maybe_state_close_root,
)

pytestmark = pytest.mark.offline

_ROOT = "6518"


def _turn(n: int, subject: str, body: str) -> dict[str, Any]:
    return {"turn_number": n, "subject": subject, "body": body, "from_agent": "cursor"}


def _exhausted_done_tip(*, status: str = "CHECKPOINT") -> str:
    prose = """\
# CHECKPOINT — arc complete

## In-flight / WIP
_None this window._

## Next pickup
_None — arc complete._

## Steps
1. [x] G1 — done

## Frictions
_None this window._

— RESUME (any seat, no command): charter root.
"""
    footer = emit_footer(
        schema_version=1,
        status=status,
        next_pickup=dict(EMPTY_GATED_PICKUP_SENTINEL),
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=0,
        evidence=[],
        window_id=f"charter-{_ROOT}-w2",
        transition_id=None,
    )
    return f"{prose}\n{footer}"


def _gated_work_tip() -> str:
    prose = """\
# CHECKPOINT — next slice

## In-flight / WIP
_None this window._

## Next pickup
- G2 — implement slice · lane=judgment · executor=cursor/grok-4.5

## Steps
1. [ ] G2 — implement slice

## Frictions
_None this window._

— RESUME (any seat, no command): charter root.
"""
    footer = emit_footer(
        schema_version=1,
        status="CHECKPOINT",
        next_pickup={"gid": "G2", "lane": "judgment", "executor": "cursor/grok-4.5"},
        wip=None,
        consult={"role": None, "poll_hint": None, "from": None},
        revise_count=0,
        evidence=[],
        window_id=f"charter-{_ROOT}-w2",
        transition_id=None,
    )
    return f"{prose}\n{footer}"


@pytest.fixture
def ledger_dir(tmp_path: Path) -> Path:
    data = tmp_path / "ledger"
    data.mkdir()
    return data


def _ledger_conn(ledger_dir: Path):
    return open_ledger_db(ledger_dir / "root-ledger.sqlite")


def _seed_typed(conn, root_id: str = _ROOT) -> None:
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


def _env(root_id: str = _ROOT) -> EnvSnapshot:
    return EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up", "project_ask": "up"},
        attendance_by_root={root_id: "autonomous"},
        scoreboard_pointer={
            root_id: f"cortex://notes/system/threads/{root_id}-charter-scoreboard.md"
        },
        bus_tip_meta={root_id: {"has_checkpoint": True, "turn_id": "t2"}},
    )


def test_is_exhausted_hopper_footer_sentinel() -> None:
    body = _exhausted_done_tip()
    assert is_exhausted_hopper_footer(body) is True


def test_is_exhausted_hopper_footer_rejects_gated_next_pickup() -> None:
    assert is_exhausted_hopper_footer(_gated_work_tip()) is False


@pytest.mark.asyncio
async def test_typed_done_tip_skips_exhausted_hopper_no_admit(
    ledger_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_typed(conn)
        assert load_root(conn, _ROOT) is not None
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

    async def refuse_admit(**_kw: Any) -> AdmitResult:
        raise AssertionError("exhausted_hopper must not ADMIT_WORKER")

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.admit_worker_window",
        refuse_admit,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.admit_consult_window",
        refuse_admit,
    )

    outcome = await apply_kernel_tick_for_root(
        _ROOT,
        [_turn(2, "CHECKPOINT — done", _exhausted_done_tip())],
        caps=CapStore(intent_dir=tmp_path / "intent"),
        workspace_root=tmp_path / "ws",
        env=_env(),
    )
    assert outcome.old_decision_label == "kernel_exhausted_hopper"
    assert outcome.skipped_reason == "exhausted_hopper"
    assert outcome.admitted is False


@pytest.mark.asyncio
async def test_typed_gated_next_pickup_still_admits(
    ledger_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_typed(conn)
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
    fired: list[bool] = []

    async def fake_admit(**_kw: Any) -> AdmitResult:
        fired.append(True)
        return AdmitResult(
            admitted=True,
            fire_attempt_outcome=FireAttemptOutcome.FIRED,
        )

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel_tick.admit_worker_window",
        fake_admit,
    )

    outcome = await apply_kernel_tick_for_root(
        _ROOT,
        [_turn(2, "CHECKPOINT — work", _gated_work_tip())],
        caps=CapStore(intent_dir=tmp_path / "intent"),
        workspace_root=tmp_path / "ws",
        env=_env(),
    )
    assert outcome.admitted is True
    assert fired == [True]
    assert outcome.skipped_reason is None


@pytest.mark.asyncio
async def test_typed_ungated_tip_without_sentinel_not_exhausted(
    ledger_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserves typed-admit when footer lacks exhaustion sentinel (S3 birth-grace path)."""
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_typed(conn, root_id="7300")
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

    ungated = """\
# CHECKPOINT — idle ungated

## Next-pickup
- finish the thing

## Steps
1. [ ] work
"""
    outcome = await apply_kernel_tick_for_root(
        "7300",
        [_turn(2, "CHECKPOINT wave 2", ungated)],
        caps=CapStore(intent_dir=tmp_path / "intent"),
        workspace_root=tmp_path / "ws",
        env=_env("7300"),
    )
    assert outcome.skipped_reason != "exhausted_hopper"


@pytest.fixture
def ledger_patch(ledger_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _open() -> Any:
        return _ledger_conn(ledger_dir)

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.open_default_ledger",
        _open,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.open_default_ledger",
        _open,
    )


@pytest.mark.asyncio
async def test_exhausted_hopper_state_closes_typed_valid(
    ledger_dir: Path,
    ledger_patch: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_typed(conn)
    finally:
        conn.close()

    bus_calls: list[tuple[str, dict[str, Any]]] = []

    async def close_root_thread(root_id: str, **kwargs: Any) -> None:
        bus_calls.append(("close_root_thread", {"root_id": root_id, **kwargs}))

    async def unenroll_root(root_id: str) -> dict[str, Any]:
        bus_calls.append(("unenroll_root", {"root_id": root_id}))
        return {"tags": [], "unenrolled": True}

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
        AsyncMock(return_value={"status": "active"}),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.prepare_state_close_summary",
        AsyncMock(return_value=("summary", "uri")),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.write_cortex_mirror",
        lambda _row: None,
    )

    decision = Decision(
        False,
        "exhausted_hopper",
        _ROOT,
        checkpoint=_turn(2, "CHECKPOINT — done", _exhausted_done_tip()),
    )
    count = await maybe_state_close_root(
        decision,
        reason="exhausted_hopper",
        state_closes_this_tick=0,
    )
    assert count == 1
    assert any(op == "close_root_thread" for op, _ in bus_calls)
    assert any(op == "unenroll_root" for op, _ in bus_calls)

    conn = _ledger_conn(ledger_dir)
    try:
        row = load_root(conn, _ROOT)
        assert row is not None
        assert row.status == RootStatus.CLOSED
        assert row.wip_window_id is None
        assert row.last_error == "state_close:exhausted_hopper"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_skip_side_effects_exhausted_hopper_closes(
    ledger_dir: Path,
    ledger_patch: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _ledger_conn(ledger_dir)
    try:
        _seed_typed(conn)
    finally:
        conn.close()

    closed: list[str] = []

    async def close_root_thread(root_id: str, **_kw: Any) -> None:
        closed.append(root_id)

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.bus_client.close_root_thread",
        close_root_thread,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.bus_client.unenroll_root",
        AsyncMock(return_value={"tags": [], "unenrolled": True}),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.bus_client.fetch_thread",
        AsyncMock(return_value={"status": "active"}),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.state_close.prepare_state_close_summary",
        AsyncMock(return_value=("summary", "uri")),
    )

    turns = [_turn(2, "CHECKPOINT — done", _exhausted_done_tip())]
    skipped: dict[str, int] = {}
    count = await apply_skip_side_effects(
        root_id=_ROOT,
        turns=turns,
        skipped_reason="exhausted_hopper",
        old_decision_label="kernel_exhausted_hopper",
        admitted=False,
        state_closes_this_tick=0,
        skipped_by_reason=skipped,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )
    assert skipped.get("exhausted_hopper") == 1
    assert count == 1
    assert closed == [_ROOT]
