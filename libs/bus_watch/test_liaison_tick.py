"""liaison-tick.py operator-path budget on --set / mark early return."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from bus_watch.digest_budget import build_budget_block
from bus_watch.ide_budget import IDE_BUDGET_SOURCE

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_TICK_SCRIPT = _REPO / "scripts" / "liaison-tick.py"
_TID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _load_tick_module(name: str):
    spec = importlib.util.spec_from_file_location(name, _TICK_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_liaison_tick_set_includes_context_budget_and_hop_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from bus_watch import fable_lock as fl

    watch = tmp_path / "watchers"
    watch.mkdir()
    state_path = watch / "liaison-18291.tick.json"
    state_path.write_text(
        json.dumps(
            {
                "register": "attended",
                "policy": {"now_row": "R9 hop now", "gear": "1-fable-mvp"},
            }
        ),
        encoding="utf-8",
    )
    (watch / "liaison-fable-18291.lock").write_text(
        json.dumps({"holder": f"ide:{_TID}", "root": "18291"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(fl, "WATCH_DIR", watch)

    budget = build_budget_block(
        used_tokens=200_000,
        window_limit_tokens=256_000,
        model="ide-tab",
        source=IDE_BUDGET_SOURCE,
        scope="liaison_seat",
        epoch=_TID,
        as_of="2026-10-18T00:00:00Z",
        transcript_id=_TID,
        holder_basis="seat_lock",
        tool_calls=40,
    )
    assert budget["stop_class"] == "CONTEXT_BUDGET"

    mod = _load_tick_module("liaison_tick_set_budget_test")
    monkeypatch.setattr(
        mod,
        "_operator_path_budget",
        lambda root, state, register: budget,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liaison-tick.py",
            "--root",
            "18291",
            "--state-file",
            str(state_path),
            "--set",
            "now_row=R9 hop now",
        ],
    )
    with patch("bus_watch.loop_tape_mint.ensure_loop_tape", return_value=None):
        assert mod.main() == 0

    lines = [ln for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]
    payload = json.loads(lines[-1])
    assert payload["budget"]["stop_class"] == "CONTEXT_BUDGET"
    assert payload["stop_class"] == "CONTEXT_BUDGET"
    assert "liaison-ide-hop.py" in payload["hop_command"]
    assert "--gui-host :8" in payload["hop_command"]
    assert _TID in payload["hop_command"]


def test_liaison_ide_hop_defaults_row_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bus_watch import fable_lock as fl

    watch = tmp_path / "watchers"
    watch.mkdir()
    state_path = watch / "liaison-18291.tick.json"
    state_path.write_text(
        json.dumps({"register": "attended", "policy": {"now_row": "digest NOW"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(fl, "WATCH_DIR", watch)

    captured: dict = {}

    def _fake_fire(message, **kwargs):  # noqa: ANN001
        captured["row_in_message"] = message
        return {"ok": True, "phase": "landed", "landed_transcript_id": "new-tab"}

    hop_path = _REPO / "scripts" / "liaison-ide-hop.py"
    spec = importlib.util.spec_from_file_location("liaison_ide_hop_row_default", hop_path)
    assert spec and spec.loader
    hop_mod = importlib.util.module_from_spec(spec)
    sys.modules["liaison_ide_hop_row_default"] = hop_mod
    spec.loader.exec_module(hop_mod)

    monkeypatch.setattr(hop_mod, "harvest_judgment_turns", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        hop_mod,
        "build_digest",
        lambda *a, **k: {"policy": {"now_row": "digest NOW"}},
    )
    monkeypatch.setattr(hop_mod, "resolve_now_row", lambda d: ("digest NOW", "policy"))
    monkeypatch.setattr(
        hop_mod,
        "format_now_line",
        lambda raw, src, digest, **k: str(raw or ""),
    )
    monkeypatch.setattr(hop_mod, "hop_qualifies", lambda **k: {"ok": True})
    monkeypatch.setattr(
        hop_mod,
        "seal_hop_window",
        lambda *a, **k: {"ok": True, "bus_turn": 3},
    )
    monkeypatch.setattr(hop_mod, "fire_ide_hop", _fake_fire)
    monkeypatch.setattr(hop_mod, "retire_departing_tab", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(hop_mod, "live_watcher_labels", lambda *a, **k: [])
    monkeypatch.setattr(hop_mod, "tick_register", lambda *a, **k: "attended")
    monkeypatch.setattr(hop_mod, "policy_gui_host", lambda *a, **k: ":8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "liaison-ide-hop.py",
            "--root",
            "18291",
            "--transcript-id",
            _TID,
            "--dry-run",
        ],
    )
    assert hop_mod.main() == 0
    assert "digest NOW" in captured.get("row_in_message", "")
