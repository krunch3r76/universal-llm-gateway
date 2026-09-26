"""AC7: agent_bus_store non-test tree must not reference host JSONL paths."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_FORBIDDEN = (
    "extract_turns_from_jsonl",
    "_transcripts_root",
    "jsonl_path_for_uuid",
    "_find_jsonl_for_uuid",
    "CURSOR_AGENT_TRANSCRIPTS_ROOT",
    "source.jsonl",
)

_ROOT = Path(__file__).resolve().parents[1]


def test_agent_bus_store_host_import_fence() -> None:
    hits: list[str] = []
    for path in _ROOT.rglob("*.py"):
        if path.parts[-2:] == ("tests", path.name) or "/tests/" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            if needle in text:
                hits.append(f"{path.relative_to(_ROOT)}: {needle}")
    assert hits == []
