"""Admit plane must not echo a resolved effort rung on effort-less models."""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.admit_report import (
    admit_plane_resolved_effort,
    build_admit_report_body,
)


def _minimal_admit_kwargs(**overrides: object) -> dict:
    base = {
        "model": {
            "requested": "auto",
            "resolved_model_id": "cursor/composer-2.5",
            "honored": False,
        },
        "effort": {"requested": "xhigh", "resolved_effort": "xhigh"},
        "escalation": {"requested": None, "resolved_escalation": None},
        "contract": "implement",
        "handoff_contract": "pure-mechanical",
        "override_rule": (
            "model_override_rule: auto chose cursor/composer-2.5 via "
            "workflows.mechanical_implement for contract=implement"
        ),
    }
    base.update(overrides)
    return base


def test_composer_admit_does_not_echo_resolved_xhigh() -> None:
    body = build_admit_report_body(**_minimal_admit_kwargs())
    assert "requested_effort=xhigh" in body
    assert "resolved=(unsupported: model has no effort axis)" in body
    assert "resolved=xhigh" not in body


def test_grok_admit_echoes_resolved_rung() -> None:
    resolved = admit_plane_resolved_effort(
        "cursor/grok-4.7",
        {"requested": "xhigh", "resolved_effort": "xhigh"},
    )
    assert resolved == "xhigh"
