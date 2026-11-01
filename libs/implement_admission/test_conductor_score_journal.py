"""Hermetic tests for conductor scoreboard journal."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.conductor_score_journal import (
    WITNESS_CLEAR_REASON,
    JournalRecord,
    _tip_path,
    append_journal_record,
    birth_scoreboard,
    effective_retracted_rows_from_journal,
    forward_mutate_tip,
    load_journal,
    read_tip,
    reject_rewind_closed_row,
    retract_state_from_journal,
    tip_sha256,
    walk_journal_to_tip,
    witnessed_done_from_journal,
)

pytestmark = pytest.mark.offline


def _sparse_body() -> str:
    return "\n".join(
        [
            "# Scoreboard — todo:foo",
            "",
            "| ID | Deliverable | Mode | Status | Stops |",
            "|---|---|---|---|---|",
            "| G1 | Architecture | — | OPEN | |",
            "| G2 | Frame | — | OPEN | |",
        ]
    )


def _g1_done_body() -> str:
    return "\n".join(
        [
            "# Scoreboard — todo:foo",
            "",
            "| ID | Deliverable | Mode | Status | Stops |",
            "|---|---|---|---|---|",
            "| G1 | Architecture | — | DONE | |",
            "| G2 | Frame | — | OPEN | |",
        ]
    )


def test_journal_sparse_to_two_mutations_tip_walk(tmp_path: Path) -> None:
    files_root = tmp_path
    slug = "foo"
    birth_sha = birth_scoreboard(
        slug, scoreboard_body=_sparse_body(), files_root=files_root
    )
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
        next_body=_g1_done_body().replace(
            "G2 | Frame | OPEN", "G2 | Frame | WIP(conductor)"
        ),
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


def test_parse_journal_ndjson_lines(tmp_path: Path) -> None:
    files_root = tmp_path
    slug = "ndjson-journal"
    journal_path = files_root / "notes/system/scoreboards" / f"{slug}-score-journal.md"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(
        '{"reason":"birth","tip_sha":"a"}\n{"reason":"mutate","tip_sha":"b"}\n',
        encoding="utf-8",
    )
    records = load_journal(slug, files_root=files_root)
    assert len(records) == 2


def test_reject_rewind_witnessed_done_to_claimed() -> None:
    prior = _g1_done_body()
    next_body = prior.replace("DONE", "CLAIMED")
    reason = reject_rewind_closed_row(
        prior_body=prior,
        next_body=next_body,
        prior_witnessed_done=frozenset({"G1"}),
    )
    assert reason is not None


def test_unwitnessed_done_to_claimed_allowed() -> None:
    reason = reject_rewind_closed_row(
        prior_body=_g1_done_body(),
        next_body=_g1_done_body().replace("DONE", "CLAIMED"),
        prior_witnessed_done=frozenset(),
    )
    assert reason is None


def test_reject_rewind_closed_row_unit() -> None:
    reason = reject_rewind_closed_row(
        prior_body=_g1_done_body(), next_body=_sparse_body()
    )
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


def test_birth_crash_journal_present_tip_absent_read_tip_recovers(
    tmp_path: Path,
) -> None:
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


@pytest.mark.offline
def test_ac_p2_5_journal_stops_block_reason(tmp_path: Path) -> None:
    """AC-P2-5 — journal delta carries Stops reason when G4 DONE re-renders CLAIMED."""
    files_root = tmp_path
    slug = "stops-journal"
    tip_body = "\n".join(
        [
            "# Scoreboard — todo:stops-journal",
            "",
            "## Gated deliverables",
            "",
            "| ID | Deliverable | Status | Stops |",
            "|---|---|---|---|",
            "| G4 | Skeptic | DONE | ROW_PINNED |",
            "",
        ]
    )
    birth_scoreboard(slug, scoreboard_body=tip_body, files_root=files_root)
    from implement_admission.conductor_witness import FoldDeps, fold_scoreboard

    class _Cortex:
        def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ANN201
            return {
                "id": entity_id,
                "attributes": {"density_triage": "judgment_required"},
            }

        def list_relationships(self, entity_id: str, *, type_id: str | None = None):  # noqa: ARG002
            return []

    deps = FoldDeps(
        cortex=_Cortex(),
        bus=None,
        git=None,
        source_ref="todo:stops-journal",
        repo=tmp_path / "repo",
    )
    fold = fold_scoreboard(slug, deps=deps, files_root=files_root, write_journal=True)
    assert fold is not None
    assert fold.row_status["G4"] == "DONE"
    assert "G4" not in fold.witnessed_done
    assert fold.blocked_rows.get("G4") == "ROW_PINNED" or "ROW_PINNED" in (
        fold.missing_witnesses.get("G4") or ""
    )


def _charter_body(*, status: str = "OPEN") -> str:
    return "\n".join(
        [
            "# 10479 — charter scoreboard",
            "",
            "## Rows",
            "",
            "| # | row | work_key | status | evidence | next |",
            "|---|---|---|---|---|---|",
            f"| R1 | harvest | agent-bus:10479 | {status} | | |",
        ]
    )


def test_charter_locus_round_trip_recovers_from_journal(tmp_path: Path) -> None:
    from implement_admission.conductor_score_io import (
        birth_scoreboard_at,
        forward_mutate_tip_at,
        read_tip_at,
    )
    from implement_admission.conductor_score_locus import charter_locus

    locus = charter_locus("10479", files_root=tmp_path)
    birth_scoreboard_at(locus, scoreboard_body=_charter_body())
    next_body = _charter_body(status="**DONE — LANDED + LIVE**")
    mutated = forward_mutate_tip_at(
        locus,
        next_body=next_body,
        seat="cursor",
        dispatch_id="d1",
        reason="checkpoint",
        rows=(),
        delta="seat fold",
    )
    assert mutated.rejected_reason is None
    locus.tip_path.unlink()
    recovered = read_tip_at(locus)
    assert recovered is not None
    assert recovered[1] == mutated.tip_sha
    assert "**DONE — LANDED + LIVE**" in recovered[0]


def test_forward_mutate_tip_rejects_stale_expected_prior_tip_sha(
    tmp_path: Path,
) -> None:
    files_root = tmp_path
    slug = "tip-cas"
    birth_scoreboard(slug, scoreboard_body=_sparse_body(), files_root=files_root)
    prior = read_tip(slug, files_root=files_root)
    assert prior is not None
    stale_sha = "0" * 64
    result = forward_mutate_tip(
        slug,
        next_body=_g1_done_body(),
        seat="fold",
        dispatch_id=None,
        reason="witness_fold",
        rows=("G1",),
        delta="G1 OPEN→DONE",
        files_root=files_root,
        expected_prior_tip_sha=stale_sha,
    )
    assert result.rejected_reason == "tip_cas_mismatch"
    tip = read_tip(slug, files_root=files_root)
    assert tip is not None
    assert tip[1] == prior[1]


def test_witnessed_done_journal_union_survives_fold(tmp_path: Path) -> None:
    """Rewind guard uses journal union, not only the current fold witnesses."""
    files_root = tmp_path
    slug = "witnessed-union"
    birth_scoreboard(slug, scoreboard_body=_g1_done_body(), files_root=files_root)
    append_journal_record(
        slug,
        JournalRecord(
            prior_tip_sha=read_tip(slug, files_root=files_root)[1],
            tip_sha=tip_sha256(_g1_done_body()),
            tip_body=_g1_done_body(),
            seat="fold",
            dispatch_id=None,
            reason="witness_fold",
            rows=("G1",),
            delta="G1 witnessed",
            written_at="2026-10-18T00:00:00+00:00",
            witnessed_done=("G1",),
        ),
        files_root=files_root,
    )
    next_body = _g1_done_body().replace("DONE", "CLAIMED")
    result = forward_mutate_tip(
        slug,
        next_body=next_body,
        seat="fold",
        dispatch_id=None,
        reason="witness_fold",
        rows=("G1",),
        delta="attempt demote",
        files_root=files_root,
        prior_witnessed_done=frozenset({"G1"}),
    )
    assert result.rejected_reason is not None
    assert "rewind" in (result.rejected_reason or "").lower()


def test_charter_locus_does_not_reject_bolded_status(tmp_path: Path) -> None:
    from implement_admission.conductor_score_io import (
        birth_scoreboard_at,
        forward_mutate_tip_at,
    )
    from implement_admission.conductor_score_locus import charter_locus

    locus = charter_locus("10479", files_root=tmp_path)
    birth_scoreboard_at(locus, scoreboard_body=_charter_body(status="DONE"))
    result = forward_mutate_tip_at(
        locus,
        next_body=_charter_body(status="**DONE — LANDED + LIVE**"),
        seat="cursor",
        dispatch_id="d1",
        reason="checkpoint",
        rows=(),
        delta="emphasis",
    )
    assert result.rejected_reason is None


@pytest.mark.offline
def test_retract_clear_lifts_retract_state() -> None:
    """AC1 — witness_clear after retract removes G4 from retract set."""
    records = [
        {
            "reason": "witness_retract",
            "rows": ["G4"],
            "written_at": "2026-10-01T10:00:00+00:00",
        },
        {
            "reason": WITNESS_CLEAR_REASON,
            "rows": ["G4"],
            "written_at": "2026-10-01T11:00:00+00:00",
        },
    ]
    assert retract_state_from_journal(records) == frozenset()
    assert effective_retracted_rows_from_journal(
        records, live_witnessed=frozenset({"G4"})
    ) == frozenset()


@pytest.mark.offline
def test_stale_witness_clear_leaves_retract() -> None:
    """AC2 — clear before latest retract is ignored."""
    records = [
        {
            "reason": WITNESS_CLEAR_REASON,
            "rows": ["G4"],
            "written_at": "2026-10-01T09:00:00+00:00",
        },
        {
            "reason": "witness_retract",
            "rows": ["G4"],
            "written_at": "2026-10-01T10:00:00+00:00",
        },
    ]
    assert retract_state_from_journal(records) == frozenset({"G4"})


@pytest.mark.offline
def test_retract_clear_retract_latest_wins() -> None:
    """AC3 — second retract after clear re-sticks the row."""
    records = [
        {
            "reason": "witness_retract",
            "rows": ["G4"],
            "written_at": "2026-10-01T10:00:00+00:00",
        },
        {
            "reason": WITNESS_CLEAR_REASON,
            "rows": ["G4"],
            "written_at": "2026-10-01T11:00:00+00:00",
        },
        {
            "reason": "witness_retract",
            "rows": ["G4"],
            "written_at": "2026-10-01T12:00:00+00:00",
        },
    ]
    assert retract_state_from_journal(records) == frozenset({"G4"})


@pytest.mark.offline
def test_witnessed_done_respects_retract_clear_order() -> None:
    records = [
        {
            "reason": "witness_fold",
            "witnessed_done": ["G4"],
            "written_at": "2026-10-01T08:00:00+00:00",
        },
        {
            "reason": "witness_retract",
            "rows": ["G4"],
            "written_at": "2026-10-01T10:00:00+00:00",
        },
        {
            "reason": WITNESS_CLEAR_REASON,
            "rows": ["G4"],
            "written_at": "2026-10-01T11:00:00+00:00",
        },
        {
            "reason": "witness_fold",
            "witnessed_done": ["G4"],
            "written_at": "2026-10-01T12:00:00+00:00",
        },
    ]
    assert witnessed_done_from_journal(records) == frozenset({"G4"})
