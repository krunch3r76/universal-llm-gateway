from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from research_dialogue._types import Snapshot
from research_dialogue.driver import (
    attribute_ledger_window,
    ledger_entries_after,
    ledger_max_id,
    probe_pids,
    run_driver,
)

pytestmark = pytest.mark.offline


def _snap(open_usd: str = "0", *, ledger_path: str = "/tmp/ledger.sqlite") -> Snapshot:
    return Snapshot(
        as_of_utc=datetime(2026, 1, 1, tzinfo=UTC),
        healthy=True,
        committed_usd=Decimal("0"),
        open_reservations_usd=Decimal(open_usd),
        window_start="w",
        ledger_instance_id="i",
        latch_active=False,
        raw={"healthy": True, "ledger_path": ledger_path},
    )


def _init_ledger(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY,
            kind TEXT,
            request_id TEXT,
            model TEXT,
            usd TEXT,
            created_at TEXT,
            window_start TEXT
        );
        """
    )
    conn.close()


def _seed_session(session_dir: Path, *, turn_count: int = 0) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "state.json").write_text(
        json.dumps({"turn_count": turn_count, "committed_S0": "0"}),
        encoding="utf-8",
    )


def test_pid_mismatch_exit_5(tmp_path: Path) -> None:
    _seed_session(tmp_path)
    code, rec = run_driver(
        "turn",
        tmp_path,
        expect_stargate_pid="1",
        expect_cloud_proxy_pid="2",
        ss_output="pid=999",
        pidfile_text="888",
        read_fn=lambda: _snap(),
        open_wait_max_s=0,
    )
    assert code == 5
    assert rec["abort"] == "pid_changed_or_restart"


def test_foreign_open_exit_6(tmp_path: Path) -> None:
    _seed_session(tmp_path)
    code, rec = run_driver(
        "turn",
        tmp_path,
        expect_stargate_pid="9",
        expect_cloud_proxy_pid="9",
        ss_output="pid=9",
        pidfile_text="9",
        read_fn=lambda: _snap("1.0"),
        open_wait_max_s=0,
    )
    assert code == 6
    assert rec["abort"] == "foreign_open_reservation"


def test_floor_inference() -> None:
    entries = [
        {"request_id": "r1", "kind": "reserve", "model": "anthropic/claude-opus-5-5", "usd": "0.50"},
        {"request_id": "r1", "kind": "commit", "model": "anthropic/claude-opus-5-5", "usd": "0.50"},
    ]
    rec, stop = attribute_ledger_window(entries, harness_billed=Decimal("0.30"))
    assert stop is None
    assert rec["finalize_branch_inferred"] == "floor (commit == reservation)"


def test_undercount_exit_4(tmp_path: Path) -> None:
    rec, stop = attribute_ledger_window(
        [
            {"request_id": "r1", "kind": "reserve", "model": "anthropic/claude-opus-5-5", "usd": "0.10"},
            {"request_id": "r1", "kind": "commit", "model": "anthropic/claude-opus-5-5", "usd": "0.05"},
        ],
        harness_billed=Decimal("0.20"),
    )
    assert stop == "ledger_undercount"
    assert rec["gap"] == "-0.15"


def test_ambiguous_attribution() -> None:
    entries = [
        {"request_id": "a", "kind": "reserve", "model": "anthropic/claude-opus-5-5", "usd": "1"},
        {"request_id": "b", "kind": "reserve", "model": "anthropic/claude-opus-5-5", "usd": "1"},
    ]
    rec, stop = attribute_ledger_window(entries, harness_billed=Decimal("0.5"))
    assert stop == "attribution_ambiguous"


def test_foreign_request_ids_recorded() -> None:
    entries = [
        {"request_id": "ours", "kind": "reserve", "model": "anthropic/claude-opus-5-5", "usd": "1"},
        {"request_id": "other", "kind": "reserve", "model": "anthropic/claude-sonnet-5", "usd": "1"},
    ]
    rec, _ = attribute_ledger_window(entries, harness_billed=None)
    assert rec["foreign_request_ids"] == ["other"]


def test_run_driver_writes_ledger_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "ledger.sqlite"
    _init_ledger(ledger)
    session = tmp_path / "session"
    _seed_session(session, turn_count=1)
    (session / "turn-01.json").write_text(
        json.dumps({"billed_usd": "0.20", "usage": {}, "stop_reason": "end_turn"}),
        encoding="utf-8",
    )
    snap = _snap(ledger_path=str(ledger))
    reads = [snap, snap, snap]

    def read_fn() -> Snapshot:
        return reads.pop(0) if reads else snap

    def fake_turn(session_dir: Path, **kwargs: object) -> tuple[int, dict]:
        conn = sqlite3.connect(ledger)
        conn.execute(
            "insert into entries (id,kind,request_id,model,usd,created_at,window_start) "
            "values (1,'reserve','r1',?,?,?,?)",
            ("anthropic/claude-opus-5-5", "0.20", "2026-01-01", "w"),
        )
        conn.execute(
            "insert into entries (id,kind,request_id,model,usd,created_at,window_start) "
            "values (2,'commit','r1',?,?,?,?)",
            ("anthropic/claude-opus-5-5", "0.20", "2026-01-01", "w"),
        )
        conn.commit()
        conn.close()
        return 0, {"turn": 1, "billed": "0.20"}

    monkeypatch.setattr("research_dialogue.driver.run_turn", fake_turn)

    code, summary = run_driver(
        "turn",
        session,
        expect_stargate_pid="1",
        expect_cloud_proxy_pid="2",
        ss_output="pid=1",
        pidfile_text="2",
        read_fn=read_fn,
        sleep_after=lambda _s: None,
        open_wait_max_s=0,
    )
    assert code == 0
    assert summary["cli"]
    assert (session / "turn-01-ledger.json").is_file()
    doc = json.loads((session / "turn-01-ledger.json").read_text())
    assert doc["ledger_commit"] == "0.20"
    assert ledger_max_id(ledger) == 2


def test_run_driver_undercount_exit_4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "ledger.sqlite"
    _init_ledger(ledger)
    session = tmp_path / "session"
    _seed_session(session, turn_count=1)
    (session / "turn-01.json").write_text(
        json.dumps({"billed_usd": "0.20", "usage": {}, "stop_reason": "end_turn"}),
        encoding="utf-8",
    )
    snap = _snap(ledger_path=str(ledger))

    def fake_turn(session_dir: Path, **kwargs: object) -> tuple[int, dict]:
        conn = sqlite3.connect(ledger)
        conn.execute(
            "insert into entries values (1,'reserve','r1','anthropic/claude-opus-5-5','0.20','t','w')"
        )
        conn.execute(
            "insert into entries values (2,'commit','r1','anthropic/claude-opus-5-5','0.05','t','w')"
        )
        conn.commit()
        conn.close()
        return 0, {"turn": 1}

    monkeypatch.setattr("research_dialogue.driver.run_turn", fake_turn)
    code, _ = run_driver(
        "turn",
        session,
        expect_stargate_pid="1",
        expect_cloud_proxy_pid="2",
        ss_output="pid=1",
        pidfile_text="2",
        read_fn=lambda: snap,
        sleep_after=lambda _s: None,
        open_wait_max_s=0,
    )
    assert code == 4
