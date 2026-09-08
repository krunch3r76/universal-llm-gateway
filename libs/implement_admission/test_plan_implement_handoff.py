"""Unit tests for plan-mode → implement nest handoff helpers."""

from __future__ import annotations

import json

import pytest

from implement_admission.plan_implement_handoff import (
    NEST_IMPLEMENT_HINT_KEY,
    build_nest_implement_hint,
    parse_nest_implement_hint,
    plan_implement_handoff_eligible,
    plan_implement_handoff_open,
)

pytestmark = pytest.mark.offline


def test_build_nest_implement_hint_requires_plan_complete_and_implement_ready() -> None:
    built = build_nest_implement_hint(
        plan_verdict="PLAN_COMPLETE",
        implement_ready=True,
        thread_id="10363",
        dispatch_id="d1",
        source_ref="todo:plan-bridge",
        artifact_paths=["cortex://notes/spec.md"],
    )
    assert built is not None
    assert built["verdict"] == "PLAN_COMPLETE"
    assert built["density_triage"] == "implement_ready"
    assert built["sdk_mode"] == "agent"
    assert built["artifact_paths"] == ["cortex://notes/spec.md"]

    assert (
        build_nest_implement_hint(
            plan_verdict="PARTIAL",
            implement_ready=True,
            thread_id="10363",
            dispatch_id="d1",
        )
        is None
    )
    assert (
        build_nest_implement_hint(
            plan_verdict="PLAN_COMPLETE",
            implement_ready=False,
            thread_id="10363",
            dispatch_id="d1",
        )
        is None
    )


def test_parse_and_eligible_nest_implement_hint() -> None:
    hint = {
        "verdict": "PLAN_COMPLETE",
        "density_triage": "implement_ready",
        "dispatch_id": "plan-d1",
    }
    body = json.dumps({NEST_IMPLEMENT_HINT_KEY: hint})
    parsed = parse_nest_implement_hint(body)
    assert parsed == hint
    assert plan_implement_handoff_eligible(parsed) is True
    assert plan_implement_handoff_open(body) is True


def test_parse_nest_implement_hint_rejects_non_json() -> None:
    assert parse_nest_implement_hint("not json") is None
    assert plan_implement_handoff_open({"other": 1}) is False
