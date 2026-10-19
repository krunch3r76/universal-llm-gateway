"""Shape A AC4 — hop-class seat selection in conductor defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
from implement_admission.conductor_policy_snapshot import (
    default_knobs_from_route_policy,
)
from implement_admission.conductor_score_journal import render_sparse_scoreboard

from .conductor_defaults import evaluate_conductor_defaults
from .validate import evaluate_validate


def _seed_tip(tmp_path: Path, slug: str) -> Path:
    body = render_sparse_scoreboard(
        source_ref=f"todo:{slug}",
        slug=slug,
        entry_gate="G2",
        stop_after=None,
        rows=("G1", "G2", "G3"),
    )
    tip_path = tmp_path / "notes" / "system" / "scoreboards" / f"{slug}-scoreboard.md"
    tip_path.parent.mkdir(parents=True, exist_ok=True)
    tip_path.write_text(body, encoding="utf-8")
    return tmp_path


def _run(opts: dict) -> dict:
    v = evaluate_validate(opts)
    return evaluate_conductor_defaults(opts, v)


@pytest.mark.parametrize(
    "verb",
    ["reply-fold", "child-recheck", "harvest", "fold"],
)
@pytest.mark.offline
def test_mechanical_verbs(verb: str, tmp_path: Path) -> None:
    snap = default_knobs_from_route_policy()
    root = _seed_tip(tmp_path, "ac4-mechanical")
    plan = _run(
        {
            "op": "generate",
            "job": "conductor",
            "seat": "cursor-sdk",
            "hop_seq": 3,
            "next_admit_verb": verb,
            "source_ref": "todo:ac4-mechanical",
            "files_root": str(root),
            "_test_todo_attrs": {"name": "ac4"},
            "_skip_packet_render": True,
        }
    )
    assert plan.get("selected_seat_role") == "mechanical"
    assert plan.get("model") == snap.mechanical["model"]


@pytest.mark.offline
def test_judgment_and_birth_use_driver(tmp_path: Path) -> None:
    snap = default_knobs_from_route_policy()
    slug = "ac4-driver"
    root = _seed_tip(tmp_path, slug)
    cases = (
        {"hop_seq": 0, "next_admit_verb": None, "files_root": "/tmp"},
        {"hop_seq": 2, "next_admit_verb": "judgment", "files_root": str(root)},
    )
    for extra in cases:
        plan = _run(
            {
                "op": "generate",
                "job": "conductor",
                "seat": "cursor-sdk",
                "source_ref": f"todo:{slug}",
                "_test_todo_attrs": {"name": "ac4"},
                "_skip_packet_render": True,
                **extra,
            }
        )
        assert plan.get("selected_seat_role") == "driver"
        assert plan.get("model") == snap.driver["model"]
