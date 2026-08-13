"""Pickup advance, window monotonicity, and harvest release (a:26628 class).

Falsifiers for the four defects that let root 5975 fire seven windows on one
packet: a write-once ``pickup_gid``, a bus-only window counter that reset to 1, a
``wip_window_id`` nothing ever cleared, and an admit path that never checked the tip
for a live gated row.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner import (
    kernel_tick,
    pickup_advance,
    window_log,
    window_sequence,
)
from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    parse_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    RootStatus,
    SeedConfirm,
    Transition,
    load_root,
    seed_from_confirm,
)

_TIP_TEMPLATE = """\
# CHECKPOINT — agent-bus:5975

## In-flight / WIP
_None this window._

## Next-pickup
- {row}

## Steps
1. [ ] Advance the pickup

## Frictions
_None this window._

— RESUME (any seat, no command): charter root.
"""


def _tip(row: str) -> str:
    return _TIP_TEMPLATE.format(row=row)


def _parsed(row: str):
    return parse_checkpoint(_tip(row))


def _turn(n: int, subject: str, body: str = "") -> dict[str, Any]:
    return {"turn_number": n, "subject": subject, "body": body, "from_agent": "cursor"}


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated ledger + suppressed cortex mirror; yields an open connection."""
    db = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger.default_ledger_path",
        lambda: db,
    )
    for module in (kernel_tick, pickup_advance, window_sequence):
        monkeypatch.setattr(module, "write_cortex_mirror", lambda _row: "")
    conn = open_ledger_db(db)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def local_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(window_log, "LOG_DIR", tmp_path / "cr")
    monkeypatch.setattr(window_log, "_HARVESTED_DIR", tmp_path / "cr" / "harvested")
    return tmp_path / "cr"


def _seed(conn, *, gid: str = "G7", lane: str = "judgment", attendance: str = "attended"):
    return seed_from_confirm(
        conn,
        SeedConfirm(
            root_id="5975",
            pickup_gid=gid,
            pickup_lane=lane,
            attendance=attendance,
            scoreboard_uri="cortex://notes/system/threads/5975-charter-scoreboard.md",
        ),
    )


# ---- gated pickup extraction --------------------------------------------------


@pytest.mark.offline
def test_gated_pickup_reads_gid_lane_and_executor() -> None:
    live = pickup_advance.gated_pickup_from_parsed(
        _parsed("G8 — wire the loop · executor_lane: implement · executor=cursor/composer-2.5")
    )
    assert live is not None
    assert (live.gid, live.lane, live.executor) == (
        "G8",
        "implement",
        "cursor/composer-2.5",
    )


@pytest.mark.offline
def test_gated_pickup_strips_markdown_backticks_on_executor() -> None:
    """Prose tips often write executor=`cursor/…` — must not empty-hopper (6237)."""
    live = pickup_advance.gated_pickup_from_parsed(
        _parsed(
            "G2 — A + Gate-2 · detent=standard · executor=`cursor/grok-4.6` · "
            "executor_lane: judgment"
        )
    )
    assert live is not None
    assert live.executor == "cursor/grok-4.6"
    assert pickup_advance.tip_executor_is_explicitly_unbound(live) is False


@pytest.mark.offline
def test_gated_pickup_skips_id_less_closeout_row() -> None:
    """A bare ``CLOSEOUT`` row gates admission but names no gid to advance to."""
    assert pickup_advance.gated_pickup_from_parsed(_parsed("CLOSEOUT — arc done")) is None


@pytest.mark.offline
def test_gated_pickup_none_without_tip() -> None:
    assert pickup_advance.gated_pickup_from_parsed(None) is None


# ---- ledger advance ----------------------------------------------------------


@pytest.mark.offline
def test_advance_moves_ledger_pickup_to_tip(ledger) -> None:
    row = _seed(ledger, gid="G7")
    live = pickup_advance.advance_pickup_gid(ledger, row, _parsed("G8 — wire the loop"))
    assert live is not None and live.gid == "G8"
    stored = load_root(ledger, "5975")
    assert stored is not None
    assert stored.pickup_gid == "G8"
    assert stored.last_transition == Transition.ADVANCE_PICKUP.value


@pytest.mark.offline
def test_advance_is_noop_when_already_aligned(ledger) -> None:
    row = _seed(ledger, gid="G8")
    assert pickup_advance.advance_pickup_gid(ledger, row, _parsed("G8 — same")) is None
    stored = load_root(ledger, "5975")
    assert stored is not None and stored.last_transition is None


@pytest.mark.offline
def test_advance_syncs_lane_same_gid(ledger) -> None:
    """Same gid densify→implement must update ledger lane (6563 G4 class)."""
    row = _seed(ledger, gid="G4", lane="judgment")
    live = pickup_advance.advance_pickup_gid(
        ledger,
        row,
        _parsed(
            "G4 — Implement board fold · executor_lane: implement · "
            "executor=cursor/composer-2.5"
        ),
    )
    assert live is not None and live.gid == "G4" and live.lane == "implement"
    stored = load_root(ledger, "5975")
    assert stored is not None
    assert stored.pickup_gid == "G4"
    assert stored.pickup_lane == "implement"
    assert stored.pickup_executor == "cursor/composer-2.5"
    assert stored.last_transition == Transition.ADVANCE_PICKUP.value
    # Consult backoff is keyed by gid — lane-only sync must not reset it.
    assert stored.consult_attempts == row.consult_attempts


@pytest.mark.offline
def test_advance_resets_consult_backoff_for_new_gid(ledger) -> None:
    """Consult attempts are keyed ``(root, gid, role)`` — a new gid starts fresh."""
    from dataclasses import replace

    from scripts.model_manager.ui.controller.charter_runner.root_ledger import upsert_root

    row = replace(
        _seed(ledger, gid="G7"),
        consult_role="judgment_gap",
        consult_attempts=3,
        consult_next_retry=1.0,
    )
    upsert_root(ledger, row)
    assert pickup_advance.advance_pickup_gid(ledger, row, _parsed("G8 — next")) is not None
    stored = load_root(ledger, "5975")
    assert stored is not None
    assert (stored.consult_attempts, stored.consult_next_retry) == (0, None)


# ---- window sequencing -------------------------------------------------------


@pytest.mark.offline
def test_window_index_cannot_regress_when_bus_view_is_short(ledger, local_logs) -> None:
    """The 5975 defect: bus turns come back empty, ledger still remembers w13."""
    from dataclasses import replace

    from scripts.model_manager.ui.controller.charter_runner.root_ledger import upsert_root

    row = replace(_seed(ledger), last_window_id="charter-5975-w13")
    upsert_root(ledger, row)
    assert window_sequence.next_window_index("5975", [], row=row) == 14


@pytest.mark.offline
def test_window_index_uses_transcript_when_ledger_is_fresh(local_logs) -> None:
    window_log.append_admit(
        root_id="5975",
        window_index=9,
        worker_thread="6105",
        packet_path="/tmp/p.md",
        packet_text="packet",
        push_reminder="",
        dispatch_id="d1",
    )
    assert window_sequence.next_window_index("5975", [], row=None) == 10


@pytest.mark.offline
def test_window_index_advances_with_bus_pointers(local_logs) -> None:
    turns = [
        _turn(1, "WIP charter-runner window 1", '{"window": 1}'),
        _turn(2, "WIP charter-runner window 2", '{"window": 2}'),
    ]
    assert window_sequence.next_window_index("5975", turns, row=None) == 3


@pytest.mark.offline
def test_window_index_from_id_tolerates_junk() -> None:
    assert window_sequence.window_index_from_id(None) == 0
    assert window_sequence.window_index_from_id("charter-5975-consult") == 0
    assert window_sequence.window_index_from_id("charter-5975-w7") == 7


# ---- harvest release ---------------------------------------------------------


@pytest.mark.offline
def test_release_clears_wip_and_advances_pickup(ledger, local_logs) -> None:
    """``HARVEST_OK`` existed but was never applied — roots froze in ADMITTED."""
    from dataclasses import replace

    from scripts.model_manager.ui.controller.charter_runner.root_ledger import upsert_root

    row = replace(
        _seed(ledger, gid="G7"),
        status=RootStatus.ADMITTED,
        wip_window_id="charter-5975-w14",
    )
    upsert_root(ledger, row)
    assert window_sequence.release_window_on_harvest("5975", 14) is True
    stored = load_root(ledger, "5975")
    assert stored is not None
    assert stored.status == RootStatus.IDLE
    assert stored.wip_window_id is None
    assert stored.last_window_id == "charter-5975-w14"
    # pickup advance is tip-owned in kernel_tick — release leaves gid alone
    assert stored.pickup_gid == "G7"
    assert stored.last_transition == Transition.HARVEST_OK.value


@pytest.mark.offline
def test_release_is_noop_for_unseeded_root(ledger, local_logs) -> None:
    assert window_sequence.release_window_on_harvest("9999", 1) is False


@pytest.mark.offline
def test_pre_fix_wip_stub_is_cleared(ledger) -> None:
    """6091/6110 held ``charter-{root}-w`` — an index-less wip harvest cannot release."""
    from dataclasses import replace

    from scripts.model_manager.ui.controller.charter_runner.root_ledger import upsert_root

    row = replace(
        _seed(ledger), status=RootStatus.ADMITTED, wip_window_id="charter-5975-w"
    )
    upsert_root(ledger, row)
    cleared = window_sequence.clear_uncorrelatable_wip(ledger, row)
    assert cleared.wip_window_id is None
    assert cleared.status == RootStatus.IDLE
    stored = load_root(ledger, "5975")
    assert stored is not None and stored.wip_window_id is None


@pytest.mark.offline
def test_well_formed_wip_is_left_alone(ledger) -> None:
    from dataclasses import replace

    from scripts.model_manager.ui.controller.charter_runner.root_ledger import upsert_root

    row = replace(
        _seed(ledger), status=RootStatus.ADMITTED, wip_window_id="charter-5975-w14"
    )
    upsert_root(ledger, row)
    kept = window_sequence.clear_uncorrelatable_wip(ledger, row)
    assert kept.wip_window_id == "charter-5975-w14"
    assert kept.status == RootStatus.ADMITTED


# ---- kernel wiring -----------------------------------------------------------


def _env() -> EnvSnapshot:
    return EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up"},
        attendance_by_root={"5975": "attended"},
        scoreboard_pointer={},
        bus_tip_meta={"5975": {}},
    )


def _tick(tmp_path: Path, turns: list[dict[str, Any]]):
    return asyncio.run(
        kernel_tick.apply_kernel_tick_for_root(
            "5975",
            turns,
            caps=CapStore(intent_dir=tmp_path / "intent"),
            workspace_root=tmp_path,
            env=_env(),
        )
    )


@pytest.mark.offline
def test_kernel_advances_pickup_then_admits_next_window(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One tick: ledger G7 → tip G8, and the window index continues from w13."""
    from dataclasses import replace

    from scripts.model_manager.ui.controller.charter_runner.root_ledger import upsert_root

    upsert_root(ledger, replace(_seed(ledger), last_window_id="charter-5975-w13"))
    fired: list[int] = []

    async def fake_admit(*, root_id: str, window_index: int, **_kw: Any) -> bool:
        fired.append(window_index)
        return True

    monkeypatch.setattr(kernel_tick, "admit_worker_window", fake_admit)
    outcome = _tick(tmp_path, [_turn(3, "CHECKPOINT — tip", _tip("G8 — wire the loop"))])
    assert outcome.admitted is True
    assert fired == [14]
    stored = load_root(ledger, "5975")
    assert stored is not None
    assert stored.pickup_gid == "G8"
    assert stored.wip_window_id == "charter-5975-w14"


@pytest.mark.offline
def test_kernel_refuses_admit_without_gated_pickup(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5975 re-fired on a tip whose gated lane was idle — decide() cannot see that."""
    _seed(ledger)

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("must not admit without a gated pickup")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    outcome = _tick(tmp_path, [_turn(3, "CHECKPOINT — tip", _tip("CLOSEOUT — arc done"))])
    assert outcome.admitted is False
    assert outcome.skipped_reason == "no_gated_pickup"


@pytest.mark.offline
def test_kernel_refuses_queue_consult_without_gated_pickup(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a:26596 — autonomous judgment + idle tip must not QUEUE_CONSULT."""
    _seed(ledger, attendance="autonomous")
    queued: list[str] = []

    async def capture_queued(**payload: Any) -> None:
        queued.append(str(payload.get("root") or ""))

    monkeypatch.setattr(kernel_tick, "emit_consult_queued", capture_queued)

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("must not admit without a gated pickup")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    monkeypatch.setattr(kernel_tick, "admit_consult_window", refuse_admit)
    outcome = _tick(tmp_path, [_turn(3, "CHECKPOINT — tip", _tip("CLOSEOUT — arc done"))])
    assert outcome.admitted is False
    assert outcome.skipped_reason == "no_gated_pickup"
    assert queued == []


@pytest.mark.offline
def test_kernel_does_not_readmit_while_wip_is_held(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second tick before closeout must NOOP — the duplicate-admit falsifier."""
    from dataclasses import replace

    from scripts.model_manager.ui.controller.charter_runner.root_ledger import upsert_root

    upsert_root(
        ledger,
        replace(
            _seed(ledger),
            status=RootStatus.ADMITTED,
            wip_window_id="charter-5975-w14",
        ),
    )

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("must not re-admit while a window is in flight")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    outcome = _tick(tmp_path, [_turn(3, "CHECKPOINT — tip", _tip("G8 — wire the loop"))])
    assert outcome.admitted is False
    assert outcome.old_decision_label == "NOOP"


@pytest.mark.offline
def test_tip_empty_hopper_true_for_pending_executor() -> None:
    parsed = _parsed("G3 — wait · executor=pending · executor_lane: judgment")
    assert parsed.consult_pending is False
    assert (
        pickup_advance.tip_is_empty_hopper(
            parsed, has_wip=False, wip_window_id=None
        )
        is True
    )


@pytest.mark.offline
def test_tip_empty_hopper_false_when_consult_pending() -> None:
    """6237 class — CONSULT_PENDING + executor=pending must not fence QUEUE_CONSULT."""
    body = _TIP_TEMPLATE.format(
        row=(
            "G3 — R-admit dynamic predicates · CONSULT_PENDING · "
            "consult_role=r_admit · executor=pending · executor_lane: judgment"
        )
    )
    parsed = parse_checkpoint(body)
    assert parsed.consult_pending is True
    assert (
        pickup_advance.tip_is_empty_hopper(
            parsed, has_wip=False, wip_window_id=None
        )
        is False
    )


# ---- reject idempotency ------------------------------------------------------


@pytest.mark.offline
def test_reject_marker_is_body_scoped(local_logs) -> None:
    sha = window_log.checkpoint_body_sha("bad footerless body")
    assert not window_log.already_marked("5975", 4, kind="rejected", token=sha)
    window_log.mark("5975", 4, kind="rejected", token=sha)
    assert window_log.already_marked("5975", 4, kind="rejected", token=sha)
    other = window_log.checkpoint_body_sha("author reposted a fixed body")
    assert not window_log.already_marked("5975", 4, kind="rejected", token=other)
