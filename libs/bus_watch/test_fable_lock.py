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
    monkeypatch.setattr(fl, "FABLE_LOCK", tmp_path / "liaison-fable.lock")
    monkeypatch.setattr(fl, "TICKER_LOCK", tmp_path / "liaison-ticker.lock")
    return tmp_path


def test_ticker_lease_does_not_block_successor(watch_dir: Path) -> None:
    """AC-2: ticker lease held; sdk successor can claim seat mutex."""
    assert fl.claim_ticker_lease("10479")["ok"]
    result = fl.claim_fable_lock("sdk:abc", hop=True, root_id="10479")
    assert result["ok"] is True


def test_ticker_cannot_write_preempt_by(watch_dir: Path) -> None:
    """AC-3: ticker holder never writes preempt_by on live sdk holder."""
    fl.claim_fable_lock("sdk:live", hop=False, root_id="10479")
    fl.claim_ticker_lease("10479")
    lock = fl.read_lock()
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
    lock = fl.read_lock()
    assert fl.seat_lock_free(lock, max_hop_minutes=60) is False
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
    assert fl.read_lock().get("preempt_by") is None


def test_take_over_preempts_live_attended_holder(watch_dir: Path) -> None:
    """Operator's word (resume on another workstation) requests preempt; claim succeeds after release."""
    assert fl.claim_fable_lock("ide:tab-a", hop=False, root_id="10479")["ok"]
    requested = fl.claim_fable_lock(
        "ide:tab-b", hop=False, root_id="10479", take_over=True
    )
    assert requested["reason"] == "held_preempt_requested"
    assert fl.read_lock().get("preempt_by") == "ide:tab-b"
    assert fl.release_fable_lock("ide:tab-a")["ok"]
    assert fl.claim_fable_lock("ide:tab-b", hop=False, root_id="10479")["ok"] is True


def test_model_gated_refresh_increments_tick_seq(watch_dir: Path) -> None:
    """AC-9: successive refreshes strictly increase tick_seq."""
    fl.claim_fable_lock("sdk:seat", hop=False, max_hop_minutes=60, root_id="10479")
    assert fl.refresh_fable_lock("sdk:seat", max_hop_minutes=60, turns_seen=10)
    seq1 = fl.read_lock()["tick_seq"]
    assert fl.refresh_fable_lock("sdk:seat", max_hop_minutes=60, turns_seen=11)
    seq2 = fl.read_lock()["tick_seq"]
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
    fl.FABLE_LOCK.write_text(
        json.dumps(
            {
                "holder": None,
                "night_id": "2026-09-10",
                "hops": 8,
                "hops_by_night": {"2026-09-10": 8},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fl, "current_night_id", lambda: "2026-09-11")
    result = fl.claim_fable_lock("sdk:new", hop=True, root_id="10479")
    assert result["ok"]
    lock = fl.read_lock()
    assert lock["hops"] == 1
    assert lock["night_id"] == "2026-09-11"
    assert emitted == ["2026-09-11"]
