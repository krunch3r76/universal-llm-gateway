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


class _StopLoopError(Exception):
    pass


def test_loop_persists_snapshot_matching_projection(tmp_path, monkeypatch, capsys) -> None:
    """Falsifier: --loop writes the snapshot before the projection (22355#2)."""
    mod = _load_tick()
    monkeypatch.setattr(mod, "_WATCH_DIR", tmp_path)
    digest = {
        "ts": "fixed",
        "root": {"id": "42"},
        "changed_since_last_tick": True,
        "budget": {},
        "policy": {"wake_on_attention_only": True},
    }
    monkeypatch.setattr(mod, "build_digest", lambda *_a, **_k: digest)
    monkeypatch.setattr(mod, "is_overnight_quiet", lambda **_k: False)
    monkeypatch.setattr(mod, "refresh_fable_lock", lambda *_a, **_k: True)
    monkeypatch.setattr(mod, "read_lock", lambda *_a, **_k: {})
    monkeypatch.setattr(mod, "monitor_heartbeat_stale", lambda *_a, **_k: False)
    monkeypatch.setattr(mod, "absorb_operator_edits", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "_log_steer", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "effective_policy", lambda state: state.get("policy") or {})
    monkeypatch.setattr(mod, "liaison_loop_due", lambda *_a, **_k: True)
    monkeypatch.setattr(mod, "attended_tick_due", lambda *_a, **_k: False)
    monkeypatch.setattr(mod, "is_own_digest_echo", lambda *_a, **_k: False)
    monkeypatch.setattr(mod, "save_state", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "publish_if_enabled", lambda *_a, **_k: "skipped")
    def _stop(*_a, **_k):
        raise _StopLoopError

    monkeypatch.setattr(mod.time, "sleep", _stop)

    args = type(
        "A",
        (),
        {"poll": 1, "heartbeat": 0, "full_digest": False, "monitor_heartbeat": ""},
    )()
    state = {"register": "autonomous", "ticks": 1, "est_tokens": 0, "policy": {}}
    with pytest.raises(_StopLoopError):
        mod._loop(args, "42", state, tmp_path / "state.json", "autonomous", "holder", 0.0)
    line = next(
        ln for ln in capsys.readouterr().out.splitlines() if mod._SENTINEL in ln
    )
    payload = json.loads(line.split(" ", 1)[1])
    snap = tmp_path / "liaison-42.digest-full.json"
    raw = snap.read_bytes()
    assert payload["projection"]["full_digest_sha256"] == hashlib.sha256(raw).hexdigest()

    monkeypatch.setattr(
        "sys.argv",
        ["liaison-tick.py", "--root", "42", "--once", "--emit-only"],
    )
    assert mod.main() == 0
    printed = capsys.readouterr().out.strip()
    assert printed.encode() == raw


def test_persist_unlinks_temp_when_replace_fails(tmp_path, monkeypatch) -> None:
    mod = _load_tick()
    monkeypatch.setattr(mod, "_WATCH_DIR", tmp_path)

    def _boom(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr(mod.os, "replace", _boom)
    with pytest.raises(OSError, match="replace failed"):
        mod._persist_full_snapshot("42", {"a": 1})
    assert list(tmp_path.glob(".liaison-42.digest-full.*")) == []


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
