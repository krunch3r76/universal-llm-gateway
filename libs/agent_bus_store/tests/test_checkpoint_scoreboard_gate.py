"""Tests for checkpoint scoreboard gate (AMEND-R T18–T25)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from agent_bus_store.checkpoint_scoreboard_wiring import assert_scoreboard_coherent

pytestmark = pytest.mark.offline

_SCOREBOARD_BODY = """# Scoreboard

| G | Deliverable | Status | Stops |
| G6 | review | DONE | |
| G7 | ship | OPEN | |
"""


def _write_scoreboard(files_root: Path, slug: str, body: str) -> str:
    path = files_root / "notes/system/scoreboards" / f"{slug}-scoreboard.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return f"cortex://notes/system/scoreboards/{slug}-scoreboard.md"


@patch.dict(os.environ, {"AGENT_BUS_SCOREBOARD_GATE_MODE": "enforce"})
def test_t18_enforce_drift_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _write_scoreboard(tmp_path, "human-continuity-speech-tape", _SCOREBOARD_BODY)
    body = (
        "TYPE: CHECKPOINT\n"
        "Scoreboard: human-continuity-speech-tape · G6 CLEAR · G7 LANDED @ sha\n"
    )
    with pytest.raises(HTTPException) as exc:
        assert_scoreboard_coherent(
            thread="10223",
            subject="CHECKPOINT",
            body=body,
            tags=["role:root"],
        )
    assert exc.value.detail["code"] == "checkpoint.scoreboard_drift"
    rows = exc.value.detail["rows"]
    assert any(r["g"] == "G7" and r["cp"] == "LANDED" for r in rows)


@patch.dict(os.environ, {"AGENT_BUS_SCOREBOARD_GATE_MODE": "enforce"})
def test_t19_coherent_projects_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    body = _SCOREBOARD_BODY.replace("G7 | ship | OPEN", "G7 | ship | DONE")
    _write_scoreboard(tmp_path, "human-continuity-speech-tape", body)
    residue = (
        "TYPE: CHECKPOINT\n"
        "Scoreboard: human-continuity-speech-tape · G7 LANDED\n"
    )
    assert (
        assert_scoreboard_coherent(
            thread="10223",
            subject="CHECKPOINT",
            body=residue,
            tags=["role:root"],
        )
        is None
    )


@patch.dict(os.environ, {"AGENT_BUS_SCOREBOARD_GATE_MODE": "enforce"})
def test_t20_root_prose_g_claim_without_scoreboard_line() -> None:
    body = "TYPE: CHECKPOINT\nG7 LANDED on master.\n"
    with pytest.raises(HTTPException) as exc:
        assert_scoreboard_coherent(
            thread="10223",
            subject="CHECKPOINT",
            body=body,
            tags=["role:root"],
        )
    assert exc.value.detail["code"] == "checkpoint.scoreboard_line_required"


@patch.dict(os.environ, {"AGENT_BUS_SCOREBOARD_GATE_MODE": "enforce"})
def test_t21_unresolved_scoreboard() -> None:
    body = "TYPE: CHECKPOINT\nScoreboard: · G7 LANDED\n"
    with pytest.raises(HTTPException) as exc:
        assert_scoreboard_coherent(
            thread="10223",
            subject="CHECKPOINT",
            body=body,
            tags=[],
        )
    assert exc.value.detail["code"] == "checkpoint.scoreboard_unresolved"


def test_t22_non_checkpoint_subject_no_gate() -> None:
    assert (
        assert_scoreboard_coherent(
            thread="1",
            subject="reply",
            body="Scoreboard: foo · G7 LANDED",
            tags=[],
        )
        is None
    )


def test_t23_status_complete_alone_no_gate() -> None:
    assert (
        assert_scoreboard_coherent(
            thread="1",
            subject="CHECKPOINT",
            body="TYPE: CHECKPOINT\nstatus: complete\n",
            tags=[],
        )
        is None
    )


@patch.dict(os.environ, {"AGENT_BUS_SCOREBOARD_GATE_MODE": "observe"})
def test_t24_observe_mode_advisory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    _write_scoreboard(tmp_path, "human-continuity-speech-tape", _SCOREBOARD_BODY)
    body = "TYPE: CHECKPOINT\nScoreboard: human-continuity-speech-tape · G7 LANDED\n"
    advisory = assert_scoreboard_coherent(
        thread="10223",
        subject="CHECKPOINT",
        body=body,
        tags=[],
    )
    assert advisory is not None
    assert advisory["advisory"] == "checkpoint.scoreboard_drift"
    assert advisory["mode"] == "observe"
