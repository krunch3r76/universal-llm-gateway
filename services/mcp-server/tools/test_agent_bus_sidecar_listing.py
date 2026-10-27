"""Sidecar section listing on agent_bus get default projection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools._line_range import DEFAULT_UNWINDOWED_MAX_BYTES
from tools._unwindowed_read_projection import MAX_LISTED_SECTIONS
from tools.agent_bus._closeout_projection import project_turn_body
from tools.agent_bus.fetch import _get_dispatch


def test_get_default_includes_sidecar_section_listing(
    tmp_path: Path, monkeypatch
) -> None:
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    sidecar_uri = "cortex://notes/system/threads/12286-auto-overflow-2.md"
    rel = sidecar_uri.removeprefix("cortex://")
    sidecar_body = "\n\n".join(
        [
            "# Spill",
            "## Executive envelope\ninline head only in turn body\n",
            "## Full body\n" + ("x" * 5000),
        ]
    )
    (cortex_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (cortex_root / rel).write_text(sidecar_body, encoding="utf-8")

    turn = {
        "subject": "Big reply",
        "body": f"Brief head\n\nSidecar: {sidecar_uri}",
        "thread": "12286",
        "turn_number": 2,
        "sidecar_uri": sidecar_uri,
    }
    projected = project_turn_body(turn, projection="compact")
    assert projected["body"] == turn["body"]
    assert len(projected["sidecar_sections"]) >= 2
    assert projected["sidecar_section_reads"]
    assert all("md_read" in call for call in projected["sidecar_section_reads"])


@patch("tools.agent_bus.fetch.relay")
def test_get_projection_full_omits_sidecar_listing(
    relay, tmp_path: Path, monkeypatch
) -> None:
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    sidecar_uri = "cortex://notes/system/threads/1-auto-overflow-2.md"
    rel = sidecar_uri.removeprefix("cortex://")
    (cortex_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (cortex_root / rel).write_text("## A\na\n## B\nb\n", encoding="utf-8")

    turn = {
        "subject": "reply",
        "body": f"head\n\nSidecar: {sidecar_uri}",
        "thread": "1",
        "turn_number": 2,
        "sidecar_uri": sidecar_uri,
    }
    relay.return_value = turn
    out = _get_dispatch(thread="1", turn_number=2, projection="full")
    assert out["turn"]["body"] == turn["body"]
    assert "sidecar_sections" not in out["turn"]


def _sidecar_turn(tmp_path: Path, monkeypatch, sidecar_body: str, uri_suffix: str) -> dict:
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    sidecar_uri = f"cortex://notes/system/threads/{uri_suffix}.md"
    rel = sidecar_uri.removeprefix("cortex://")
    (cortex_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (cortex_root / rel).write_text(sidecar_body, encoding="utf-8")
    return {
        "subject": "reply",
        "body": "brief",
        "thread": "20933",
        "turn_number": 1,
        "sidecar_uri": sidecar_uri,
    }


def test_sidecar_listing_truncated_flags_and_sections_total(
    tmp_path: Path, monkeypatch
) -> None:
    sections = [f"## Section {i}\nbody {i}\n" for i in range(MAX_LISTED_SECTIONS + 5)]
    body = "# Root\n\n" + "\n\n".join(sections)
    turn = _sidecar_turn(tmp_path, monkeypatch, body, "listing-cap")
    projected = project_turn_body(turn, projection="compact")
    assert projected["listing_truncated"] is True
    assert projected["sections_total"] == MAX_LISTED_SECTIONS + 6
    assert len(projected["sidecar_sections"]) == MAX_LISTED_SECTIONS


def test_sidecar_listing_not_truncated_reports_false(
    tmp_path: Path, monkeypatch
) -> None:
    body = "# Root\n\n## A\na\n\n## B\nb\n"
    turn = _sidecar_turn(tmp_path, monkeypatch, body, "listing-small")
    projected = project_turn_body(turn, projection="compact")
    assert projected["listing_truncated"] is False
    assert projected["sections_total"] >= 2


def test_sidecar_load_cap_flags(tmp_path: Path, monkeypatch) -> None:
    big = "x" * 40_000
    body = f"# Doc\n\n## Only\n{big}\n"
    turn = _sidecar_turn(tmp_path, monkeypatch, body, "sidecar-cap")
    projected = project_turn_body(turn, projection="compact")
    assert projected["sidecar_capped"] is True
    assert projected["sidecar_bytes_total"] > DEFAULT_UNWINDOWED_MAX_BYTES
    assert projected["sidecar_bytes_returned"] == DEFAULT_UNWINDOWED_MAX_BYTES


def test_sidecar_load_under_cap_reports_false(tmp_path: Path, monkeypatch) -> None:
    body = "# Doc\n\n## Only\n" + ("y" * 1000)
    turn = _sidecar_turn(tmp_path, monkeypatch, body, "sidecar-small")
    projected = project_turn_body(turn, projection="compact")
    assert projected["sidecar_capped"] is False
    assert projected["sidecar_bytes_total"] == projected["sidecar_bytes_returned"]
