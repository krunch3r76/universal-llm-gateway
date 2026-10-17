from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from research_dialogue.cli import main

pytestmark = pytest.mark.offline


def test_cli_turn_delegates_to_driver(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    (session / "state.json").write_text(json.dumps({"turn_count": 0}), encoding="utf-8")
    with patch("research_dialogue.cli.run_driver", return_value=(0, {"cli": "ok"})) as rd:
        code = main(
            [
                "turn",
                "--session",
                str(session),
                "--expect-stargate-pid",
                "1",
                "--expect-cloud-proxy-pid",
                "2",
            ]
        )
    assert code == 0
    rd.assert_called_once()
    assert rd.call_args.kwargs.get("stream") is False


def test_cli_smoke_runs_preflight(tmp_path: Path) -> None:
    with patch("research_dialogue.cli.preflight_guard", return_value=(5, {"abort": "pid"})):
        code = main(
            [
                "smoke",
                "--session",
                str(tmp_path),
                "--expect-stargate-pid",
                "1",
                "--expect-cloud-proxy-pid",
                "2",
            ]
        )
    assert code == 5
