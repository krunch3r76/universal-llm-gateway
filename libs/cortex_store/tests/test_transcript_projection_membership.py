"""Offline tests for transcript_projection_membership CP residue parsing (O15-D3)."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops.ops_transcript_project import _window_patch_from_facts
from cortex_store.transcript_projection_facts import BusSendFact, WindowFacts
from cortex_store.transcript_projection_membership import (
    HIGHLIGHT_MAX_CHARS,
    BusTurnIndex,
    extract_cp_highlight,
)
from cortex_store.transcript_projection_render import render_projection_markdown, sha256_text


@pytest.mark.offline
def test_extract_cp_highlight_verbatim() -> None:
    body = "In one line: ship the sidecar\nHighlight: verbatim residue line\nPools: deadbeef"
    assert extract_cp_highlight(body) == "verbatim residue line"


def test_extract_cp_highlight_bold_markdown() -> None:
    body = "**Highlight:** Attended resume reads tip CP, not hop queue state."
    assert extract_cp_highlight(body) == "Attended resume reads tip CP, not hop queue state."


@pytest.mark.offline
def test_extract_cp_highlight_absent() -> None:
    assert extract_cp_highlight("In one line: no highlight here") is None
    assert extract_cp_highlight("") is None
    assert extract_cp_highlight("Highlight:\n") is None


@pytest.mark.offline
def test_extract_cp_highlight_capped_at_600() -> None:
    long_text = "x" * 700
    body = f"Highlight: {long_text}"
    got = extract_cp_highlight(body)
    assert got is not None
    assert len(got) == HIGHLIGHT_MAX_CHARS
    assert got == long_text[:HIGHLIGHT_MAX_CHARS]


@pytest.mark.offline
def test_highlight_wired_into_cell_and_render() -> None:
    highlight = "lid-close residue survives projection"
    body = f"In one line: CP note\nHighlight: {highlight}\n"
    bus_index = BusTurnIndex(
        by_thread_subject={
            (
                "10223",
                "CHECKPOINT — CP155",
            ): {
                "turn_number": 155,
                "body": body,
            }
        },
        checkpoint_ordinals={155: 1},
    )
    facts = WindowFacts(transcript_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", turn_count=6)
    facts.bus_sends = [
        BusSendFact(
            thread="10223",
            kind="CHECKPOINT",
            subject="CHECKPOINT — CP155",
            subject_head="CHECKPOINT — CP155",
            turn_index=6,
            record_index=0,
        )
    ]
    patch, cells, _ = _window_patch_from_facts(
        facts,
        root="10223",
        children=[],
        bus_index=bus_index,
        closed_hi=0,
        first_seen="2026-09-08T00:00:00+00:00",
    )
    assert len(cells) == 1
    assert cells[0]["highlight"] == highlight
    state = {
        "thread": "10223",
        "updated_at": "2026-09-08",
        "runs": 1,
        "windows": {
            facts.transcript_id: {
                **patch,
                "jsonl": {"mtime": "2026-09-08T00:00:00+00:00"},
            }
        },
        "anchor_mismatches": [],
        "last_run": {},
    }
    md, _ = render_projection_markdown(state, state_sha256=sha256_text("state"))
    assert highlight in md
    assert md.count("highlight |") >= 1


@pytest.mark.offline
def test_absent_highlight_renders_empty_column() -> None:
    bus_index = BusTurnIndex(
        by_thread_subject={
            (
                "10223",
                "CHECKPOINT — CP155",
            ): {
                "turn_number": 155,
                "body": "In one line: no highlight line\n",
            }
        },
        checkpoint_ordinals={155: 1},
    )
    facts = WindowFacts(transcript_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", turn_count=4)
    facts.bus_sends = [
        BusSendFact(
            thread="10223",
            kind="CHECKPOINT",
            subject="CHECKPOINT — CP155",
            subject_head="CHECKPOINT — CP155",
            turn_index=4,
            record_index=0,
        )
    ]
    patch, cells, _ = _window_patch_from_facts(
        facts,
        root="10223",
        children=[],
        bus_index=bus_index,
        closed_hi=0,
        first_seen="2026-09-08T00:00:00+00:00",
    )
    assert "highlight" not in cells[0]
    state = {
        "thread": "10223",
        "updated_at": "2026-09-08",
        "runs": 1,
        "windows": {
            facts.transcript_id: {
                **patch,
                "jsonl": {"mtime": "2026-09-08T00:00:00+00:00"},
            }
        },
        "anchor_mismatches": [],
        "last_run": {},
    }
    md, _ = render_projection_markdown(state, state_sha256=sha256_text("state"))
    assert "highlight |" in md
    assert "| CHECKPOINT |" in md
    assert "| lid-close residue survives projection |" not in md
