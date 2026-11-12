"""Unit tests for compact agent-bus pointer formatting."""

from __future__ import annotations

import hashlib
import re

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


def _sha256_from_pointer(body: str) -> str:
    match = re.search(r"sha256: `([0-9a-f]{64})`", body)
    assert match is not None
    return match.group(1)


def test_closeout_memo_bus_body_sha256_matches_stored_sidecar_bytes() -> None:
    stored = "## closeout\n\nAll green.\n"
    digest = hashlib.sha256(stored.encode("utf-8")).hexdigest()
    body = format_closeout_memo_bus_body(
        memo_kind="sdk_closeout",
        memo_status="completed",
        wake_lane="23222",
        dispatch_id="disp-x",
        execution_id="exec-y",
        sidecar_uri="cortex://notes/closeout.md",
        sidecar_text=stored,
    )
    assert _sha256_from_pointer(body) == digest


def test_c1_format_closeout_memo_no_sidecar_uri_inlines_text() -> None:
    overflow = "## closeout\n\nAll green.\n"
    body = format_closeout_memo_bus_body(
        memo_kind="sdk_closeout",
        memo_status="completed",
        wake_lane="23222",
        dispatch_id="disp-x",
        execution_id="exec-y",
        sidecar_uri=None,
        sidecar_text=overflow,
    )
    assert body == overflow
    assert "sidecar_ref:" not in body
    assert "agent-bus:" not in body


def test_c4_section_tail_recount_after_truncation() -> None:
    big = "\n".join(f"## section {i}\n\ncontent\n" for i in range(80))
    rows, hidden = build_section_map(big, max_rows=12)
    assert hidden == 68
    pad = "x" * 400
    body = compact_pointer_body(
        verdict="PARTIAL",
        sidecar_uri="cortex://notes/x.md",
        sidecar_sha256="d" * 64,
        section_map=rows,
        context_lines=[pad, pad, pad],
        section_tail="+68 more (md_list notes/x.md)",
        section_map_source_rows=len(rows) + hidden,
        max_chars=320,
    )
    assert len(body) <= 320
    section_rows = [ln for ln in body.splitlines() if ln.startswith("| section ")]
    assert len(section_rows) < len(rows)
    dropped = 68 + len(rows) - len(section_rows)
    assert dropped > 0
    assert f"+{dropped} more" in body


def test_c8_extract_verdict_line_allows_leading_whitespace() -> None:
    text = "  VERDICT: APPROVE\n"
    line = extract_verdict_line(text)
    assert line is not None
    assert line.startswith("VERDICT: APPROVE")


def test_c2_protected_metadata_lines_survive_truncation() -> None:
    meta = [
        "- execution_id: `exec-1`",
        "- satellite_execution_id: `sat-1`",
        "- cost_source: `catalog`",
        "- archive_uri: `cortex://notes/arch.md`",
        "- content_proof_uri: `cortex://notes/proof.md`",
    ]
    sections = [
        {
            "heading": f"section {i}",
            "level": 2,
            "offset": i * 40,
            "line": i + 1,
        }
        for i in range(40)
    ]
    body = compact_pointer_body(
        verdict="APPROVE",
        execution_id="exec-1",
        satellite_execution_id="sat-1",
        archive_uri="cortex://notes/arch.md",
        content_proof_uri="cortex://notes/proof.md",
        protected_lines=meta,
        sidecar_uri="cortex://notes/h.md",
        sidecar_sha256="a" * 64,
        section_map=sections,
        context_lines=[f"context-{i}" for i in range(15)],
        section_map_source_rows=40,
        max_chars=900,
    )
    assert len(body) <= 900
    assert "satellite_execution_id" in body
    assert "archive_uri" in body
    assert "content_proof_uri" in body
    assert "cost_source" in body


def test_pathological_verdict_preserves_full_sha256_line() -> None:
    long_verdict = "VERDICT: " + ("X" * 1900)
    sections = [
        {
            "heading": f"section {i}",
            "level": 2,
            "offset": i * 40,
            "line": i + 1,
        }
        for i in range(50)
    ]
    digest = "a" * 64
    body = compact_pointer_body(
        verdict=long_verdict,
        execution_id="e" * 40,
        dispatch_id="d" * 40,
        sidecar_uri="cortex://notes/huge.md",
        sidecar_sha256=digest,
        section_map=sections,
        context_lines=[f"context-{i}" for i in range(20)],
    )
    assert len(body) <= 2000
    assert _sha256_from_pointer(body) == digest
    assert "sidecar_ref: `cortex://notes/huge.md`" in body
    assert "execution_id:" in body
    assert "dispatch_id:" in body
    assert body.endswith(f"sha256: `{digest}`") or f"sha256: `{digest}`" in body
    assert re.search(r"sha256: `[0-9a-f]{64}`", body) is not None
