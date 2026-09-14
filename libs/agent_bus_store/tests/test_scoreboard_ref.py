"""Tests for scoreboard_ref resolver (phase B B1-1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_bus_store.scoreboard_ref import resolve_scoreboard_ref
from implement_admission.conductor_score_journal import scoreboard_tip_uri
from implement_admission.conductor_score_locus import charter_locus

pytestmark = pytest.mark.offline


def _write_charter(files_root: Path, thread_id: str) -> str:
    locus = charter_locus(thread_id, files_root=files_root)
    locus.tip_path.parent.mkdir(parents=True, exist_ok=True)
    locus.tip_path.write_text("# charter\n", encoding="utf-8")
    return locus.tip_uri


def _write_conductor(files_root: Path, slug: str, *, with_journal: bool = True) -> str:
    tip = files_root / "notes/system/scoreboards" / f"{slug}-scoreboard.md"
    tip.parent.mkdir(parents=True, exist_ok=True)
    tip.write_text("# board\n", encoding="utf-8")
    if with_journal:
        journal = files_root / "notes/system/scoreboards" / f"{slug}-score-journal.md"
        journal.write_text("[]\n", encoding="utf-8")
    return scoreboard_tip_uri(slug)


def test_precedence_options_uri_over_body(tmp_path: Path) -> None:
    charter_uri = "cortex://notes/system/threads/10479-charter-scoreboard.md"
    path = tmp_path / "notes/system/threads/10479-charter-scoreboard.md"
    path.parent.mkdir(parents=True)
    path.write_text("# charter\n", encoding="utf-8")
    ref = resolve_scoreboard_ref(
        options={"scoreboard_uri": charter_uri},
        tip_body=f"Scoreboard: {_write_conductor(tmp_path, 'ignored')}\n",
        thread_tags=[],
        files_root=tmp_path,
    )
    assert ref is not None
    assert ref.uri == charter_uri
    assert ref.family == "charter"


def test_threads_uri_is_charter_family(tmp_path: Path) -> None:
    uri = "cortex://notes/system/threads/10479-charter-scoreboard.md"
    path = tmp_path / uri.removeprefix("cortex://")
    path.parent.mkdir(parents=True)
    path.write_text("# charter\n", encoding="utf-8")
    ref = resolve_scoreboard_ref(
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=[],
        files_root=tmp_path,
    )
    assert ref is not None
    assert ref.family == "charter"


def test_scoreboard_uri_with_journal_is_conductor(tmp_path: Path) -> None:
    uri = _write_conductor(tmp_path, "phase-b-slug")
    ref = resolve_scoreboard_ref(
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=[],
        files_root=tmp_path,
    )
    assert ref is not None
    assert ref.family == "conductor"
    assert ref.slug == "phase-b-slug"


def test_scoreboard_uri_without_journal_is_charter(tmp_path: Path) -> None:
    uri = _write_conductor(tmp_path, "no-journal", with_journal=False)
    ref = resolve_scoreboard_ref(
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=[],
        files_root=tmp_path,
    )
    assert ref is not None
    assert ref.family == "charter"


def test_no_thread_derivation_returns_none() -> None:
    assert (
        resolve_scoreboard_ref(
            options={},
            tip_body="",
            thread_tags=[],
        )
        is None
    )


def test_scoreboard_tag_resolves_charter_when_file_exists(tmp_path: Path) -> None:
    uri = _write_charter(tmp_path, "5250")
    ref = resolve_scoreboard_ref(
        options={},
        tip_body="",
        thread_tags=["scoreboard:5250"],
        files_root=tmp_path,
    )
    assert ref is not None
    assert ref.uri == uri
    assert ref.family == "charter"
    assert ref.sha256 is not None


def test_bare_slug_line_resolves_charter_when_file_exists(tmp_path: Path) -> None:
    uri = _write_charter(tmp_path, "5250")
    ref = resolve_scoreboard_ref(
        options={},
        tip_body="Scoreboard: 5250",
        thread_tags=[],
        files_root=tmp_path,
    )
    assert ref is not None
    assert ref.uri == uri
    assert ref.family == "charter"
    assert ref.sha256 is not None


def test_guess_returns_none_when_neither_candidate_exists(tmp_path: Path) -> None:
    assert (
        resolve_scoreboard_ref(
            options={},
            tip_body="Scoreboard: missing-slug",
            thread_tags=["scoreboard:missing-slug"],
            files_root=tmp_path,
        )
        is None
    )


def test_conductor_slug_still_resolves_with_journal(tmp_path: Path) -> None:
    uri = _write_conductor(tmp_path, "phase-b-slug")
    ref = resolve_scoreboard_ref(
        options={},
        tip_body="",
        thread_tags=["scoreboard:phase-b-slug"],
        files_root=tmp_path,
    )
    assert ref is not None
    assert ref.uri == uri
    assert ref.family == "conductor"
    assert ref.sha256 is not None


def test_explicit_missing_uri_still_returned_with_sha256_none(tmp_path: Path) -> None:
    missing = "cortex://notes/system/threads/9999-charter-scoreboard.md"
    ref = resolve_scoreboard_ref(
        options={"scoreboard_uri": missing},
        tip_body="",
        thread_tags=[],
        files_root=tmp_path,
    )
    assert ref is not None
    assert ref.uri == missing
    assert ref.sha256 is None


def test_whole_body_token_without_scoreboard_line(tmp_path: Path) -> None:
    uri = "cortex://notes/system/threads/7445-charter-scoreboard.md"
    path = tmp_path / uri.removeprefix("cortex://")
    path.parent.mkdir(parents=True)
    path.write_text("# charter\n", encoding="utf-8")
    ref = resolve_scoreboard_ref(
        options={},
        tip_body=f"WIP — see {uri} for gate state.\n",
        thread_tags=[],
        files_root=tmp_path,
    )
    assert ref is not None
    assert ref.uri == uri
