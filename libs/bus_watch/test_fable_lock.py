"""Tests for seat mutex and ticker lease split."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from bus_watch import fable_lock as fl


@pytest.fixture()
def watch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fl, "WATCH_DIR", tmp_path)
    monkeypatch.setattr(fl, "TICKER_LOCK", tmp_path / "liaison-ticker.lock")
    return tmp_path


def test_fable_lock_path() -> None:
    assert fl.fable_lock_path("10534").name == "liaison-fable-10534.lock"


def test_ticker_lock_path() -> None:
    assert fl.ticker_lock_path("10534").name == "liaison-ticker-10534.lock"


def test_ticker_lease_does_not_block_successor(watch_dir: Path) -> None:
    """AC-2: ticker lease held; sdk successor can claim seat mutex."""
    assert fl.claim_ticker_lease("10479")["ok"]
    result = fl.claim_fable_lock("sdk:abc", hop=True, root_id="10479")
    assert result["ok"] is True


def test_ticker_cannot_write_preempt_by(watch_dir: Path) -> None:
    """AC-3: ticker holder never writes preempt_by on live sdk holder."""
    fl.claim_fable_lock("sdk:live", hop=False, root_id="10479")
    fl.claim_ticker_lease("10479")
    lock = fl.read_lock("10479")
    assert lock.get("preempt_by") is None
    assert lock.get("holder") == "sdk:live"


def test_declared_lease_45min_not_free(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-8: healthy hop at 45 min is not seat_lock_free."""
    t0 = 1_000_000.0
    monkeypatch.setattr(time, "time", lambda: t0)
    fl.claim_fable_lock("sdk:hop", hop=False, max_hop_minutes=60, root_id="10479")
    monkeypatch.setattr(time, "time", lambda: t0 + 45 * 60)
    lock = fl.read_lock("10479")
    assert fl.seat_lock_free(lock, max_hop_minutes=60, root_id="10479") is False
    other = fl.claim_fable_lock(
        "sdk:other", hop=False, max_hop_minutes=60, root_id="10479"
    )
    assert other["ok"] is False


def test_second_attended_tab_is_held_without_take_over(watch_dir: Path) -> None:
    """Two attended tabs on one root: the second is refused, not co-holding (2026-09-11 specimen)."""
    assert fl.claim_fable_lock("ide:tab-a", hop=False, root_id="10479")["ok"]
    second = fl.claim_fable_lock("ide:tab-b", hop=False, root_id="10479")
    assert second["ok"] is False
    assert second["reason"] == "held"
    assert fl.read_lock("10479").get("preempt_by") is None


def test_take_over_preempts_live_attended_holder(watch_dir: Path) -> None:
    """Operator's word (resume on another workstation) requests preempt; claim succeeds after release."""
    assert fl.claim_fable_lock("ide:tab-a", hop=False, root_id="10479")["ok"]
    requested = fl.claim_fable_lock(
        "ide:tab-b", hop=False, root_id="10479", take_over=True
    )
    assert requested["reason"] == "held_preempt_requested"
    assert fl.read_lock("10479").get("preempt_by") == "ide:tab-b"
    assert fl.release_fable_lock("ide:tab-a", root_id="10479")["ok"]
    assert fl.claim_fable_lock("ide:tab-b", hop=False, root_id="10479")["ok"] is True


def test_release_refused_from_another_process_unless_operator(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An orphaned duplicate loop (same holder, other pid) must not drop the live lease."""
    assert fl.claim_fable_lock("ide:tab-a", hop=False, root_id="10479")["ok"]
    other_pid = fl.os.getpid() + 1
    refused = fl.release_fable_lock("ide:tab-a", root_id="10479", pid=other_pid)
    assert refused["ok"] is False
    assert refused["reason"] == "not_holder_process"
    assert fl.read_lock("10479")["holder"] == "ide:tab-a"
    assert fl.release_fable_lock("ide:tab-a", root_id="10479", pid=None)["ok"]
    assert fl.read_lock("10479").get("holder") is None
    assert fl.claim_fable_lock("ide:tab-a", hop=False, root_id="10479")["ok"]
    assert fl.release_fable_lock("ide:tab-a", root_id="10479")["ok"]


def test_model_gated_refresh_increments_tick_seq(watch_dir: Path) -> None:
    """AC-9: successive refreshes strictly increase tick_seq."""
    fl.claim_fable_lock("sdk:seat", hop=False, max_hop_minutes=60, root_id="10479")
    assert fl.refresh_fable_lock(
        "sdk:seat", root_id="10479", max_hop_minutes=60, turns_seen=10
    )
    seq1 = fl.read_lock("10479")["tick_seq"]
    assert fl.refresh_fable_lock(
        "sdk:seat", root_id="10479", max_hop_minutes=60, turns_seen=11
    )
    seq2 = fl.read_lock("10479")["tick_seq"]
    assert seq2 > seq1


def test_no_utime_refresh_path() -> None:
    """AC-9: refresh path must not call os.utime."""
    source = Path(fl.__file__).read_text(encoding="utf-8")
    assert "utime" not in source


def test_night_id_reset_on_mismatch(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-10: new night_id resets hops without deleting lock file."""
    emitted: list[str] = []
    monkeypatch.setattr(
        fl,
        "emit_night_id_reset",
        lambda **kwargs: emitted.append(kwargs["new_night_id"]),
    )
    watch_dir.mkdir(parents=True, exist_ok=True)
    fl.fable_lock_path("10479").write_text(
        json.dumps(
            {
                "holder": None,
                "night_id": "2026-09-10",
                "hops": 8,
                "hops_by_night": {"2026-09-10": 8},
                "root": "10479",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fl, "current_night_id", lambda: "2026-09-11")
    result = fl.claim_fable_lock("sdk:new", hop=True, root_id="10479")
    assert result["ok"]
    lock = fl.read_lock("10479")
    assert lock["hops"] == 1
    assert lock["night_id"] == "2026-09-11"
    assert emitted == ["2026-09-11"]


def test_two_roots_increment_hops_independently(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    night = "2026-09-12"
    monkeypatch.setattr(fl, "current_night_id", lambda: night)
    fl.claim_fable_lock("sdk:a", hop=True, root_id="10479")
    fl.claim_fable_lock("sdk:b", hop=True, root_id="10534")
    assert fl.read_lock("10479")["hops"] == 1
    assert fl.read_lock("10534")["hops"] == 1
    fl.release_fable_lock("sdk:a", root_id="10479")
    fl.claim_fable_lock("sdk:a2", hop=True, root_id="10479")
    assert fl.read_lock("10479")["hops"] == 2
    assert fl.read_lock("10534")["hops"] == 1


def test_two_roots_both_hold_live_seats(watch_dir: Path) -> None:
    assert fl.claim_fable_lock("sdk:10479", hop=False, root_id="10479")["ok"]
    result = fl.claim_fable_lock("sdk:10534", hop=False, root_id="10534")
    assert result["ok"] is True
    assert result.get("reason") != "held"
    assert fl.read_lock("10479")["holder"] == "sdk:10479"
    assert fl.read_lock("10534")["holder"] == "sdk:10534"


def test_two_roots_both_hold_ticker_leases(watch_dir: Path) -> None:
    assert fl.claim_ticker_lease("10479")["ok"]
    result = fl.claim_ticker_lease("10534")
    assert result["ok"] is True
    assert fl.read_ticker_lock("10479")["holder"] == "ticker:10479"
    assert fl.read_ticker_lock("10534")["holder"] == "ticker:10534"


def test_legacy_global_ticker_file_does_not_block_other_root(watch_dir: Path) -> None:
    (watch_dir / "liaison-ticker.lock").write_text(
        json.dumps({"holder": "ticker:10479", "root": "10479"}),
        encoding="utf-8",
    )
    assert fl.claim_ticker_lease("10534")["ok"] is True
    assert fl.read_ticker_lock("10534")["holder"] == "ticker:10534"


def test_missing_per_root_file_does_not_inherit_legacy_hops(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    night = "2026-09-12"
    monkeypatch.setattr(fl, "current_night_id", lambda: night)
    (watch_dir / "liaison-fable.lock").write_text(
        json.dumps({"hops": 5, "night_id": night, "holder": None}),
        encoding="utf-8",
    )
    result = fl.claim_fable_lock("sdk:new", hop=True, root_id="10534")
    assert result["ok"]
    lock = fl.read_lock("10534")
    assert lock["hops"] == 1
