"""G5 nest pin: conductor dispatch_id line on nest hop (a:37920)."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.conductor_g5_nest_pin import pin_g5_nest_dispatch_id
from implement_admission.conductor_score_journal import (
    birth_scoreboard,
    read_tip,
    render_sparse_scoreboard,
)
from implement_admission.conductor_witness_table import _conductor_dispatch_id

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
