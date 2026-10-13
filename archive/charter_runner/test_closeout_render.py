"""Closeout render — table, empty ledger, aggregation."""

from __future__ import annotations

from scripts.model_manager.ui.controller.charter_runner.closeout_render import (
    render_closeout,
    render_frictions_table,
)
from scripts.model_manager.ui.controller.charter_runner.friction_ledger import (
    FrictionLedgerRow,
)


def test_empty_ledger_renders_sentinel() -> None:
    assert render_frictions_table([]) == "_No frictions this arc._"


def test_frictions_table_shows_per_row_status() -> None:
    table = render_frictions_table(
        [
            FrictionLedgerRow(
                assertion_id=26301,
                note="gate bypass",
                enroll_state="queued",
                todo_slug="todo:friction-26301-gate",
            ),
            FrictionLedgerRow(
                assertion_id=26302,
                note="skipped audit",
                enroll_state="opted_out",
            ),
        ]
    )
    assert "| 26301 | gate bypass | **queued** — `todo:friction-26301-gate` |" in table
    assert "| 26302 | skipped audit | opted_out |" in table


def test_closeout_includes_plain_sections_and_machine_comment() -> None:
    body = render_closeout(
        root_id="5624",
        root_subject="path-sim todo",
        window_count=2,
        reason="no_gated_pickup",
        what_happened="Window 1 advanced G2.\n\nWindow 2 blocked on consult.",
        where_left="**Next pickup:**\n- G3 — R-admit",
        ledger=[
            FrictionLedgerRow(
                assertion_id=99,
                note="example",
                enroll_state="filed_only",
            )
        ],
        checkpoint_turn=42,
    )
    assert "## What happened" in body
    assert "Window 1 advanced G2." in body
    assert "## Frictions" in body
    assert "| 99 | example | filed only |" in body
    assert "<!-- machine:" in body
    assert "checkpoint_turn=42" in body
    assert "a:26216" not in body
