"""Round-trip: extract JSONL → render md preserves assembly shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from continuity_tape.extract_jsonl import extract_turns_from_jsonl
from continuity_tape.render_md import render_verbatim_md

pytestmark = pytest.mark.offline


def test_render_matches_legacy_shape(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    records = [
        {
            "role": "user",
            "message": {
                "content": [{"type": "text", "text": "Plan the refactor."}]
            },
        },
        {
            "role": "assistant",
            "message": {"content": [{"type": "text", "text": "On it."}]},
        },
    ]
    jsonl.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    sid = "cursor-2026-09-09-120000-abc"
    envelope = extract_turns_from_jsonl(jsonl, tools="marker", session_id=sid)
    md, turns = render_verbatim_md(envelope, sid)
    assert turns == 1
    assert md.startswith(f"# Transcript: {sid}\n")
    assert "## Turn 1 — Plan the refactor." in md
    assert "### User" in md
    assert "Plan the refactor." in md
    assert "### Assistant" in md
    assert "On it." in md
