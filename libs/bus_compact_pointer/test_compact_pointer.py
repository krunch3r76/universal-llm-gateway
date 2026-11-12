"""Unit tests for compact agent-bus pointer formatting."""

from __future__ import annotations

import pytest

from bus_compact_pointer import (
    build_section_map,
    compact_pointer_body,
    extract_verdict_line,
    format_closeout_memo_bus_body,
)

pytestmark = pytest.mark.offline

_HARVEST = """# Review

## findings

Line one.

## verdict

VERDICT: APPROVE range=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa..bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb head=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"""


def test_build_section_map_offsets() -> None:
    rows, hidden = build_section_map(_HARVEST, max_rows=20)
    assert hidden == 0
    assert len(rows) >= 2
    verdict_row = next(r for r in rows if "verdict" in r["heading"].lower())
    assert verdict_row["offset"] < len(_HARVEST)
    assert verdict_row["line"] >= 1


def test_compact_pointer_budget_and_required_fields() -> None:
    rows, _ = build_section_map(_HARVEST)
    body = compact_pointer_body(
        verdict="APPROVE",
        execution_id="exec-1",
        dispatch_id="disp-1",
        sidecar_uri="cortex://notes/h.md",
        sidecar_sha256="abc",
        section_map=rows,
        context_lines=["lane: SDK", "sha: deadbeef"],
    )
    assert len(body) <= 2000
    assert "VERDICT:" in body
    assert "exec-1" in body
    assert "disp-1" in body
    assert "cortex://notes/h.md" in body
    assert "abc" in body
    assert "## sections" in body


def test_truncation_drops_section_rows_before_context() -> None:
    big = "\n".join(f"## section {i}\n\ncontent\n" for i in range(80))
    rows, hidden = build_section_map(big, max_rows=12)
    assert hidden == 68
    body = compact_pointer_body(
        verdict="PARTIAL",
        sidecar_uri="cortex://notes/x.md",
        sidecar_sha256="d" * 64,
        section_map=rows,
        context_lines=[f"context-{i}" for i in range(10)],
        section_tail="+68 more (md_list notes/x.md)",
        max_chars=480,
    )
    assert len(body) <= 480
    assert "VERDICT:" in body
    assert "sha256:" in body
    section_rows = [ln for ln in body.splitlines() if ln.startswith("| section ")]
    assert len(section_rows) < 12


def test_extract_verdict_line() -> None:
    assert extract_verdict_line(_HARVEST) is not None
    line = extract_verdict_line(_HARVEST)
    assert line is not None and line.startswith("VERDICT:")


def test_closeout_memo_bus_body() -> None:
    overflow = "<closeout_memo v=\"1\">\nlane: 23222\n</closeout_memo>\n"
    body = format_closeout_memo_bus_body(
        memo_kind="sdk_closeout",
        memo_status="completed",
        wake_lane="23222",
        dispatch_id="disp-x",
        execution_id="exec-y",
        sidecar_uri="cortex://notes/closeout.md",
        sidecar_text=overflow,
    )
    assert len(body) <= 2000
    assert "CLOSEOUT COMPLETED" in body
    assert "exec-y" in body
    assert "## sections" in body
