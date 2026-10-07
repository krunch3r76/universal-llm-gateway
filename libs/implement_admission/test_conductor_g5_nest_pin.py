"""G5 nest pin: conductor dispatch_id line on nest hop (a:37920)."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.conductor_g5_nest_pin import pin_g5_nest_dispatch_id
from implement_admission.conductor_score_io import (
    JournalRecord,
    append_journal_record_at,
)
from implement_admission.conductor_score_journal import (
    G_ROWS,
    birth_scoreboard,
    read_tip,
    render_sparse_scoreboard,
)
from implement_admission.conductor_score_locus import work_item_locus
from implement_admission.conductor_witness_table import (
    _conductor_dispatch_id,
    _g3_journal_written_at,
)

pytestmark = pytest.mark.offline

_PARENT = "2f217a394114-c1a03488"
_SLUG = "g5-nest-pin-fixture"


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "lane"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "pin@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "pin"], cwd=repo, check=True)
    (repo / "f").write_text("1\n", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "c1"], cwd=repo, check=True)
    return repo


def test_pin_writes_dispatch_id_leaves_l1_pending(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    repo = _git_repo(tmp_path)
    body = render_sparse_scoreboard(
        source_ref=f"todo:{_SLUG}",
        slug=_SLUG,
        entry_gate="G5",
        stop_after=None,
    )
    birth_scoreboard(_SLUG, scoreboard_body=body, files_root=files_root)
    pinned = pin_g5_nest_dispatch_id(
        parent_dispatch_id=_PARENT,
        parent_contract="conductor",
        child_contract="implement",
        work_key=f"todo:{_SLUG}",
        source_repo=str(repo),
        files_root=files_root,
    )
    assert pinned == _PARENT
    tip = read_tip(_SLUG, files_root=files_root)
    assert tip is not None
    assert _conductor_dispatch_id(tip[0]) == _PARENT
    assert "(pending)" in next(
        line for line in tip[0].splitlines() if line.startswith("| L1 |")
    )


def test_pin_skips_without_tip(tmp_path: Path) -> None:
    assert (
        pin_g5_nest_dispatch_id(
            parent_dispatch_id=_PARENT,
            parent_contract="conductor",
            child_contract="implement",
            work_key="todo:missing-scoreboard",
            source_repo=None,
            files_root=tmp_path / "cortex",
        )
        is None
    )


def test_pin_skips_non_conductor_parent(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    body = render_sparse_scoreboard(
        source_ref=f"todo:{_SLUG}",
        slug=_SLUG,
        entry_gate="G5",
        stop_after=None,
    )
    birth_scoreboard(_SLUG, scoreboard_body=body, files_root=files_root)
    assert (
        pin_g5_nest_dispatch_id(
            parent_dispatch_id=_PARENT,
            parent_contract="none",
            child_contract="implement",
            work_key=f"todo:{_SLUG}",
            source_repo=None,
            files_root=files_root,
        )
        is None
    )


def test_pin_idempotent_when_line_present(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    body = render_sparse_scoreboard(
        source_ref=f"todo:{_SLUG}",
        slug=_SLUG,
        entry_gate="G5",
        stop_after=None,
    )
    birth_scoreboard(_SLUG, scoreboard_body=body, files_root=files_root)
    first = pin_g5_nest_dispatch_id(
        parent_dispatch_id=_PARENT,
        parent_contract="conductor",
        child_contract="implement",
        work_key=f"todo:{_SLUG}",
        source_repo=None,
        files_root=files_root,
    )
    second = pin_g5_nest_dispatch_id(
        parent_dispatch_id=_PARENT,
        parent_contract="conductor",
        child_contract="implement",
        work_key=f"todo:{_SLUG}",
        source_repo=None,
        files_root=files_root,
    )
    assert first == second == _PARENT


def test_g3_cutoff_skips_nest_pin_all_row_stamp(tmp_path: Path) -> None:
    """A nest-pin journal that lists every G-row is not a new G3 cutoff."""
    files_root = tmp_path / "cortex"
    slug = "g3-cutoff-nest-pin"
    body = "tip\n"
    birth_scoreboard(slug, scoreboard_body=body, files_root=files_root, rows=("G1",))
    locus = work_item_locus(slug, files_root=files_root)
    g3_at = "2026-10-03T06:13:01.334149+00:00"
    pin_at = "2026-10-07T04:06:15.480904+00:00"
    append_journal_record_at(
        locus,
        JournalRecord(
            prior_tip_sha="a" * 64,
            tip_sha="b" * 64,
            tip_body=body,
            seat="fold",
            dispatch_id=None,
            reason="witness_fold",
            rows=("G3",),
            delta="G3 OPEN→DONE",
            written_at=g3_at,
        ),
    )
    append_journal_record_at(
        locus,
        JournalRecord(
            prior_tip_sha="b" * 64,
            tip_sha="c" * 64,
            tip_body=body,
            seat="giw",
            dispatch_id=_PARENT,
            reason="g5 nest pin conductor dispatch_id",
            rows=G_ROWS,
            delta="conductor dispatch_id",
            written_at=pin_at,
        ),
    )
    assert _g3_journal_written_at(slug, files_root=files_root) == g3_at
    later = "2026-10-07T05:00:00.000000+00:00"
    append_journal_record_at(
        locus,
        JournalRecord(
            prior_tip_sha="c" * 64,
            tip_sha="d" * 64,
            tip_body=body,
            seat="cursor-sdk",
            dispatch_id=None,
            reason="densify S4b",
            rows=("G3",),
            delta="S4b item 3",
            written_at=later,
        ),
    )
    assert _g3_journal_written_at(slug, files_root=files_root) == later
