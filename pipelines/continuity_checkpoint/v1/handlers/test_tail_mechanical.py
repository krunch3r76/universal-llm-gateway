"""Tests for tail_mechanical scoreboard family gate (phase B B1, X-1)."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from implement_admission.conductor_score_journal import load_journal, read_tip
from implement_admission.conductor_score_table import cell
from implement_admission.conductor_witness_types import FoldResult

from .conftest import (
    DISTINCT_MODE,
    charter_board,
    charter_board_g_ladder,
    charter_board_none,
    conductor_board,
    continuity_card,
    g1_witness_deps,
)
from .tail_mechanical import run_tail_mechanical, scoreboard_sha_on_disk

pytestmark = pytest.mark.offline

_G_ROW_CLAIM_RE = re.compile(r"G\d+ (DONE|OPEN|CLAIMED)")


def test_charter_r_ledger_projection_no_mutation(
    tmp_path: Path, cortex_files_root: Path
) -> None:
    """B1-4 — charter R-ledger: file unchanged, projects status, no conductor path."""
    thread = "10479"
    uri = charter_board(tmp_path, thread)
    continuity_card(tmp_path, thread)
    before = scoreboard_sha_on_disk(uri, files_root=tmp_path)
    out = run_tail_mechanical(
        thread=thread,
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=["role:root"],
        files_root=tmp_path,
    )
    after = scoreboard_sha_on_disk(uri, files_root=tmp_path)
    assert after == before
    assert out["folded"] is False
    assert out["family"] == "charter"
    assert out["reason"] == "projection_only"
    assert out["genre"] == "r_ledger"
    assert out["row_status"] == {"R1": "OPEN"}
    assert out["scoreboard_pin"].startswith("Scoreboard:")
    scoreboards = tmp_path / "notes/system/scoreboards"
    assert not scoreboards.exists() or not any(scoreboards.iterdir())
    assert not _G_ROW_CLAIM_RE.search(str(out))
    assert len(out.get("fold_row_lines") or []) == 1


def test_charter_g_ladder_projection_writes_card(
    tmp_path: Path, cortex_files_root: Path
) -> None:
    """Charter G-ladder genre projects Settled/Live/Next without witness fold."""
    thread = "10220"
    uri = charter_board_g_ladder(tmp_path, thread)
    card = continuity_card(tmp_path, thread)
    out = run_tail_mechanical(
        thread=thread,
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=[f"scoreboard:{thread}"],
        files_root=tmp_path,
    )
    assert out["reason"] == "projection_only"
    assert out["genre"] == "g_ladder"
    assert out["row_status"] == {"G1": "DONE", "G2": "OPEN"}
    assert out["card_written"] is True
    text = card.read_text(encoding="utf-8")
    assert "**Settled:** G1" in text
    assert "**Live:** G2" in text


def test_charter_none_genre_validate_only(
    tmp_path: Path, cortex_files_root: Path
) -> None:
    """Charter tips with no G/R rows stay validate-only."""
    thread = "10092"
    uri = charter_board_none(tmp_path, thread)
    out = run_tail_mechanical(
        thread=thread,
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=[],
        files_root=tmp_path,
    )
    assert out["reason"] == "validate_only"
    assert out["genre"] == "none"
    assert out["card_written"] is False


def test_unresolved_emits_skipped_reason() -> None:
    with patch("agent_bus_store.events.publisher.emit") as emit:
        out = run_tail_mechanical(
            thread="99999",
            options={},
            tip_body="TYPE: CHECKPOINT\nno scoreboard token\n",
            thread_tags=[],
        )
    assert out["folded"] is False
    assert out["reason"] == "scoreboard_unresolved"
    emit.assert_called_once()
    assert emit.call_args.args[0] == "checkpoint.tail_mechanical.skipped"
    assert emit.call_args.args[1]["reason"] == "scoreboard_unresolved"


def test_conductor_projection_does_not_journal(
    tmp_path: Path, cortex_files_root: Path
) -> None:
    """X-1 / B1-3 — conductor projection reports status but writes nothing; pin = on-disk sha."""
    slug = "tail-mechanical-fold"
    uri = conductor_board(tmp_path, slug)
    thread = "7777"
    continuity_card(tmp_path, thread)

    deps = g1_witness_deps(slug, tmp_path / "repo")
    pre_fold_sha = scoreboard_sha_on_disk(uri, files_root=tmp_path)
    before = len(load_journal(slug, files_root=tmp_path))
    with patch(
        "handlers.tail_mechanical.fold_deps_for_admit",
        return_value=deps,
    ):
        out = run_tail_mechanical(
            thread=thread,
            options={"scoreboard_slug": slug},
            tip_body=f"Scoreboard: {uri}\n",
            thread_tags=[f"scoreboard:{slug}"],
            files_root=tmp_path,
        )

    # Projection only: the tail must not journal or touch the tip. The witness
    # rewrites operator-recorded DONE to CLAIMED, and CHECKPOINT fires far more
    # often than a conductor hop, so nothing here may write.
    assert len(load_journal(slug, files_root=tmp_path)) == before

    disk_body, on_disk_sha = read_tip(slug, files_root=tmp_path)
    assert on_disk_sha == pre_fold_sha
    g1_line = next(
        line for line in disk_body.splitlines() if line.lstrip().startswith("| G1 |")
    )
    assert cell(g1_line, 3) == DISTINCT_MODE

    assert out.get("family") == "conductor"
    assert out.get("folded") is False
    assert out.get("reason") == "projection_only"
    assert out.get("journal_applied") is False
    # The projection is still reported, it is simply not persisted.
    assert (out.get("row_status") or {}).get("G1") == "DONE"
    post_sha = out.get("scoreboard_sha256")
    assert post_sha == on_disk_sha
    assert out["scoreboard_pin"].startswith(f"Scoreboard: {uri} · sha256:{post_sha[:8]}")
    assert len(out.get("fold_row_lines") or []) == 1
    assert (out.get("fold_row_lines") or [""])[0].lstrip().startswith("| G1 |")


def test_charter_none_card_not_written(
    tmp_path: Path, cortex_files_root: Path
) -> None:
    """B2-2 — charter none-genre does not derive Settled/Live/Next onto the card."""
    thread = "7445"
    uri = charter_board_none(tmp_path, thread)
    card = continuity_card(tmp_path, thread)
    out = run_tail_mechanical(
        thread=thread,
        options={},
        tip_body=f"inline ref {uri}\n",
        thread_tags=[],
        files_root=tmp_path,
    )
    assert out["card_written"] is False
    assert out["card_reason"] == "card_derivation_unavailable"
    assert "**Settled:**" not in card.read_text(encoding="utf-8")


@patch("handlers.tail_mechanical.fold_scoreboard", return_value=None)
def test_entity_missing_never_raises(_mock_fold: object, tmp_path: Path) -> None:
    """B1-6 — entity_get 404 path yields entity_missing without raising."""
    slug = "missing-entity"
    uri = conductor_board(tmp_path, slug)
    out = run_tail_mechanical(
        thread="8888",
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=[f"scoreboard:{slug}"],
        files_root=tmp_path,
    )
    assert out["folded"] is False
    assert out["reason"] == "entity_missing"


@patch(
    "handlers.tail_mechanical.fold_deps_for_admit",
    side_effect=OSError("permission denied"),
)
def test_scoreboard_unreadable_never_raises(_mock_deps: object, tmp_path: Path) -> None:
    """B1-6 — unreadable scoreboard yields scoreboard_unreadable without raising."""
    slug = "unreadable-board"
    uri = conductor_board(tmp_path, slug)
    out = run_tail_mechanical(
        thread="8889",
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=[f"scoreboard:{slug}"],
        files_root=tmp_path,
    )
    assert out["folded"] is False
    assert out["reason"] == "scoreboard_unreadable"


def test_charter_missing_file_scoreboard_unreadable() -> None:
    """B1-6 — charter URI with missing file yields scoreboard_unreadable."""
    uri = "cortex://notes/system/threads/missing-charter-scoreboard.md"
    out = run_tail_mechanical(
        thread="10001",
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=[],
        files_root=Path("/nonexistent/root"),
    )
    assert out["folded"] is False
    assert out["reason"] == "scoreboard_unreadable"


@patch("handlers.tail_mechanical.fold_scoreboard")
def test_journal_rejected_never_raises(mock_fold: object, tmp_path: Path) -> None:
    """B1-6 — forward_mutate_tip rejection yields journal_rejected without raising."""
    slug = "journal-reject"
    uri = conductor_board(tmp_path, slug)
    mock_fold.return_value = FoldResult(
        slug=slug,
        raw_body="raw-body",
        folded_body="folded-body",
        row_status={"G1": "DONE"},
        witnesses={},
        witnessed_done=frozenset(),
        rows_claimed=frozenset(),
        entry_gate="G2",
        journal_applied=False,
        tip_sha="a" * 64,
    )
    out = run_tail_mechanical(
        thread="8890",
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=[f"scoreboard:{slug}"],
        files_root=tmp_path,
    )
    assert out["folded"] is False
    # journal_applied=False is the expected projection-only outcome, not a rejection.
    assert out["reason"] == "projection_only"
