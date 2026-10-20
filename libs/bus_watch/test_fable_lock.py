"""Tests for seat mutex and ticker lease split."""

from __future__ import annotations

import hashlib
import json
import os
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


def test_loop_flock_path() -> None:
    assert fl.loop_flock_path("10534").name == "liaison-loop-10534.flock"


def test_acquire_root_loop_flock_single_instance(watch_dir: Path) -> None:
    ok1, fd1, meta1 = fl.acquire_root_loop_flock("10534")
    assert ok1 is True
    assert fd1 is not None
    assert meta1["root"] == "10534"
    assert meta1["pid"] == os.getpid()

    ok2, fd2, meta2 = fl.acquire_root_loop_flock("10534")
    assert ok2 is False
    assert fd2 is None
    assert meta2.get("pid") == os.getpid()

    ok3, fd3, _meta3 = fl.acquire_root_loop_flock("10534")
    assert ok3 is False
    assert fd3 is None

    fl.release_root_loop_flock(fd1)
    ok4, fd4, meta4 = fl.acquire_root_loop_flock("10534")
    assert ok4 is True
    assert fd4 is not None
    assert meta4["pid"] == os.getpid()
    fl.release_root_loop_flock(fd4)


def test_acquire_root_loop_flock_subprocess_refusal(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    import sys

    ok, fd, _ = fl.acquire_root_loop_flock("10534")
    assert ok is True
    try:
        libs = Path(fl.__file__).resolve().parents[1]
        wd = str(watch_dir)
        script = (
            f"import sys; from pathlib import Path; from bus_watch import fable_lock as fl; "
            f"fl.WATCH_DIR = Path({wd!r}); "
            "ok, fd, meta = fl.acquire_root_loop_flock('10534'); "
            "sys.exit(0 if not ok else 1)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(fl.__file__).resolve().parents[2]),
            env={**os.environ, "PYTHONPATH": str(libs)},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0, proc.stderr
    finally:
        fl.release_root_loop_flock(fd)


def test_refresh_fable_lock_require_pid(watch_dir: Path) -> None:
    fl.claim_fable_lock("ide:tab", hop=False, root_id="10479")
    lock = fl.read_lock("10479")
    assert lock["holder_pid"] == os.getpid()
    assert fl.refresh_fable_lock("ide:tab", root_id="10479", require_pid=os.getpid())
    assert fl.refresh_fable_lock("ide:tab", root_id="10479", require_pid=os.getpid() + 1) is False
    assert fl.refresh_fable_lock("ide:tab", root_id="10479") is True


def test_ticker_lock_path() -> None:
    assert fl.ticker_lock_path("10534").name == "liaison-ticker-10534.lock"


def test_ticker_lease_refuses_while_holder_pid_alive(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    holder_pid = 424242
    monkeypatch.setattr(fl.os, "getpid", lambda: holder_pid)
    assert fl.claim_ticker_lease("10534")["ok"]
    monkeypatch.setattr(fl.os, "getpid", lambda: holder_pid + 1)

    def _alive(pid: int) -> bool:
        return pid == holder_pid

    monkeypatch.setattr(fl, "_holder_pid_alive", _alive)
    second = fl.claim_ticker_lease("10534")
    assert second["ok"] is False
    assert second["reason"] == "held"


def test_ticker_lease_reclaims_after_holder_exit(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dead_pid = 424242
    monkeypatch.setattr(fl.os, "getpid", lambda: dead_pid)
    assert fl.claim_ticker_lease("10534")["ok"]
    monkeypatch.setattr(fl, "_holder_pid_alive", lambda _pid: False)
    monkeypatch.setattr(fl.os, "getpid", lambda: dead_pid + 1)
    again = fl.claim_ticker_lease("10534")
    assert again["ok"] is True
    lock = fl.read_ticker_lock("10534")
    assert lock["holder_pid"] == dead_pid + 1


def test_ticker_lease_release_clears_on_matching_pid(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = 515151
    monkeypatch.setattr(fl.os, "getpid", lambda: pid)
    claim = fl.claim_ticker_lease("10534")
    assert claim["lock"]["holder_pid"] == pid
    assert fl.release_ticker_lease("10534")["ok"]
    assert fl.read_ticker_lock("10534").get("holder") is None


def test_ticker_lease_claim_does_not_touch_tick_json(watch_dir: Path) -> None:
    tick = watch_dir / "liaison-10534.tick.json"
    tick.write_text('{"register":"autonomous","ticks":1}\n', encoding="utf-8")
    digest = hashlib.sha256(tick.read_bytes()).hexdigest()
    assert fl.claim_ticker_lease("10534")["ok"]
    assert fl.release_ticker_lease("10534")["ok"]
    assert hashlib.sha256(tick.read_bytes()).hexdigest() == digest


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
    """``ide:`` hop resume transfers the lock immediately; departing tab does not poll ``preempt_by``."""
    assert fl.claim_fable_lock("ide:tab-a", hop=False, root_id="10479")["ok"]
    requested = fl.claim_fable_lock(
        "ide:tab-b", hop=False, root_id="10479", take_over=True
    )
    assert requested["ok"] is True
    assert fl.read_lock("10479").get("holder") == "ide:tab-b"
    assert fl.read_lock("10479").get("preempt_by") is None


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


def test_refresh_ticker_lease_requires_holder_pid(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = 616161
    monkeypatch.setattr(fl.os, "getpid", lambda: pid)
    assert fl.claim_ticker_lease("10534")["ok"]
    monkeypatch.setattr(fl.os, "getpid", lambda: pid + 1)
    assert fl.refresh_ticker_lease("10534") is False


def test_release_ticker_lease_wrong_pid_no_clear(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fl.ticker_lock_path("10534").write_text(
        json.dumps({"holder": "ticker:10534", "root": "10534"}),
        encoding="utf-8",
    )
    refused = fl.release_ticker_lease("10534")
    assert refused["ok"] is False
    assert refused["reason"] == "not_holder_process"
    assert fl.release_ticker_lease("10534", pid=None)["ok"]


def test_claim_refuses_legacy_pidless_lease_when_loop_alive(
    watch_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = tmp_path / "proc"
    foreign = proc_root / "424242"
    foreign.mkdir(parents=True)
    (foreign / "cmdline").write_bytes(
        b"python\x00scripts/liaison-tick.py\x00--root\x0010534\x00"
        b"--loop\x00--spawn-on-wake\x00"
    )
    monkeypatch.setenv("LIAISON_PROC_ROOT", str(proc_root))
    fl.ticker_lock_path("10534").write_text(
        json.dumps({"holder": "ticker:10534", "root": "10534"}),
        encoding="utf-8",
    )
    assert fl.claim_ticker_lease("10534")["ok"] is False


def test_claim_reclaims_stale_legacy_pidless_lease(
    watch_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    monkeypatch.setenv("LIAISON_PROC_ROOT", str(proc_root))
    path = fl.ticker_lock_path("10534")
    path.write_text(
        json.dumps({"holder": "ticker:10534", "root": "10534"}),
        encoding="utf-8",
    )
    import os

    os.utime(path, (time.time() - 60, time.time() - 60))
    lock = fl.read_ticker_lock("10534")
    assert 0 < float(lock["age_s"]) < fl.LOCK_STALE_S
    assert fl.claim_ticker_lease("10534")["ok"] is True


def test_ticker_lease_age_zero_not_stale(
    watch_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = tmp_path / "proc"
    foreign = proc_root / "525252"
    foreign.mkdir(parents=True)
    (foreign / "cmdline").write_bytes(
        b"python\x00scripts/liaison-tick.py\x00--root\x0010534\x00"
        b"--loop\x00--spawn-on-wake\x00"
    )
    monkeypatch.setenv("LIAISON_PROC_ROOT", str(proc_root))
    lock = {
        "holder": "ticker:10534",
        "age_s": 0.0,
        "root": "10534",
    }
    assert fl.ticker_lease_live(lock, root_id="10534") is True
    fl.ticker_lock_path("10534").write_text(json.dumps(lock), encoding="utf-8")
    assert fl.claim_ticker_lease("10534")["ok"] is False


def test_ticker_lease_refuses_real_dead_holder_pid_reclaim(
    watch_dir: Path,
) -> None:
    import subprocess

    child = subprocess.Popen(
        ["sleep", "30"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        fl.ticker_lock_path("10534").write_text(
            json.dumps(
                {
                    "holder": "ticker:10534",
                    "holder_pid": child.pid,
                    "root": "10534",
                }
            ),
            encoding="utf-8",
        )
        from bus_watch.go_under import ticker_alive

        assert fl.claim_ticker_lease("10534")["ok"] is False
        assert ticker_alive("10534") is True
        child.terminate()
        child.wait(timeout=5)
        assert fl.ticker_lease_live(root_id="10534") is False
        assert ticker_alive("10534") is False
        assert fl.claim_ticker_lease("10534")["ok"] is True
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_claim_reclaims_stale_pidless_when_only_self_in_proc(
    watch_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = tmp_path / "proc"
    self_dir = proc_root / str(os.getpid())
    self_dir.mkdir(parents=True)
    (self_dir / "cmdline").write_bytes(
        b"python\x00scripts/liaison-tick.py\x00--root\x0010534\x00"
        b"--loop\x00--spawn-on-wake\x00"
    )
    monkeypatch.setenv("LIAISON_PROC_ROOT", str(proc_root))
    path = fl.ticker_lock_path("10534")
    path.write_text(
        json.dumps({"holder": "ticker:10534", "root": "10534"}),
        encoding="utf-8",
    )
    os.utime(path, (time.time() - 60, time.time() - 60))
    lock = fl.read_ticker_lock("10534")
    assert 0 < float(lock["age_s"]) < fl.LOCK_STALE_S
    assert fl.claim_ticker_lease("10534")["ok"] is True


def test_legacy_scan_detects_metronome_cmdline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = tmp_path / "proc"
    foreign = proc_root / "626262"
    foreign.mkdir(parents=True)
    (foreign / "cmdline").write_bytes(
        b"python\x00scripts/metronome.py\x00--root\x0010534\x00"
        b"--loop\x00--spawn-on-wake\x00"
    )
    monkeypatch.setenv("LIAISON_PROC_ROOT", str(proc_root))
    assert fl._legacy_spawn_loop_alive("10534") is True


def test_cmdline_root_rejects_prefix_id() -> None:
    cmd = "python scripts/liaison-tick.py --root 61640 --loop --spawn-on-wake"
    assert fl._cmdline_matches_root(cmd, "6164") is False
    assert fl._cmdline_matches_root(cmd, "61640") is True


def test_ticker_lease_two_claimers_loser_refresh_false(
    watch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    winner = 717171
    monkeypatch.setattr(fl.os, "getpid", lambda: winner)
    assert fl.claim_ticker_lease("10534")["ok"]
    monkeypatch.setattr(fl.os, "getpid", lambda: winner + 1)
    monkeypatch.setattr(fl, "_holder_pid_alive", lambda pid: pid == winner)
    assert fl.claim_ticker_lease("10534")["ok"] is False
    assert fl.refresh_ticker_lease("10534") is False
    assert fl.release_ticker_lease("10534")["ok"] is False


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
