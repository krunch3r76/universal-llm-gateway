"""Offline tests for browser_governance R1 census and warn-only preflight."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from browser_governance.census import (
    census_interval_s,
    check_ephemeral_admission_preflight,
    collect_local_browser_census,
    run_census_and_emit,
    run_census_throttled,
)


def _write_proc_tree(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "meminfo").write_text(
        "MemTotal:       32768000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "SwapTotal:       4194304 kB\n"
        "SwapFree:        2097152 kB\n",
        encoding="utf-8",
    )

    def _pid(
        name: str,
        *,
        cmdline: bytes,
        rss_kb: int,
        pss_kb: int | None,
        threads: int = 1,
    ) -> None:
        pdir = root / name
        pdir.mkdir()
        (pdir / "cmdline").write_bytes(cmdline)
        (pdir / "status").write_text(
            f"Name:\t{name}\nVmRSS:\t{rss_kb} kB\nThreads:\t{threads}\n",
            encoding="utf-8",
        )
        if pss_kb is not None:
            (pdir / "smaps_rollup").write_text(
                f"Rss:\t{rss_kb} kB\nPss:\t{pss_kb} kB\n",
                encoding="utf-8",
            )

    _pid(
        "1001",
        cmdline=(
            b"/opt/google/chrome\x00--remote-debugging-port=9338\x00"
            b"--user-data-dir=/home/u/.gateway/profile-a\x00"
        ),
        rss_kb=512000,
        pss_kb=400000,
    )
    _pid(
        "1002",
        cmdline=(
            b"/opt/google/chrome\x00--type=renderer\x00"
            b"--user-data-dir=/home/u/.gateway/profile-a\x00"
        ),
        rss_kb=256000,
        pss_kb=None,
    )
    _pid(
        "1003",
        cmdline=(
            b"/usr/bin/Xvfb\x00:8\x00-screen\x000\x001280x720x24\x00"
        ),
        rss_kb=64000,
        pss_kb=32000,
    )
    _pid(
        "1004",
        cmdline=(
            b"/usr/share/cursor/cursor\x00--user-data-dir=/home/u/.config/Cursor\x00"
        ),
        rss_kb=128000,
        pss_kb=96000,
    )


def test_collect_local_browser_census_synthetic_proc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = tmp_path / "proc"
    _write_proc_tree(proc_root)
    monkeypatch.setattr(
        "browser_governance.census._probe_cdp_json_list",
        lambda _host, port, timeout=0.8: (port == 9338, 3, 2)
        if port == 9338
        else (False, 0, 0),
    )

    census = collect_local_browser_census(
        host="test",
        proc_root=proc_root,
        disk_path=str(tmp_path),
        cdp_ports=[9338, 9339],
    )

    assert census["mem_available_mib"] == pytest.approx(8192000 / 1024.0)
    assert census["swap_free_mib"] == pytest.approx(2097152 / 1024.0)
    assert census["swap_total_mib"] == pytest.approx(4194304 / 1024.0)
    assert census["disk_free_gib"] is not None
    assert census["inode_free_pct"] is not None
    assert census["cdp_ports_live"] == 1
    assert census["cdp_targets_total"] == 3
    assert census["cdp_page_targets"] == 2
    assert census["profile_count"] == 2
    assert census["xvfb_rss_mib"]["8"] == pytest.approx(32000 / 1024.0)
    assert census["total_pss_mib"] is not None
    assert census["total_rss_mib"] is not None


def test_pss_fallback_to_rss_when_smaps_unreadable(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    (proc_root / "meminfo").write_text("MemAvailable: 1024 kB\n", encoding="utf-8")
    pdir = proc_root / "2000"
    pdir.mkdir()
    (pdir / "cmdline").write_bytes(
        b"/opt/chrome\x00--user-data-dir=/tmp/p\x00--remote-debugging-port=9338\x00"
    )
    (pdir / "status").write_text("VmRSS:\t2048 kB\n", encoding="utf-8")

    census = collect_local_browser_census(
        proc_root=proc_root,
        disk_path=str(tmp_path),
        cdp_ports=[],
    )
    assert census["total_pss_mib"] is None
    assert census["total_rss_mib"] == pytest.approx(2.0)


def test_cdp_unreachable_port_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.error
    import urllib.request

    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    (proc_root / "meminfo").write_text("", encoding="utf-8")

    def _fail(*_a: Any, **_k: Any) -> Any:
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(urllib.request, "urlopen", _fail)
    census = collect_local_browser_census(
        proc_root=proc_root,
        disk_path=str(tmp_path),
        cdp_ports=[9338],
    )
    assert census["cdp_ports_live"] == 0


def test_run_census_and_emit_disabled_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "0")
    assert run_census_and_emit() is None


def test_run_census_and_emit_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "1")
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    (proc_root / "meminfo").write_text("MemAvailable: 4096 kB\n", encoding="utf-8")
    monkeypatch.setattr(
        "browser_governance.census.collect_local_browser_census",
        lambda **_: {
            "host": "io",
            "mem_available_mib": 4.0,
            "swap_free_mib": None,
            "swap_total_mib": None,
            "disk_free_gib": 100.0,
            "inode_free_pct": 99.0,
            "total_pss_mib": 1.0,
            "total_rss_mib": 2.0,
            "cdp_ports_live": 0,
            "cdp_page_targets": 0,
            "profile_count": 0,
            "details": {},
        },
    )
    emitted: list[Any] = []
    monkeypatch.setattr(
        "browser_governance.census.emit_browser_event",
        lambda event, **_: emitted.append(event) or True,
    )
    out = run_census_and_emit(source="test")
    assert out is not None
    assert emitted
    assert emitted[0].signal == "browser.resource.census"


def test_admission_preflight_off_skips_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "0")
    called = False

    def _collect(**_: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr("browser_governance.census.collect_local_browser_census", _collect)
    result = check_ephemeral_admission_preflight(caller="test")
    assert result == {"allowed": True, "mode": "off", "warnings": []}
    assert called is False


def test_admission_preflight_warn_always_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "1")
    monkeypatch.setenv("ULG_BROWSER_ADMISSION_PREFLIGHT", "warn")
    monkeypatch.setenv("ULG_BROWSER_WARN_MEM_MIB", "50")
    monkeypatch.setenv("ULG_BROWSER_WARN_SWAP_MIB", "50")
    monkeypatch.setenv("ULG_BROWSER_WARN_DISK_GIB", "50")
    snapshot = {
        "mem_available_mib": 100.0,
        "swap_free_mib": 100.0,
        "disk_free_gib": 100.0,
    }
    result = check_ephemeral_admission_preflight(caller="x", snapshot=snapshot)
    assert result["allowed"] is True
    assert result["mode"] == "warn"
    assert result["warnings"] == []


def test_admission_preflight_emits_on_low_mem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "1")
    monkeypatch.setenv("ULG_BROWSER_ADMISSION_PREFLIGHT", "warn")
    monkeypatch.setenv("ULG_BROWSER_WARN_MEM_MIB", "5000")
    emitted: list[Any] = []
    monkeypatch.setattr(
        "browser_governance.census.emit_browser_event",
        lambda event, **_: emitted.append(event) or True,
    )
    result = check_ephemeral_admission_preflight(
        caller="cdp_registry.ephemeral",
        snapshot={"mem_available_mib": 100.0, "swap_free_mib": 9000, "disk_free_gib": 9000},
    )
    assert result["allowed"] is True
    assert result["warnings"]
    assert emitted
    assert emitted[0].signal == "browser.admission.warn"


def test_registry_hygiene_loop_calls_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.registry_hygiene_loop import run_hygiene_once

    calls: list[str] = []

    def _census(**kwargs: Any) -> None:
        calls.append(kwargs.get("source", ""))

    monkeypatch.setattr("browser_governance.census.run_census_throttled", _census)
    _stub_hygiene(monkeypatch)
    run_hygiene_once()
    assert calls == ["cdp_ask"]


def test_lifecycle_calls_admission_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_bundles.cdp_registry import lifecycle

    calls: list[str] = []

    def _preflight(**kwargs: Any) -> dict[str, Any]:
        calls.append(str(kwargs.get("caller")))
        return {"allowed": True, "mode": "off", "warnings": []}

    monkeypatch.setattr(
        "browser_governance.census.check_ephemeral_admission_preflight",
        _preflight,
    )
    row = {
        "registration_id": "reg-test",
        "port": 9340,
        "profile": "/tmp/p",
        "profile_suffix": "reg-test",
        "display": "8",
        "account_id": "default",
        "status": "active",
        "holder": "test-holder",
    }

    monkeypatch.setattr(
        lifecycle,
        "activate_allocating_row",
        lambda *_a, **_k: dict(row),
    )
    monkeypatch.setattr(
        lifecycle,
        "_events",
        MagicMock(emit=lambda *_a, **_k: None, cdp_port_registered=lambda *_a, **_k: object()),
    )

    lifecycle._finish_reserved_launch(
        row,
        launch=False,
        launch_fn=lambda *_a, **_k: 1,
    )
    assert calls == ["cdp_registry.ephemeral"]


def test_census_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ULG_BROWSER_CENSUS_INTERVAL_S", "60")
    monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "1")
    import browser_governance.census as census_mod

    census_mod._last_census_monotonic = 0.0
    calls = 0

    def _emit(**_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"host": "io"}

    monkeypatch.setattr(census_mod, "run_census_and_emit", _emit)
    assert run_census_throttled() is not None
    assert run_census_throttled() is None
    assert calls == 1
    census_mod._last_census_monotonic = time.monotonic() - 120
    assert run_census_throttled() is not None
    assert calls == 2


def test_census_interval_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ULG_BROWSER_CENSUS_INTERVAL_S", "30")
    assert census_interval_s() == 30.0
    monkeypatch.setenv("ULG_BROWSER_CENSUS_INTERVAL_S", "bad")
    assert census_interval_s() == 60.0


def _stub_hygiene(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_bundles.cdp_registry import dormant_drain

    monkeypatch.setattr(
        dormant_drain,
        "drain_live_hosts_to_dormant",
        lambda **_: dormant_drain.DrainResult(dormant=[], released=[], protected={}),
    )
    monkeypatch.setattr("claude_bundles.cdp_registry.reclaim_dormant_rows", lambda: [])
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.hygiene_reclaim_extended",
        lambda: type("R", (), {"reclaimed_ports": [], "removed_profiles": []})(),
    )
    monkeypatch.setattr(
        "claude_bundles.cse_session_obligations.sweep_wake_owed_ttl",
        lambda **_: [],
    )


def _load_status_module():
    script = Path(__file__).resolve().parents[3] / "scripts/web-automation-substrate-status.py"
    spec = importlib.util.spec_from_file_location("web_automation_substrate_status", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_web_automation_status_includes_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_mod = _load_status_module()

    monkeypatch.setattr(status_mod, "_load_lanes", lambda: {})
    monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "1")
    monkeypatch.setattr(
        "browser_governance.census.collect_local_browser_census",
        lambda **_: {"host": "io", "profile_count": 0},
    )
    report = status_mod.build_report()
    assert report["resource_census"]["host"] == "io"

    monkeypatch.setenv("ULG_BROWSER_CENSUS_ENABLED", "0")
    report_off = status_mod.build_report()
    assert report_off["resource_census"] == {"enabled": False}
