"""Tests for tail_mechanical scoreboard family gate (phase B B1–B2, X-1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from implement_admission.conductor_score_journal import birth_scoreboard, load_journal

from .tail_mechanical import run_tail_mechanical, scoreboard_sha_on_disk

pytestmark = pytest.mark.offline


def _charter_board(files_root: Path, thread: str) -> str:
    uri = f"cortex://notes/system/threads/{thread}-charter-scoreboard.md"
    path = files_root / uri.removeprefix("cortex://")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Charter\n\n## Rows\n\n| R1 | item | todo:a | OPEN | |\n",
        encoding="utf-8",
    )
    return uri


def _conductor_board(files_root: Path, slug: str) -> str:
    body = "\n".join(
        [
            "# Scoreboard",
            "",
            "## Gated deliverables",
            "",
            "| ID | Deliverable | Mode | Status | Stops |",
            "|---|---|---|---|---|",
            "| G1 | Architecture | — | OPEN | |",
        ]
    )
    birth_scoreboard(slug, scoreboard_body=body, files_root=files_root)
    return f"cortex://notes/system/scoreboards/{slug}-scoreboard.md"


def test_charter_validate_only_no_mutation(tmp_path: Path) -> None:
    thread = "10479"
    uri = _charter_board(tmp_path, thread)
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
    assert out["reason"] == "validate_only"
    assert out["scoreboard_pin"].startswith("Scoreboard:")
    scoreboards = tmp_path / "notes/system/scoreboards"
    assert not scoreboards.exists() or not any(scoreboards.iterdir())


def test_unresolved_emits_skipped_reason() -> None:
    out = run_tail_mechanical(
        thread="99999",
        options={},
        tip_body="TYPE: CHECKPOINT\nno scoreboard token\n",
        thread_tags=[],
    )
    assert out["folded"] is False
    assert out["reason"] == "scoreboard_unresolved"


def test_conductor_fold_journals_once(tmp_path: Path) -> None:
    from implement_admission.conductor_witness import FoldDeps

    slug = "tail-mechanical-fold"
    uri = _conductor_board(tmp_path, slug)
    card = tmp_path / "notes/system/threads/7777-continuity.md"
    card.parent.mkdir(parents=True)
    card.write_text("# card\n", encoding="utf-8")

    class _Cortex:
        def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN001, ANN003
            return {"id": entity_id, "attributes": {"density_triage": "mechanical"}}

        def list_relationships(self, entity_id: str, *, type_id: str | None = None):  # noqa: ARG002
            return []

    deps = FoldDeps(
        cortex=_Cortex(),
        bus=None,
        git=None,
        source_ref=f"todo:{slug}",
        repo=tmp_path,
    )
    before = len(load_journal(slug, files_root=tmp_path))
    with patch(
        "handlers.tail_mechanical.fold_deps_for_admit",
        return_value=deps,
    ):
        out = run_tail_mechanical(
            thread="7777",
            options={"scoreboard_slug": slug},
            tip_body=f"Scoreboard: {uri}\n",
            thread_tags=[f"scoreboard:{slug}"],
            files_root=tmp_path,
        )
    assert out.get("family") == "conductor"
    assert out.get("folded") is True
    assert out.get("scoreboard_sha256")
    assert len(load_journal(slug, files_root=tmp_path)) >= before


def test_charter_card_not_written(tmp_path: Path) -> None:
    thread = "7445"
    uri = _charter_board(tmp_path, thread)
    card = tmp_path / f"notes/system/threads/{thread}-continuity.md"
    card.write_text("# card\n", encoding="utf-8")
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
    slug = "missing-entity"
    uri = _conductor_board(tmp_path, slug)
    out = run_tail_mechanical(
        thread="8888",
        options={},
        tip_body=f"Scoreboard: {uri}\n",
        thread_tags=[f"scoreboard:{slug}"],
        files_root=tmp_path,
    )
    assert out["folded"] is False
    assert out["reason"] == "entity_missing"
