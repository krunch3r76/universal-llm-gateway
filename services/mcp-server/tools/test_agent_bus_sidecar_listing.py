"""Sidecar section listing on agent_bus get default projection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
