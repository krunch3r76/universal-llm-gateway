"""Card-write acceptance tests for tail_mechanical (phase B B2)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from implement_admission.conductor_score_journal import birth_scoreboard

from ._card_patch import apply_card_patch, apply_fold_summary_to_card
from .conftest import continuity_card, g1_witness_deps
from .tail_mechanical import run_tail_mechanical

pytestmark = pytest.mark.offline


def _run_conductor_fold_with_card(
    tmp_path: Path,
    *,
    options: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    slug = "card-derivation"
    thread = "5555"
    card = continuity_card(tmp_path, thread)
    body = "\n".join(
        [
            "# Scoreboard",
            "",
            "## Gated deliverables",
            "",
            "| ID | Deliverable | Mode | Status | Stops |",
            "|---|---|---|---|---|",
            "| G1 | Architecture | plan | OPEN | |",
            "| G2 | Frame | — | OPEN | |",
            "| G3 | Densify | plan | OPEN | |",
        ]
    )
    birth_scoreboard(slug, scoreboard_body=body, files_root=tmp_path)
    uri = f"cortex://notes/system/scoreboards/{slug}-scoreboard.md"
    deps = g1_witness_deps(slug, tmp_path / "repo")
    with patch("handlers.tail_mechanical.fold_deps_for_admit", return_value=deps):
        out = run_tail_mechanical(
            thread=thread,
            options=options,
            tip_body=f"Scoreboard: {uri}\n",
            thread_tags=[f"scoreboard:{slug}"],
            files_root=tmp_path,
        )
    return out, card


def test_b2_1_card_settled_live_next_with_options_residue(cortex_files_root: Path) -> None:
    """B2-1 — options.residue supplied still writes fold-derived Settled/Live/Next."""
    out, card = _run_conductor_fold_with_card(
        cortex_files_root,
        options={"residue": "Mission: seat residue must not block card"},
    )
    text = card.read_text(encoding="utf-8")
    assert out.get("card_written") is True
    assert "**Settled:** G1" in text
    assert "**Live:** G2" in text
    assert "**Next:** G3" in text
    assert out["settled_live_next"]["settled"] == "G1"
    assert out["settled_live_next"]["live"] == "G2"
    assert out["settled_live_next"]["next"] == "G3"


def test_b2_1_card_settled_live_next_when_pre_consolidate_skipped(
    cortex_files_root: Path,
) -> None:
    """B2-1 — pre_consolidate-skipped path still writes fold-derived Settled/Live/Next."""
    out, card = _run_conductor_fold_with_card(cortex_files_root, options={})
    text = card.read_text(encoding="utf-8")
    assert out.get("card_written") is True
    assert "**Settled:** G1" in text
    assert "**Live:** G2" in text
    assert "**Next:** G3" in text


def test_b2_3_resume_open_and_fold_summary_both_intact(cortex_files_root: Path) -> None:
    """B2-3 — pre_consolidate Resume open patch and mechanical card write both survive."""
    thread = "3333"
    card = continuity_card(cortex_files_root, thread)
    card.write_text("# Continuity\n\n## Resume open\n\nold\n", encoding="utf-8")
    applied, _, reason = apply_card_patch(
        thread=thread,
        resume_open="server-resume-open-line",
        opportunities_rows=[],
    )
    assert applied is True
    assert reason == "ok"
    written, _, card_reason = apply_fold_summary_to_card(
        thread=thread,
        settled="G1",
        live="G2",
        next_row="G3",
    )
    assert written is True
    assert card_reason == "ok"
    text = card.read_text(encoding="utf-8")
    assert "server-resume-open-line" in text
    assert "**Settled:** G1" in text
    assert "**Live:** G2" in text
    assert "**Next:** G3" in text
