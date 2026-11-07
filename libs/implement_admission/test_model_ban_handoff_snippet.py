"""Offline handoff ban snippet (source guard; full route test lives on stargate)."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def test_team_handoff_module_calls_safe_ban_gate() -> None:
    src = (
        _REPO / "services/universal-stargate/systems/frontier_consult/route_handoff.py"
    ).read_text(encoding="utf-8")
    assert "check_model_ban_safe" in src
    assert 'override_model and str(override_model).strip()' in src
