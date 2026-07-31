"""Shadow kernel — ledger path stability and starve signal (D7)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from libs.charter_runner_store.db import charter_runner_data_dir, default_ledger_path
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.kernel import (
    SHADOW_LEDGER_STARVE_ROOT,
    SHADOW_STARVE_CLASS,
    backfill_shadow_classifications,
    record_shadow_pass,
)


def _empty_env() -> EnvSnapshot:
    return EnvSnapshot(
        giw_holder_lease={"held": False, "holder": None, "residue": None},
        propagation_residue={"kind": None, "detail": None},
        in_flight_windows=[],
        satellite_health={"cdp": "up", "project_ask": "up"},
        attendance_by_root={},
        scoreboard_pointer={},
        bus_tip_meta={},
    )


@pytest.mark.offline
def test_charter_runner_data_dir_uses_operator_home_under_dispatch_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dispatch_home = tmp_path / "cursor-dispatch-homes" / "auto-test-home"
    dispatch_home.mkdir(parents=True)
    operator_home = tmp_path / "operator"
    operator_home.mkdir()
    monkeypatch.setenv("HOME", str(dispatch_home))
    monkeypatch.setenv("CHARTER_RUNNER_OPERATOR_HOME", str(operator_home))
    assert (
        charter_runner_data_dir()
        == operator_home / ".local" / "share" / "charter-runner"
    )
    assert default_ledger_path() == (
        operator_home / ".local" / "share" / "charter-runner" / "root-ledger.sqlite"
    )


@pytest.mark.offline
def test_record_shadow_pass_starves_when_ledger_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "charter-runner"
    data_dir.mkdir()
    ledger = data_dir / "root-ledger.sqlite"
    shadow = data_dir / "shadow-diff.sqlite"
    conn = sqlite3.connect(str(ledger))
    conn.execute(
        """
        CREATE TABLE root_ledger (
          root_id TEXT PRIMARY KEY,
          schema_version INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL,
          pickup_gid TEXT,
          pickup_lane TEXT,
          pickup_executor TEXT,
          wip_window_id TEXT,
          revise_count INTEGER NOT NULL DEFAULT 0,
          consult_role TEXT,
          consult_attempts INTEGER NOT NULL DEFAULT 0,
          consult_next_retry REAL,
          consult_poll_from TEXT,
          harvest_deadline REAL,
          attendance TEXT NOT NULL,
          scoreboard_uri TEXT NOT NULL,
          last_window_id TEXT,
          last_transition TEXT,
          last_error TEXT,
          env_facts_json TEXT,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data_dir))
    result = record_shadow_pass(
        {"5975": "window_in_flight", "5993": "eligible"},
        env=_empty_env(),
        db_path=shadow,
    )

    assert result.starved is True
    assert result.starve_reason == "ledger_empty"
    assert result.bus_roots == 2
    assert len(result.rows) == 1
    assert result.rows[0]["root"] == SHADOW_LEDGER_STARVE_ROOT
    assert result.rows[0]["classification"] == SHADOW_STARVE_CLASS

    conn = sqlite3.connect(str(shadow))
    row = conn.execute(
        "SELECT root, classification FROM shadow_diff ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row == (SHADOW_LEDGER_STARVE_ROOT, SHADOW_STARVE_CLASS)


@pytest.mark.offline
def test_backfill_reclassifies_all_non_starve_rows(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow-diff.sqlite"
    conn = sqlite3.connect(str(shadow))
    conn.execute(
        """
        CREATE TABLE shadow_diff (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts REAL NOT NULL,
          root TEXT NOT NULL,
          old_decision TEXT NOT NULL,
          kernel_transition TEXT NOT NULL,
          classification TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO shadow_diff
          (ts, root, old_decision, kernel_transition, classification)
        VALUES (1.0, '5975', 'window_in_flight', 'NOOP', 'old-correct')
        """
    )
    conn.execute(
        """
        INSERT INTO shadow_diff
          (ts, root, old_decision, kernel_transition, classification)
        VALUES (2.0, ?, '', '', ?)
        """,
        (SHADOW_LEDGER_STARVE_ROOT, SHADOW_STARVE_CLASS),
    )
    conn.commit()
    conn.close()

    updated = backfill_shadow_classifications(db_path=shadow)
    assert updated == 1

    conn = sqlite3.connect(str(shadow))
    rows = conn.execute(
        "SELECT root, classification FROM shadow_diff ORDER BY id"
    ).fetchall()
    conn.close()
    assert rows[0] == ("5975", "agree")
    assert rows[1] == (SHADOW_LEDGER_STARVE_ROOT, SHADOW_STARVE_CLASS)
