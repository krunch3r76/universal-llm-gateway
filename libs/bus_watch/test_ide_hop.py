"""IDE hop defaults (:8 Xvfb) and hop message bindings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bus_watch.digest_budget import POLICY_DEFAULTS
from bus_watch.ide_hop import build_ide_hop_message, fire_ide_hop, policy_gui_host

pytestmark = pytest.mark.offline


def test_policy_defaults_gui_host_is_xvfb_eight() -> None:
    assert POLICY_DEFAULTS["gui_host"] == ":8"


def test_policy_gui_host_defaults_to_eight_without_tick_state(tmp_path: Path) -> None:
    with patch("bus_watch.ide_hop.WATCH_DIR", tmp_path):
        assert policy_gui_host("18291") == ":8"


def test_build_ide_hop_message_includes_hop_xvfb_line() -> None:
    msg = build_ide_hop_message(
        "18291",
        row="R1 test row",
        arm_labels=[],
        register="attended",
    )
    assert "HOP:" in msg
    assert "--gui-host :8" in msg
    assert 'budget.stop_class == \'CONTEXT_BUDGET\'' in msg
    assert "--root 18291" in msg


def test_fire_ide_hop_defaults_gui_host_to_eight_dry_run() -> None:
    seal = {"ok": True, "bus_turn": 1}
    with patch("bus_watch.ide_hop.durable_write_text"):
        out = fire_ide_hop(
            "resume 18291",
            root_id="18291",
            seal=seal,
            gui_host=None,
            dry_run=True,
        )
    assert out.get("ok") is True
    assert out.get("gui_host") == ":8"
