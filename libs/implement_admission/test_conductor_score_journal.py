"""Hermetic tests for conductor scoreboard journal."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.conductor_score_journal import (
    forward_mutate_tip,
    load_journal,
    read_tip,
    reject_rewind_closed_row,
    walk_journal_to_tip,
    write_birth_scoreboard,
)


def _sparse_body() -> str:
    return "\n".join(
        [
            "# Scoreboard — todo:foo",
            "",
            "| ID | Deliverable | Status | Stops |",
            "|---|---|---|---|",
            "| G1 | Architecture | OPEN | |",
            "| G2 | Frame | OPEN | |",
        ]
    )


def _g1_done_body() -> str:
    return "\n".join(
        [
            "# Scoreboard — todo:foo",
            "",
            "| ID | Deliverable | Status | Stops |",
            "|---|---|---|---|",
            "| G1 | Architecture | DONE | |",
            "| G2 | Frame | OPEN | |",
        ]
    )


def test_journal_sparse_to_two_mutations_tip_walk(tmp_path: Path) -> None:
    files_root = tmp_path
    slug = "foo"
    birth_sha = write_birth_scoreboard(slug, scoreboard_body=_sparse_body(), files_root=files_root)
    first = forward_mutate_tip(
        slug,
        next_body=_g1_done_body(),
        seat="conductor",
        dispatch_id="d1",
        reason="G1 harvest",
        rows=("G1",),
        delta="G1 OPEN→DONE",
        files_root=files_root,
    )
    assert first.rejected_reason is None
    assert first.tip_sha != birth_sha
    second = forward_mutate_tip(
        slug,
        next_body=_g1_done_body().replace("G2 | Frame | OPEN", "G2 | Frame | WIP(conductor)"),
        seat="conductor",
        dispatch_id="d1",
        reason="G2 densify",
        rows=("G2",),
        delta="G2 OPEN→WIP",
        files_root=files_root,
    )
    assert second.rejected_reason is None
    assert len(load_journal(slug, files_root=files_root)) == 2
    walked = walk_journal_to_tip(slug, files_root=files_root)
    tip = read_tip(slug, files_root=files_root)
    assert tip is not None
    assert walked == tip[1]


def test_rewind_closed_row_rejected(tmp_path: Path) -> None:
    files_root = tmp_path
    slug = "foo"
    write_birth_scoreboard(slug, scoreboard_body=_g1_done_body(), files_root=files_root)
    result = forward_mutate_tip(
        slug,
        next_body=_sparse_body(),
        seat="conductor",
        dispatch_id="d1",
        reason="illegal rewind",
        rows=("G1",),
        delta="G1 DONE→OPEN",
        files_root=files_root,
    )
    assert result.rejected_reason is not None
    assert "rewind" in result.rejected_reason.lower()
    tip = read_tip(slug, files_root=files_root)
    assert tip is not None
    assert "DONE" in tip[0]


def test_reject_rewind_closed_row_unit() -> None:
    reason = reject_rewind_closed_row(prior_body=_g1_done_body(), next_body=_sparse_body())
    assert reason is not None
