"""--emit-only reads the persisted digest snapshot and does not rebuild."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline


def _load_tick():
    path = Path(__file__).resolve().parents[2] / "scripts" / "liaison-tick.py"
    spec = importlib.util.spec_from_file_location("liaison_tick_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_emit_only_does_not_rebuild_or_touch_state(tmp_path, monkeypatch, capsys) -> None:
    mod = _load_tick()
    monkeypatch.setattr(mod, "_WATCH_DIR", tmp_path)

    def _boom(*_a, **_k):
        raise AssertionError("side effect")

    monkeypatch.setattr(mod, "ensure_loop_tape", _boom)
    monkeypatch.setattr(mod, "build_digest", _boom)
    monkeypatch.setattr(mod, "save_state", _boom)
    monkeypatch.setattr(mod, "update_state", _boom)

    state = tmp_path / "liaison-42.tick.json"
    state.write_bytes(b'{"register":"autonomous"}\n')
    before = state.read_bytes()
    payload = {"ts": "fixed", "root": {"id": "42"}}
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    snap = tmp_path / "liaison-42.digest-full.json"
    snap.write_bytes(blob)

    monkeypatch.setattr(
        "sys.argv",
        ["liaison-tick.py", "--root", "42", "--once", "--emit-only"],
    )
    assert mod.main() == 0
    assert state.read_bytes() == before
    printed = capsys.readouterr().out.strip()
    assert hashlib.sha256(printed.encode()).hexdigest() == hashlib.sha256(blob).hexdigest()


def test_emit_only_missing_snapshot_is_nonmutating(tmp_path, monkeypatch, capsys) -> None:
    mod = _load_tick()
    monkeypatch.setattr(mod, "_WATCH_DIR", tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        ["liaison-tick.py", "--root", "42", "--once", "--emit-only"],
    )
    assert mod.main() == 2
    err = json.loads(capsys.readouterr().out)
    assert err["error"] == "digest_full_snapshot_missing"
