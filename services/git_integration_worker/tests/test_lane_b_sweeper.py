"""Tests for lane-B seat write ledger and quiescent sweeper."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.git_integration_worker.lane_b_sweeper import (
    REGISTRATION_GAPS,
    sweep_lane_b_writes,
)
from services.git_integration_worker.seat_write_ledger import SeatWriteLedger

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


@pytest.fixture
def ledger(tmp_path: Path) -> SeatWriteLedger:
    SeatWriteLedger.reset_instance()
    return SeatWriteLedger(db_path=tmp_path / "seat-write-ledger.db")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "master")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    seed = tmp_path / "README.md"
    seed.write_text("seed\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "seed")
    return tmp_path


@pytest.fixture
def patch_ledger(ledger: SeatWriteLedger, monkeypatch: pytest.MonkeyPatch) -> SeatWriteLedger:
    monkeypatch.setattr(
        "services.git_integration_worker.lane_b_sweeper.SeatWriteLedger.instance",
        lambda: ledger,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.lane_a_checkpoint.SeatWriteLedger.instance",
        lambda: ledger,
    )
    return ledger


def test_sweeper_cannot_touch_unregistered_path(
    repo: Path, patch_ledger: SeatWriteLedger
) -> None:
    """Negative: dirty path with no ledger row is never committed."""
    foreign = repo / "unregistered.py"
    foreign.write_text("x=1\n", encoding="utf-8")

    result = _run(sweep_lane_b_writes(repo, quiescence_s=0))

    assert result.paths_committed == 0
    assert "unregistered.py" in result.skipped_unregistered
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "unregistered.py" in status.stdout


def test_open_arc_not_swept(repo: Path, patch_ledger: SeatWriteLedger) -> None:
    """Negative: registered path on an open arc is not swept."""
    patch_ledger.open_arc(arc_id="arc-open", seat_id="ide-composer", source_repo=str(repo))
    target = repo / "wip.py"
    target.write_text("wip\n", encoding="utf-8")
    patch_ledger.register_paths(
        arc_id="arc-open",
        seat_id="ide-composer",
        source_repo=str(repo),
        paths=("wip.py",),
    )
    assert patch_ledger.is_arc_open(arc_id="arc-open")

    result = _run(sweep_lane_b_writes(repo, quiescence_s=0))

    assert result.paths_committed == 0
    assert "arc-open" in result.skipped_open_arc
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "wip.py" in status.stdout


def test_closed_quiescent_arc_is_swept(
    repo: Path, patch_ledger: SeatWriteLedger
) -> None:
    arc_id = "arc-done"
    patch_ledger.open_arc(arc_id=arc_id, seat_id="ide-composer", source_repo=str(repo))
    target = repo / "done.py"
    target.write_text("done\n", encoding="utf-8")
    patch_ledger.register_paths(
        arc_id=arc_id,
        seat_id="ide-composer",
        source_repo=str(repo),
        paths=("done.py",),
    )
    patch_ledger.close_arc(arc_id=arc_id)
    _backdate_touch(patch_ledger, arc_id=arc_id, path="done.py", seconds_ago=400)

    result = _run(sweep_lane_b_writes(repo, quiescence_s=300))

    assert result.paths_committed == 1
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--oneline"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "lane-b:" in log.stdout


def test_registration_gaps_named() -> None:
    assert any("Tab inline" in gap for gap in REGISTRATION_GAPS)
    assert any("External editor" in gap for gap in REGISTRATION_GAPS)
    assert not any("cursor-sdk" in gap for gap in REGISTRATION_GAPS)


def test_closed_cursor_sdk_arc_not_swept(
    repo: Path, patch_ledger: SeatWriteLedger
) -> None:
    """Defense in depth: closed cursor-sdk arc must not lane-B commit."""
    arc_id = "sdk-closed"
    patch_ledger.open_arc(arc_id=arc_id, seat_id="cursor-sdk", source_repo=str(repo))
    target = repo / "sdk_only.py"
    target.write_text("sdk\n", encoding="utf-8")
    patch_ledger.register_paths(
        arc_id=arc_id,
        seat_id="cursor-sdk",
        source_repo=str(repo),
        paths=("sdk_only.py",),
    )
    patch_ledger.close_arc(arc_id=arc_id)
    _backdate_touch(patch_ledger, arc_id=arc_id, path="sdk_only.py", seconds_ago=400)

    result = _run(sweep_lane_b_writes(repo, quiescence_s=300))

    assert result.paths_committed == 0
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "sdk_only.py" in status.stdout


def test_prune_stale_cursor_sdk_paths(
    repo: Path, patch_ledger: SeatWriteLedger
) -> None:
    """TTL prune removes aged cursor-sdk rows without lane-B commit."""
    arc_id = "sdk-stale"
    patch_ledger.register_paths(
        arc_id=arc_id,
        seat_id="cursor-sdk",
        source_repo=str(repo),
        paths=("stale.py",),
    )
    _backdate_touch(patch_ledger, arc_id=arc_id, path="stale.py", seconds_ago=800_000)
    patch_ledger.register_paths(
        arc_id="sdk-fresh",
        seat_id="cursor-sdk",
        source_repo=str(repo),
        paths=("fresh.py",),
    )

    deleted = patch_ledger.prune_stale_seat_paths(
        seat_id="cursor-sdk",
        max_age_s=604800,
    )

    assert deleted == 1
    assert patch_ledger.has_paths_for_arc(arc_id=arc_id) is False
    assert patch_ledger.has_paths_for_arc(arc_id="sdk-fresh") is True
    assert "stale.py" not in patch_ledger.registered_paths(source_repo=str(repo))
    assert "fresh.py" in patch_ledger.registered_paths(source_repo=str(repo))


def _backdate_touch(
    ledger: SeatWriteLedger, *, arc_id: str, path: str, seconds_ago: float
) -> None:
    ts = (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE seat_write_paths SET last_touch_at=? WHERE arc_id=? AND path=?",
            (ts, arc_id, path),
        )
