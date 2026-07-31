"""Tip executor must never ADMIT_WORKER when family ∉ cursor/* (a:26659).

Falsifies the 6110 G9 storm: tip ``executor=cdp/opus`` + attended ledger still
chose ``ADMIT_WORKER`` / ``cursor/grok-4.5``. Gate lives in ``kernel_tick`` after
``decide``; tip authority is ``LivePickup.executor`` from Next-pickup prose.

Stage-B: ``cdp/*`` tips positively rebind to ``QUEUE_CONSULT`` (never bare
refuse forever; never ``ADMIT_WORKER``). Non-cdp incompatible families still
surface ``skipped_reason=executor_mismatch``.
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
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.root_health import (
    AdmitResult,
    FireAttemptOutcome,
)
from scripts.model_manager.ui.controller.charter_runner.root_ledger import (
    SeedConfirm,
    seed_from_confirm,
)

_ROOT = "6110"

_TIP_TEMPLATE = """\
# CHECKPOINT — agent-bus:6110

## In-flight / WIP
_None this window._

## Next-pickup
- {row}

## Steps
1. [ ] Densify

## Frictions
_None this window._

```charter-state
{{
  "schema_version": 1,
  "status": "BLOCKED",
  "next_pickup": {{"gid": "G9", "lane": "judgment", "executor": "{footer_executor}"}},
  "wip": null,
  "consult": {{"role": null, "poll_hint": null, "from": null}},
  "revise_count": 0,
  "evidence": [],
  "window_id": "charter-6110-w25",
  "transition_id": null
}}
```

— RESUME (any seat, no command): charter root.
"""


def _tip(row: str, *, footer_executor: str = "cdp/opus") -> str:
    return _TIP_TEMPLATE.format(row=row, footer_executor=footer_executor)


async def _admit_ok(**_kw: Any) -> AdmitResult:
    return AdmitResult(True, FireAttemptOutcome.FIRED)


def _turn(n: int, subject: str, body: str = "") -> dict[str, Any]:
    return {"turn_number": n, "subject": subject, "body": body, "from_agent": "cursor"}


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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


def _seed(conn, *, gid: str = "G9", lane: str = "judgment", attendance: str = "attended"):
    return seed_from_confirm(
        conn,
        SeedConfirm(
            root_id=_ROOT,
            pickup_gid=gid,
            pickup_lane=lane,
            pickup_executor="cursor/grok-4.5",
            attendance=attendance,
            scoreboard_uri="cortex://notes/system/threads/6110-charter-scoreboard.md",
        ),
    )


def _env() -> EnvSnapshot:
    return EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up"},
        attendance_by_root={_ROOT: "attended"},
        scoreboard_pointer={},
        bus_tip_meta={_ROOT: {}},
    )


def _silence_consult_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(**_kw: Any) -> None:
        return None

    monkeypatch.setattr(kernel_tick, "emit_consult_queued", _noop)
    monkeypatch.setattr(kernel_tick, "emit_tick_transition", _noop)


def _tick(tmp_path: Path, turns: list[dict[str, Any]]):
    return asyncio.run(
        kernel_tick.apply_kernel_tick_for_root(
            _ROOT,
            turns,
            caps=CapStore(intent_dir=tmp_path / "intent"),
            workspace_root=tmp_path,
            env=_env(),
        )
    )


@pytest.mark.offline
@pytest.mark.parametrize(
    "executor,expected",
    [
        (None, True),
        ("", True),
        ("pending", True),
        ("cursor/grok-4.5", True),
        ("cursor/composer-2.5", True),
        ("cdp/opus", False),
        ("cdp/opus-5", False),
        ("anthropic/claude-opus-5", False),
    ],
)
def test_worker_substrate_compatible(executor: str | None, expected: bool) -> None:
    assert pickup_advance.worker_substrate_compatible(executor) is expected


@pytest.mark.offline
@pytest.mark.parametrize(
    "executor,expected",
    [
        (None, False),
        ("pending", False),
        ("cursor/grok-4.5", False),
        ("cdp/opus", True),
        ("cdp/opus-5", True),
        ("anthropic/claude-opus-5", False),
    ],
)
def test_tip_executor_is_cdp_family(executor: str | None, expected: bool) -> None:
    assert pickup_advance.tip_executor_is_cdp_family(executor) is expected


@pytest.mark.offline
def test_tip_cdp_opus_rebinds_to_queue_consult(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live-shaped tip executor=cdp/opus must never call admit_worker_window.

    Stage-B: positively rebind to QUEUE_CONSULT instead of bare refuse.
    """
    _seed(ledger)
    _silence_consult_telemetry(monkeypatch)

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("must not admit_worker_window on cdp/opus tip")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    row = (
        "G9 — CONSULT_PENDING · consult_role: judgment_gap · "
        "densify G2 Opus frame · executor=cdp/opus"
    )
    outcome = _tick(
        tmp_path,
        [
            _turn(
                177,
                "CHECKPOINT wave 22 — G9 w25 executor-mismatch park",
                _tip(row),
            )
        ],
    )
    assert outcome.admitted is False
    assert outcome.skipped_reason is None
    assert outcome.old_decision_label == "kernel_queue_consult"


@pytest.mark.offline
def test_post_park_second_tick_admits_consult_not_worker(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After Stage-B queue, next tick ADMIT_CONSULT — never mismatched generate."""
    _seed(ledger)
    _silence_consult_telemetry(monkeypatch)

    async def refuse_worker(**_kw: Any) -> bool:
        raise AssertionError("must not admit_worker_window on cdp tip")

    consult_fired: list[bool] = []

    async def fake_consult(**_kw: Any) -> AdmitResult:
        consult_fired.append(True)
        return AdmitResult(True, FireAttemptOutcome.FIRED)

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_worker)
    monkeypatch.setattr(kernel_tick, "admit_consult_window", fake_consult)
    body = _tip(
        "G9 — CONSULT_PENDING · consult_role: judgment_gap · "
        "densify · executor=cdp/opus"
    )
    turns = [
        _turn(177, "CHECKPOINT wave 22 — G9 w25 executor-mismatch park", body)
    ]
    first = _tick(tmp_path, turns)
    second = _tick(tmp_path, turns)
    assert first.old_decision_label == "kernel_queue_consult"
    assert first.admitted is False
    assert second.old_decision_label == "kernel_admit_consult"
    assert second.admitted is True
    assert consult_fired == [True]


@pytest.mark.offline
def test_compatible_cursor_executor_still_admits(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tip executor=cursor/grok-4.5 must not false-positive the refuse gate."""
    _seed(ledger)
    monkeypatch.setattr(kernel_tick, "admit_worker_window", _admit_ok)
    outcome = _tick(
        tmp_path,
        [
            _turn(
                3,
                "CHECKPOINT — tip",
                _tip(
                    "G9 — continue · executor=cursor/grok-4.5",
                    footer_executor="cursor/grok-4.5",
                ),
            )
        ],
    )
    assert outcome.admitted is True
    assert outcome.skipped_reason is None


@pytest.mark.offline
def test_pending_or_absent_executor_still_admits(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent tip executor leaves attended→generate open (no tip authority)."""
    _seed(ledger)
    monkeypatch.setattr(kernel_tick, "admit_worker_window", _admit_ok)
    outcome = _tick(
        tmp_path,
        [_turn(3, "CHECKPOINT — tip", _tip("G9 — continue slice", footer_executor="pending"))],
    )
    assert outcome.admitted is True


@pytest.mark.offline
def test_conveyor_footer_pending_prose_cdp_rebinds_consult(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """t188-shaped: footer executor=pending but Next-pickup prose still cdp/opus."""
    _seed(ledger)
    _silence_consult_telemetry(monkeypatch)

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("prose LivePickup must win over footer pending")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    outcome = _tick(
        tmp_path,
        [
            _turn(
                188,
                "CHECKPOINT — conveyor tip",
                _tip(
                    "G9 — CONSULT_PENDING · consult_role: judgment_gap · "
                    "densify G2 Opus frame · executor=cdp/opus",
                    footer_executor="pending",
                ),
            )
        ],
    )
    assert outcome.old_decision_label == "kernel_queue_consult"
    assert outcome.admitted is False


@pytest.mark.offline
def test_worker_shaped_densify_cdp_refuses_executor_mismatch(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker-shaped G3 densify + cdp/* must not queue Architecture consult (6489)."""
    _seed(ledger)

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("must not admit_worker_window on worker-shaped cdp tip")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    outcome = _tick(
        tmp_path,
        [
            _turn(
                19,
                "CHECKPOINT — G3 densify cdp/fable false-admit guard",
                _tip(
                    "G3 — densify dense spec · executor=cdp/fable",
                    footer_executor="cdp/fable",
                ),
            )
        ],
    )
    assert outcome.skipped_reason == "executor_mismatch"
    assert outcome.old_decision_label == "kernel_executor_mismatch"
    assert outcome.admitted is False


@pytest.mark.offline
def test_non_cdp_incompatible_still_bare_mismatch(
    ledger, local_logs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """anthropic/* tip stays bare executor_mismatch (Stage-B is cdp-family only)."""
    _seed(ledger)

    async def refuse_admit(**_kw: Any) -> bool:
        raise AssertionError("must not admit_worker_window on anthropic tip")

    monkeypatch.setattr(kernel_tick, "admit_worker_window", refuse_admit)
    outcome = _tick(
        tmp_path,
        [
            _turn(
                3,
                "CHECKPOINT — tip",
                _tip(
                    "G9 — densify · executor=anthropic/claude-opus-5",
                    footer_executor="anthropic/claude-opus-5",
                ),
            )
        ],
    )
    assert outcome.skipped_reason == "executor_mismatch"
    assert outcome.old_decision_label == "kernel_executor_mismatch"
    assert outcome.admitted is False
