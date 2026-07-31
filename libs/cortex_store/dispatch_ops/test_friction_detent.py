"""Enqueue-time detent triage for friction follow-ons."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops._friction_detent import classify_friction_detent


@pytest.mark.offline
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {
                "claim": "[tool_error] post-check omits consult kwargs",
                "suggestion": (
                    "In libs/cortex_store/dispatch_ops/_todo_gate_distillation_impl.py"
                    "::_evaluate_from_persisted, pass consult_thread from attrs"
                ),
            },
            "closed",
        ),
        (
            {
                "claim": "[protocol] Stage-B flipped workflow_state=done early",
                "suggestion": (
                    "When todo.attendance=autonomous, leave workflow_state "
                    "in_progress until G6"
                ),
            },
            "closed",
        ),
        (
            {
                "claim": (
                    "[protocol] redesign the architecture for cross-agent scope "
                    "substrate change"
                ),
            },
            "wide",
        ),
        (
            {
                "claim": (
                    "[tool_error] gate returns false for judgment_required todos "
                    "with consult provenance — Fix: pass attrs in "
                    "`_evaluate_from_persisted`"
                ),
            },
            "closed",
        ),
        (
            {"claim": "[protocol] something feels off in the tick"},
            "standard",
        ),
        (
            {
                "claim": "[tool_error] parser rejected WIP=none in checkpoint_parse.py",
                "note": "silent starve",
            },
            "closed",
        ),
    ],
)
def test_classify_friction_detent(kwargs: dict, expected: str) -> None:
    assert classify_friction_detent(**kwargs) == expected
