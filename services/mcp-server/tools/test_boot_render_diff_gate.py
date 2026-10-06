"""Hermetic gates for scripts/boot-render-diff readiness wait and baseline guard.

Lives next to the other boot render tests. The script is a host entrypoint,
not a package, so it is loaded by path and never run against live cortex.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "boot-render-diff"
_loader = importlib.machinery.SourceFileLoader("boot_render_diff", str(_SCRIPT))
_spec = importlib.util.spec_from_loader("boot_render_diff", _loader)
assert _spec is not None and _spec.loader is not None
brd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(brd)


def _healthy_manifest() -> dict:
    return {
        "agent": "cursor",
        "briefing_card_sha256": "abc",
        "briefing_card_bytes": 1200,
        "artifacts": [],
        "fetches": [
            {"tool": "cortex GET /boot-audit-counters", "params": {}, "rows": 1, "bytes": 88},
            {"tool": "cortex GET /boot-todos", "params": {}, "rows": 15, "bytes": 2289},
        ],
    }


def _poison_manifest() -> dict:
    return {
        "agent": "cursor",
        "briefing_card_sha256": None,
        "briefing_card_bytes": None,
        "artifacts": [],
        "fetches": [
            {"tool": "cortex GET /boot-audit-counters", "params": {}, "rows": 1, "bytes": 99},
            {"tool": "cortex GET /boot-todos", "params": {}, "rows": 1, "bytes": 99},
        ],
    }


@pytest.fixture
def baseline_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(brd, "BASELINE_DIR", tmp_path)
    return tmp_path


def test_startup_window_not_ready_skips_without_render_or_baseline(baseline_dir, capsys, monkeypatch):
    rendered = {"called": False}

    def never_ready(method, path):
        return {"error": "cortex-api error: HTTP 404", "status_code": 404}

    clock = {"t": 0.0}

    def monotonic():
        return clock["t"]

    def sleep(seconds):
        clock["t"] += seconds

    wait = brd._wait_cortex_ready
    monkeypatch.setattr(brd, "_render_manifest", lambda agent: rendered.__setitem__("called", True))
    monkeypatch.setattr(
        brd,
        "_wait_cortex_ready",
        lambda **kwargs: wait(cx_fn=never_ready, sleep=sleep, monotonic=monotonic),
    )

    rc = brd.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped — cortex_api not ready" in out
    assert "GET /boot-audit-counters not ready within 20s" in out
    assert rendered["called"] is False
    assert list(baseline_dir.iterdir()) == []
    assert clock["t"] <= brd._READY_BUDGET_S + 1e-9


def test_healthy_render_matching_baseline_reports_no_drift(baseline_dir, capsys, monkeypatch):
    manifest = _healthy_manifest()
    text = brd._canonical_text(manifest)
    (baseline_dir / "cursor.json").write_text(text)
    monkeypatch.setattr(brd, "_render_manifest", lambda agent: manifest)
    monkeypatch.setattr(brd, "_wait_cortex_ready", lambda **kwargs: True)

    rc = brd.main(["--agent", "cursor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no drift" in out
    assert (baseline_dir / "cursor.json").read_text() == text


def test_healthy_render_rewrites_poisoned_baseline(baseline_dir, capsys, monkeypatch):
    poisoned = brd._canonical_text(_poison_manifest())
    (baseline_dir / "cursor.json").write_text(poisoned)
    healthy = _healthy_manifest()
    monkeypatch.setattr(brd, "_render_manifest", lambda agent: healthy)
    monkeypatch.setattr(brd, "_wait_cortex_ready", lambda **kwargs: True)

    rc = brd.main(["--agent", "cursor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRIFT DETECTED" in out
    assert (baseline_dir / "cursor.json").read_text() == brd._canonical_text(healthy)
    assert (baseline_dir / "cursor.json").read_text() != poisoned


@pytest.mark.parametrize("mode", ["seed_missing", "drift", "refresh"])
def test_relay_error_rows_do_not_update_baseline(baseline_dir, capsys, monkeypatch, mode):
    poison = _poison_manifest()
    prior = brd._canonical_text(_healthy_manifest())
    path = baseline_dir / "cursor.json"
    if mode != "seed_missing":
        path.write_text(prior)
    monkeypatch.setattr(brd, "_render_manifest", lambda agent: poison)
    monkeypatch.setattr(brd, "_wait_cortex_ready", lambda **kwargs: True)
    argv = ["--agent", "cursor"]
    if mode == "refresh":
        argv.append("--seed")

    rc = brd.main(argv)
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped baseline update — relay-error fetches present:" in out
    assert "cortex GET /boot-audit-counters rows=1 bytes=99" in out
    assert "cortex GET /boot-todos rows=1 bytes=99" in out
    if mode == "seed_missing":
        assert not path.exists()
    else:
        assert path.read_text() == prior


def test_probe_ready_after_n_polls_renders_within_budget(baseline_dir, capsys, monkeypatch):
    calls = {"n": 0}
    clock = {"t": 0.0}

    def probe(method, path):
        assert method == "GET"
        assert path == "/boot-audit-counters"
        calls["n"] += 1
        if calls["n"] < 3:
            return {"error": "cortex-api error: HTTP 404", "status_code": 404}
        return {"ok": True}

    def monotonic():
        return clock["t"]

    def sleep(seconds):
        clock["t"] += seconds

    manifest = _healthy_manifest()
    wait = brd._wait_cortex_ready
    monkeypatch.setattr(brd, "_render_manifest", lambda agent: manifest)
    monkeypatch.setattr(
        brd,
        "_wait_cortex_ready",
        lambda **kwargs: wait(cx_fn=probe, sleep=sleep, monotonic=monotonic),
    )

    rc = brd.main(["--agent", "cursor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "seeded baseline" in out
    assert calls["n"] == 3
    assert clock["t"] == pytest.approx(1.0)
    assert clock["t"] <= brd._READY_BUDGET_S
