"""cursor-auto admit injects reasoning-posture on judgment handoff contracts only."""

from __future__ import annotations

from reasoning_posture_contracts import REASONING_POSTURE_PREAMBLE

from services.git_integration_worker.cursor_auto.admit_report import (
    build_admit_report_body,
)


def _minimal_admit_kwargs(**overrides: object) -> dict:
    base = {
        "model": {
            "requested": "auto",
            "resolved_model_id": "cursor/composer-2.5",
            "honored": True,
        },
        "effort": {"requested": "medium", "resolved_effort": "medium"},
        "escalation": {"requested": None, "resolved_escalation": None},
        "contract": "investigate",
        "handoff_contract": "light-bounded",
    }
    base.update(overrides)
    return base


def test_admit_injects_reasoning_posture_on_judgment_handoff() -> None:
    body = build_admit_report_body(**_minimal_admit_kwargs())
    assert REASONING_POSTURE_PREAMBLE in body


def test_admit_skips_reasoning_posture_on_mechanical_handoff() -> None:
    body = build_admit_report_body(
        **_minimal_admit_kwargs(
            contract="implement",
            handoff_contract="pure-mechanical",
        )
    )
    assert "reasoning-posture" not in body
