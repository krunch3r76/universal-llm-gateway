"""Hop mutex — refuse stacked glass-launch (a:38364 / a:38386 / a:38439)."""

from __future__ import annotations

import json

import pytest

from bus_watch import ide_hop_mutex as mutex_mod

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
