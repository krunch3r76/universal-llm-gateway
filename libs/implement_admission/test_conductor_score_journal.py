"""Hermetic tests for conductor scoreboard journal."""

from __future__ import annotations

from pathlib import Path

from implement_admission.conductor_score_journal import (
    JournalRecord,
    _journal_path,
    _tip_path,
    append_journal_record,
    birth_scoreboard,
    forward_mutate_tip,
    load_journal,
    read_tip,
    reject_rewind_closed_row,
    tip_sha256,
    walk_journal_to_tip,
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
    birth_sha = birth_scoreboard(slug, scoreboard_body=_sparse_body(), files_root=files_root)
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
    assert len(load_journal(slug, files_root=files_root)) == 3
    walked = walk_journal_to_tip(slug, files_root=files_root)
    tip = read_tip(slug, files_root=files_root)
    assert tip is not None
    assert walked == tip[1]


def test_rewind_closed_row_rejected(tmp_path: Path) -> None:
    files_root = tmp_path
    slug = "foo"
    birth_scoreboard(slug, scoreboard_body=_g1_done_body(), files_root=files_root)
    journal_before = len(load_journal(slug, files_root=files_root))
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
    assert len(load_journal(slug, files_root=files_root)) == journal_before
    tip = read_tip(slug, files_root=files_root)
    assert tip is not None
    assert "DONE" in tip[0]


def test_reject_rewind_closed_row_unit() -> None:
    reason = reject_rewind_closed_row(prior_body=_g1_done_body(), next_body=_sparse_body())
    assert reason is not None


def test_crash_after_journal_before_tip_read_tip_recovers(tmp_path: Path) -> None:
    files_root = tmp_path
    slug = "crash-mutate"
    birth_scoreboard(slug, scoreboard_body=_sparse_body(), files_root=files_root)
    next_body = _g1_done_body()
    new_sha = tip_sha256(next_body)
    prior = read_tip(slug, files_root=files_root)
    assert prior is not None
    append_journal_record(
        slug,
        JournalRecord(
            prior_tip_sha=prior[1],
            tip_sha=new_sha,
            tip_body=next_body,
            seat="conductor",
            dispatch_id="d1",
            reason="G1 harvest",
            rows=("G1",),
            delta="G1 OPEN→DONE",
            written_at="2026-08-25T00:00:00+00:00",
        ),
        files_root=files_root,
    )
    tip_path = _tip_path(slug, files_root=files_root)
    tip_path.unlink()
    tip = read_tip(slug, files_root=files_root)
    assert tip is not None
    assert tip[0] == next_body
    assert tip[1] == new_sha
    assert walk_journal_to_tip(slug, files_root=files_root) == new_sha


def test_birth_crash_journal_present_tip_absent_read_tip_recovers(tmp_path: Path) -> None:
    files_root = tmp_path
    slug = "crash-birth"
    body = _sparse_body()
    expected_sha = tip_sha256(body)
    append_journal_record(
        slug,
        JournalRecord(
            prior_tip_sha=None,
            tip_sha=expected_sha,
            tip_body=body,
            seat="materializer",
            dispatch_id=None,
            reason="conductor spawn birth",
            rows=("G1", "G2"),
            delta="sparse birth",
            written_at="2026-08-25T00:00:00+00:00",
        ),
        files_root=files_root,
    )
    assert not _tip_path(slug, files_root=files_root).is_file()
    tip = read_tip(slug, files_root=files_root)
    assert tip is not None
    assert tip[0] == body
    assert tip[1] == expected_sha
    assert walk_journal_to_tip(slug, files_root=files_root) == expected_sha
