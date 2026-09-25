"""Slice C — hop seal ordering before keystroke (Fable I8)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from bus_watch.ide_hop import fire_ide_hop

pytestmark = pytest.mark.offline

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "liaison-ide-hop.py"
_HOP_ARGS = [
    "liaison-ide-hop.py",
    "--root",
    "10479",
    "--row",
    "R16 test",
    "--transcript-id",
    "94b2a901-7807-460e-8102-2e83bb5b96c7",
    "--no-auto-arm",
    "--dry-run",
]


def _load_hop_module():
    spec = importlib.util.spec_from_file_location("liaison_ide_hop_under_test", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hop_retires_departing_tab_after_land(capsys) -> None:
    hop_mod = _load_hop_module()
    call_order: list[str] = []
    args = [a for a in _HOP_ARGS if a != "--dry-run"]

    def fake_fire(*_a, **_k):
        call_order.append("fire")
        return {"ok": True, "root": "10479"}

    def fake_retire(root, holder, **_k):
        call_order.append("retire")
        assert root == "10479"
        assert holder == "ide:94b2a901-7807-460e-8102-2e83bb5b96c7"
        return {"ok": True, "stopped_loops": [1], "stopped_tails": []}

    with (
        patch.object(hop_mod, "harvest_judgment_turns", return_value={"ok": True}),
        patch.object(hop_mod, "load_state", return_value={}),
        patch.object(hop_mod, "effective_policy", return_value={}),
        patch.object(hop_mod, "build_digest", return_value={}),
        patch.object(hop_mod, "resolve_now_row", return_value=("R16 test", "arg")),
        patch.object(hop_mod, "format_now_line", return_value="R16 test"),
        patch.object(
            hop_mod, "seal_hop_window", return_value={"ok": True, "phase": "sealed"}
        ),
        patch.object(hop_mod, "fire_ide_hop", side_effect=fake_fire),
        patch.object(hop_mod, "retire_departing_tab", side_effect=fake_retire),
        patch.object(hop_mod, "hop_qualifies", return_value={"ok": True}),
        patch.object(hop_mod, "live_watcher_labels", return_value=[]),
        patch.object(hop_mod, "tick_register", return_value="attended"),
        patch.object(hop_mod, "policy_gui_host", return_value="jupiter"),
        patch.object(hop_mod, "build_ide_hop_message", return_value="resume 10479\n"),
        patch.object(sys, "argv", args),
    ):
        code = hop_mod.main()
    assert code == 0
    assert call_order == ["fire", "retire"]
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["retire"]["stopped_loops"] == [1]
    assert "LIAISON_HOP_TAB_GOAL_RELEASE" in payload["goal_release"]
    assert "UpdateGoal" in captured.err


def test_hop_seals_with_channel_hop_before_keystroke(capsys) -> None:
    call_order: list[str] = []
    hop_mod = _load_hop_module()

    def fake_seal(root_id: str, *, transcript_id: str, **kwargs):
        call_order.append("seal")
        assert root_id == "10479"
        assert transcript_id == "94b2a901-7807-460e-8102-2e83bb5b96c7"
        return {"ok": True, "phase": "sealed", "bus_turn": 2621}

    def fake_fire(*args, **kwargs):
        call_order.append("fire")
        return {"ok": True, "dry_run": True, "root": "10479"}

    with (
        patch.object(hop_mod, "seal_hop_window", side_effect=fake_seal),
        patch.object(hop_mod, "fire_ide_hop", side_effect=fake_fire),
        patch.object(hop_mod, "hop_qualifies", return_value={"ok": True}),
        patch.object(hop_mod, "live_watcher_labels", return_value=[]),
        patch.object(hop_mod, "tick_register", return_value="attended"),
        patch.object(hop_mod, "policy_gui_host", return_value="jupiter"),
        patch.object(hop_mod, "build_ide_hop_message", return_value="resume 10479\n"),
        patch.object(sys, "argv", _HOP_ARGS),
    ):
        code = hop_mod.main()
    assert code == 0
    assert call_order == ["seal", "fire"]


def test_hop_aborts_when_seal_fails(capsys) -> None:
    fire_called = False
    hop_mod = _load_hop_module()

    def fake_fire(*args, **kwargs):
        nonlocal fire_called
        fire_called = True
        return {"ok": True}

    with (
        patch.object(
            hop_mod,
            "seal_hop_window",
            return_value={"ok": False, "phase": "seal_timeout"},
        ),
        patch.object(hop_mod, "fire_ide_hop", side_effect=fake_fire),
        patch.object(hop_mod, "hop_qualifies", return_value={"ok": True}),
        patch.object(hop_mod, "live_watcher_labels", return_value=[]),
        patch.object(sys, "argv", _HOP_ARGS),
    ):
        code = hop_mod.main()
    assert code == 2
    assert fire_called is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["phase"] == "seal_timeout"


def test_hop_refuses_without_transcript_id(capsys) -> None:
    fire_called = False
    hop_mod = _load_hop_module()
    args = [
        "liaison-ide-hop.py",
        "--root",
        "10479",
        "--row",
        "R16 test",
        "--no-auto-arm",
    ]

    def fake_fire(*args, **kwargs):
        nonlocal fire_called
        fire_called = True
        return {"ok": True}

    with (
        patch.object(hop_mod, "fire_ide_hop", side_effect=fake_fire),
        patch.object(hop_mod, "seal_hop_window") as seal_mock,
        patch.object(hop_mod, "hop_qualifies", return_value={"ok": True}),
        patch.object(hop_mod, "live_watcher_labels", return_value=[]),
        patch.object(sys, "argv", args),
    ):
        code = hop_mod.main()
    assert code == 2
    assert fire_called is False
    seal_mock.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert payload["phase"] == "seal_transcript_unknown"


def test_bearer_headers_fall_back_to_mcp_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bus_watch.digest_budget import agent_bus_bearer_headers

    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    yaml_path = tmp_path / "mcp.yaml"
    yaml_path.write_text("AGENT_BUS_TOKEN: hop-secret\n", encoding="utf-8")
    monkeypatch.setattr("bus_watch.digest_budget._MCP_YAML", yaml_path)
    assert agent_bus_bearer_headers() == {"Authorization": "Bearer hop-secret"}


def test_bearer_headers_env_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bus_watch.digest_budget import agent_bus_bearer_headers

    monkeypatch.setenv("AGENT_BUS_TOKEN", "from-env")
    yaml_path = tmp_path / "mcp.yaml"
    yaml_path.write_text("AGENT_BUS_TOKEN: from-yaml\n", encoding="utf-8")
    monkeypatch.setattr("bus_watch.digest_budget._MCP_YAML", yaml_path)
    assert agent_bus_bearer_headers() == {"Authorization": "Bearer from-env"}


def test_ac1_fire_ide_hop_requires_seal_kwonly() -> None:
    with pytest.raises(TypeError):
        fire_ide_hop("msg", root_id="10479", gui_host="jupiter")  # type: ignore[call-arg]


def test_ac2_fire_ide_hop_refuses_unsealed_receipt() -> None:
    with (
        patch("bus_watch.ide_hop.subprocess.run") as run_mock,
        patch("bus_watch.ide_hop.durable_write_text") as write_mock,
    ):
        out = fire_ide_hop(
            "msg",
            root_id="10479",
            seal={"ok": False, "phase": "seal_timeout"},
            gui_host="jupiter",
        )
    assert out == {
        "ok": False,
        "phase": "seal_receipt_missing",
        "root": "10479",
        "bus_turn": None,
        "execution_id": None,
    }
    run_mock.assert_not_called()
    write_mock.assert_not_called()


def test_ac3_fire_ide_hop_proceeds_with_ok_seal_and_none_bus_turn() -> None:
    with patch("bus_watch.ide_hop.durable_write_text"):
        out = fire_ide_hop(
            "resume 10479\n",
            root_id="10479",
            seal={"ok": True, "bus_turn": None},
            gui_host="jupiter",
            dry_run=True,
        )
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["bus_turn"] is None
    assert out["execution_id"] is None


def test_ac11_qualify_refusal_skips_seal_and_fire(capsys) -> None:
    hop_mod = _load_hop_module()
    seal_called = False
    fire_called = False

    def fake_seal(*_a, **_k):
        nonlocal seal_called
        seal_called = True
        return {"ok": True}

    def fake_fire(*_a, **_k):
        nonlocal fire_called
        fire_called = True
        return {"ok": True}

    with (
        patch.object(hop_mod, "harvest_judgment_turns", return_value={"ok": True}),
        patch.object(hop_mod, "load_state", return_value={}),
        patch.object(hop_mod, "effective_policy", return_value={}),
        patch.object(hop_mod, "build_digest", return_value={}),
        patch.object(hop_mod, "resolve_now_row", return_value=("quiet", "digest")),
        patch.object(hop_mod, "format_now_line", return_value="quiet"),
        patch.object(
            hop_mod,
            "hop_qualifies",
            return_value={"ok": False, "reason": "no_autonomous_followup"},
        ),
        patch.object(hop_mod, "seal_hop_window", side_effect=fake_seal),
        patch.object(hop_mod, "fire_ide_hop", side_effect=fake_fire),
        patch.object(hop_mod, "live_watcher_labels", return_value=[]),
        patch.object(sys, "argv", _HOP_ARGS),
    ):
        code = hop_mod.main()
    assert code == 2
    assert seal_called is False
    assert fire_called is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["phase"] == "no_autonomous_followup"
    assert payload["stay"] is True


def test_ac7_force_still_refuses_when_seal_fails(capsys) -> None:
    hop_mod = _load_hop_module()
    fire_called = False

    def fake_fire(*_a, **_k):
        nonlocal fire_called
        fire_called = True
        return {"ok": True}

    with (
        patch.object(hop_mod, "harvest_judgment_turns", return_value={"ok": True}),
        patch.object(hop_mod, "load_state", return_value={}),
        patch.object(hop_mod, "effective_policy", return_value={}),
        patch.object(hop_mod, "build_digest", return_value={}),
        patch.object(hop_mod, "resolve_now_row", return_value=("quiet", "digest")),
        patch.object(hop_mod, "format_now_line", return_value="quiet"),
        patch.object(
            hop_mod,
            "hop_qualifies",
            return_value={"ok": False, "reason": "no_autonomous_followup"},
        ),
        patch.object(
            hop_mod,
            "seal_hop_window",
            return_value={"ok": False, "phase": "seal_timeout"},
        ),
        patch.object(hop_mod, "fire_ide_hop", side_effect=fake_fire),
        patch.object(hop_mod, "live_watcher_labels", return_value=[]),
        patch.object(sys, "argv", [*_HOP_ARGS, "--force"]),
    ):
        code = hop_mod.main()
    assert code == 2
    assert fire_called is False
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["phase"] == "seal_timeout"
