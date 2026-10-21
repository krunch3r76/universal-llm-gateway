"""Hop mutex — refuse stacked glass-launch (a:38364 / a:38386 / a:38439)."""

from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

from bus_watch import ide_hop_mutex as mutex_mod
from bus_watch.ide_hop import fire_ide_hop

pytestmark = pytest.mark.offline


def test_hop_mutex_refuses_second_acquire(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mutex_mod, "WATCH_DIR", tmp_path)
    first = mutex_mod.try_acquire_hop_mutex("15420", ttl_s=120.0)
    assert first["ok"] is True
    second = mutex_mod.try_acquire_hop_mutex("15420", ttl_s=120.0)
    assert second["ok"] is False
    assert second["phase"] == "hop_mutex_held"
    released = mutex_mod.release_hop_mutex("15420")
    assert released["ok"] is True and released["released"] is True
    third = mutex_mod.try_acquire_hop_mutex("15420", ttl_s=120.0)
    assert third["ok"] is True
    mutex_mod.release_hop_mutex("15420")


def test_hop_mutex_expires_after_ttl(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mutex_mod, "WATCH_DIR", tmp_path)
    t0 = 1_000_000.0
    first = mutex_mod.try_acquire_hop_mutex("15420", ttl_s=30.0, now=t0)
    assert first["ok"] is True
    path = mutex_mod.hop_mutex_path("15420")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["pid"] = 0  # dead holder
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    # Still within TTL with started_at set → refuse even if pid dead
    mid = mutex_mod.try_acquire_hop_mutex("15420", ttl_s=30.0, now=t0 + 10.0)
    assert mid["ok"] is False
    # Past TTL → acquire
    late = mutex_mod.try_acquire_hop_mutex("15420", ttl_s=30.0, now=t0 + 40.0)
    assert late["ok"] is True
    mutex_mod.release_hop_mutex("15420")


def test_window_mutex_serializes_cross_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mutex_mod, "WATCH_DIR", tmp_path)
    first = mutex_mod.try_acquire_window_mutex(
        "orion-node", "Cursor Agents", root_id="15441", ttl_s=120.0
    )
    assert first["ok"] is True
    second = mutex_mod.try_acquire_window_mutex(
        "orion-node", "Cursor Agents", root_id="15420", ttl_s=120.0
    )
    assert second["ok"] is False
    assert second["phase"] == "hop_window_mutex_held"
    mutex_mod.release_window_mutex("orion-node", "Cursor Agents")
    third = mutex_mod.try_acquire_window_mutex(
        "orion-node", "Cursor Agents", root_id="15420", ttl_s=120.0
    )
    assert third["ok"] is True
    mutex_mod.release_window_mutex("orion-node", "Cursor Agents")


def test_exclusive_acquire_race_only_one_ok(tmp_path, monkeypatch) -> None:
    """Two threads creating the same lock file — only one receives ok (a:39921)."""
    monkeypatch.setattr(mutex_mod, "WATCH_DIR", tmp_path)
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def _worker() -> None:
        barrier.wait()
        results.append(mutex_mod.try_acquire_hop_mutex("16951", ttl_s=120.0))

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5.0)
    assert len(results) == 2
    oks = [r for r in results if r.get("ok")]
    assert len(oks) == 1
    mutex_mod.release_hop_mutex("16951")


def test_overlapping_hop_second_refuses_before_seal(tmp_path, monkeypatch) -> None:
    """Concurrent hop legs: one seal, one launch; second refuses at mutex (a:39921)."""
    monkeypatch.setattr(mutex_mod, "WATCH_DIR", tmp_path)
    seal_calls = 0
    seal_lock = threading.Lock()
    hop_started = threading.Event()
    release_first = threading.Event()

    def _seal(*_a, **_k):
        nonlocal seal_calls
        with seal_lock:
            seal_calls += 1
        hop_started.set()
        release_first.wait(timeout=5.0)
        return {"ok": True, "bus_turn": 104}

    marker = (
        "Liaison IDE hop (attended register) tip_cp=104. LOAD the ide-continuity skill."
    )
    message = f"resume 16951\n\n{marker}\nNOW: test\n"

    launch_calls = 0
    launch_lock = threading.Lock()

    def _launch_counted(*_a, **_k):
        nonlocal launch_calls
        with launch_lock:
            launch_calls += 1
        return {"ok": True, "transport": "cdp_cursor_agents"}

    def _first_hop() -> None:
        assert mutex_mod.try_acquire_hop_mutex("16951", ttl_s=120.0)["ok"]
        _seal()
        with (
            patch("bus_watch.ide_hop.durable_write_text"),
            patch("bus_watch.ide_hop.session_unreachable", return_value=None),
            patch(
                "bus_watch.ide_hop.try_acquire_window_mutex",
                return_value={"ok": True},
            ),
            patch("bus_watch.ide_hop.release_window_mutex"),
            patch(
                "bus_watch.ide_hop.mark_fired",
                return_value={"root": "16951", "state": "fired"},
            ),
            patch(
                "bus_watch.ide_hop.mark_landed",
                return_value={"root": "16951", "state": "landed"},
            ),
            patch(
                "bus_watch.ide_hop.wait_for_landed_transcript",
                return_value=("landed-uuid", {"landed_via": "wait"}),
            ),
            patch(
                "bus_watch.ide_hop.launch_cursor_agents_tab",
                side_effect=_launch_counted,
            ),
        ):
            fire_ide_hop(
                message,
                root_id="16951",
                seal={"ok": True, "bus_turn": 104},
                gui_host=":8",
                hop_mutex_preheld=True,
            )
        release_first.set()

    th = threading.Thread(target=_first_hop)
    th.start()
    assert hop_started.wait(timeout=5.0)
    second = mutex_mod.try_acquire_hop_mutex("16951", ttl_s=120.0)
    release_first.set()
    th.join(timeout=10.0)
    assert seal_calls == 1
    assert launch_calls == 1
    assert second["ok"] is False
    assert second["phase"] == "hop_mutex_held"


def test_keystroke_failure_keeps_root_mutex(tmp_path, monkeypatch) -> None:
    """After launch_cursor_agents_tab fails, root mutex stays held (a:39921 AC2)."""
    monkeypatch.setattr(mutex_mod, "WATCH_DIR", tmp_path)
    assert mutex_mod.try_acquire_hop_mutex("16951", ttl_s=120.0)["ok"] is True
    marker = (
        "Liaison IDE hop (attended register) tip_cp=105. LOAD the ide-continuity skill."
    )
    message = f"resume 16951\n\n{marker}\nNOW: test\n"
    with (
        patch("bus_watch.ide_hop.durable_write_text"),
        patch("bus_watch.ide_hop.session_unreachable", return_value=None),
        patch(
            "bus_watch.ide_hop.try_acquire_window_mutex",
            return_value={"ok": True},
        ),
        patch("bus_watch.ide_hop.release_window_mutex"),
        patch(
            "bus_watch.ide_hop.launch_cursor_agents_tab",
            return_value={"ok": False, "reason": "cdp_timeout"},
        ),
    ):
        out = fire_ide_hop(
            message,
            root_id="16951",
            seal={"ok": True, "bus_turn": 105},
            gui_host=":8",
            hop_mutex_preheld=True,
        )
    assert out["phase"] == "keystroke"
    blocked = mutex_mod.try_acquire_hop_mutex("16951", ttl_s=120.0)
    assert blocked["ok"] is False
    mutex_mod.release_hop_mutex("16951")
