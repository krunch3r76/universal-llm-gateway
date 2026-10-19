"""Monotonic hop_seq resolution (Shape A AC6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conductor_hop_seq import resolve_next_hop_seq


@pytest.mark.offline
def test_monotonic_from_ledger_max() -> None:
    assert (
        resolve_next_hop_seq(
            ledger_max_hop_seq=15,
            prior_hop_seq=None,
            closeout_hop_seq=None,
        )
        == 16
    )


@pytest.mark.offline
def test_unresolved_when_all_missing() -> None:
    assert (
        resolve_next_hop_seq(
            ledger_max_hop_seq=None,
            prior_hop_seq=None,
            closeout_hop_seq=None,
        )
        is None
    )


@pytest.mark.offline
def test_fold_before_admit_body(tmp_path: Path) -> None:
    """Fold writes the tip; the admit body carries the post-fold sha."""
    import json

    from implement_admission.conductor_score_journal import (
        read_tip,
        render_sparse_scoreboard,
    )

    from .conductor_hop_body import build_hop_team_dispatch_body

    slug = "ac5-fold-before-admit"
    prior = render_sparse_scoreboard(
        source_ref=f"todo:{slug}",
        slug=slug,
        entry_gate="G2",
        stop_after=None,
        rows=("G1", "G2"),
    )
    tip = tmp_path / "notes" / "system" / "scoreboards" / f"{slug}-scoreboard.md"
    tip.parent.mkdir(parents=True)
    tip.write_text(prior, encoding="utf-8")
    before = read_tip(slug, files_root=tmp_path)
    assert before is not None
    nxt = prior + "\n<!-- folded -->\n"
    rec = {
        "source_ref": f"todo:{slug}",
        "contract": "conductor",
        "hop_seq": 2,
        "scoreboard_next_body": nxt,
    }
    row = {
        "contract": "conductor",
        "dispatch_id": "pred-ac5",
        "thread_id": "thread-ac5",
        "work_key": f"todo:{slug}",
        "record_json": json.dumps(rec),
        "terminal_status": "complete",
        "_files_root": str(tmp_path),
    }
    body = build_hop_team_dispatch_body(row)
    assert body is not None
    after = read_tip(slug, files_root=tmp_path)
    assert after is not None
    assert after[1] != before[1]
    assert body["generation_options"]["scoreboard_tip_sha"] == after[1]


@pytest.mark.offline
def test_summoning_house_not_applied() -> None:
    from pathlib import Path

    body_path = Path(__file__).with_name("conductor_hop_body.py")
    text = body_path.read_text(encoding="utf-8")
    assert "_summoning_house_model_fields" not in text
