"""Goal escalation ladder — dispatch vs STAY for repo-write and design goals."""

from __future__ import annotations

from bus_watch.goal_escalation import (
    classify_goal,
    escalation_target,
    format_dispatch_instruction,
    needs_escalation_dispatch,
)
from bus_watch.induction import build_wake_induction


def test_classify_repo_write_goal() -> None:
    row = "G26 — hoist now_row · [implement] · files_expected: friction_rows.py"
    assert classify_goal(row) == "repo_write"
    assert escalation_target("repo_write") == "cursor-auto"


def test_classify_design_goal() -> None:
    row = "G27 — observer-loss discriminator · design · consult_role: judgment_gap"
    assert classify_goal(row) == "design"
    assert escalation_target("design") == "cdp/fable"


def test_repo_write_emits_dispatch_not_stay() -> None:
    row = "G26 — [implement] hoist now_row onto full row list"
    digest = {
        "ts": "2026-09-19T18:00:00Z",
        "root": {"id": "10479", "turns": 385},
        "register": "autonomous",
        "attention": [],
        "watchers_complete_unrelayed": [],
        "budget": {"stop_class": None},
        "checkpoint_due": False,
        "changed_since_last_tick": True,
        "summary_row": row,
        "policy": {"now_row": row},
    }
    text = build_wake_induction(digest)
    assert needs_escalation_dispatch(row)
    assert "Dispatch:" in text
    assert "cursor-auto" in text
    assert "cursor-auto" in text or "cdp/fable" in text


def test_design_goal_emits_fable_dispatch() -> None:
    row = "G27 — design discriminator for observer-loss vs no_episode death"
    instruction = format_dispatch_instruction("design", row, root_id="10479")
    assert instruction == "Dispatch cdp/fable (judgment fork)"
    digest = {
        "ts": "2026-09-19T18:00:00Z",
        "root": {"id": "10479", "turns": 385},
        "register": "autonomous",
        "attention": [],
        "watchers_complete_unrelayed": [],
        "budget": {"stop_class": None},
        "checkpoint_due": False,
        "changed_since_last_tick": True,
        "policy": {"now_row": row},
    }
    text = build_wake_induction(digest)
    assert "Dispatch:" in text
    assert "cdp/fable" in text
