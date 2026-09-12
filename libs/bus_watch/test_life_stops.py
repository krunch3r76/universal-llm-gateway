"""Tests for life stop evaluation and knobs."""

from __future__ import annotations

import pytest

from bus_watch.consent_projection import project_gates
from bus_watch.life_stops import LIFE_KNOB_DEFAULTS, evaluate_stops, life_knobs

pytestmark = pytest.mark.offline

NOW = "2026-09-11T12:00:00Z"


def _knobs(**overrides: object) -> dict[str, dict]:
    return life_knobs(dict(overrides), as_of=NOW)


def test_nine_defaults_and_policy_override() -> None:
    assert len(LIFE_KNOB_DEFAULTS) == 9
    knobs = _knobs()
    assert knobs["wake_cron"]["source"] == "default"
    assert knobs["repeated_failure_n"]["value"] == 2

    overridden = _knobs(repeated_failure_n=5)
    assert overridden["repeated_failure_n"]["value"] == 5
    assert overridden["repeated_failure_n"]["source"] == "policy"
    assert overridden["wake_cron"]["source"] == "default"


def test_operator_gate_fires_and_not_when_lifted() -> None:
    gates = project_gates([], now=NOW, let_drive_ttl_days=30)
    facts = {
        "next_move": {
            "goal_id": "todo:g1",
            "class": "outbound_correspondence",
            "scope": "landlord",
            "draft": "hello",
        },
        "gates": gates,
    }
    rows = evaluate_stops(facts, _knobs(), now=NOW)
    assert any(r["stop"] == "OPERATOR_GATE" for r in rows)

    lifted = project_gates(
        [
            {
                "id": "a:1",
                "class": "let-drive",
                "gate_class": "outbound_correspondence",
                "scope": "landlord",
                "expiry": "2026-12-01T00:00:00Z",
            }
        ],
        now=NOW,
        let_drive_ttl_days=30,
    )
    facts["gates"] = lifted
    rows = evaluate_stops(facts, _knobs(), now=NOW)
    assert not any(r["stop"] == "OPERATOR_GATE" for r in rows)


def test_consult_pending_fires() -> None:
    facts = {"check_disagrees": True}
    rows = evaluate_stops(facts, _knobs(), now=NOW)
    assert any(r["stop"] == "CONSULT_PENDING" for r in rows)


def test_spend_cap_boundary() -> None:
    cap = LIFE_KNOB_DEFAULTS["spend_cap_dispatches_per_day"]
    at_cap = evaluate_stops({"dispatches_today": cap}, _knobs(), now=NOW)
    assert any(r["stop"] == "SPEND_CAP" for r in at_cap)
    below = evaluate_stops({"dispatches_today": cap - 1}, _knobs(), now=NOW)
    assert not any(r["stop"] == "SPEND_CAP" for r in below)


def test_context_budget_boundary() -> None:
    ratio = LIFE_KNOB_DEFAULTS["context_budget_ratio"]
    at = evaluate_stops({"context_ratio": ratio}, _knobs(), now=NOW)
    assert any(r["stop"] == "CONTEXT_BUDGET" for r in at)
    below = evaluate_stops({"context_ratio": ratio - 0.01}, _knobs(), now=NOW)
    assert not any(r["stop"] == "CONTEXT_BUDGET" for r in below)


def test_repeated_failure_and_standing_exempt() -> None:
    n = LIFE_KNOB_DEFAULTS["repeated_failure_n"]
    facts = {
        "move_failures": {
            "move-a": {"n": n, "standing": False, "goal_id": "todo:g1"},
        }
    }
    rows = evaluate_stops(facts, _knobs(), now=NOW)
    assert any(r["stop"] == "REPEATED_FAILURE" for r in rows)

    standing = {
        "move_failures": {
            "move-a": {"n": n, "standing": True, "goal_id": "todo:g1"},
        }
    }
    rows = evaluate_stops(standing, _knobs(), now=NOW)
    assert not any(r["stop"] == "REPEATED_FAILURE" for r in rows)


def test_waiting_on_world_boundary() -> None:
    facts = {
        "goals": [{"id": "todo:w", "status": "WAITING_ON_WORLD"}],
        "last_progress": {"todo:w": "2026-09-07T12:00:00Z"},
    }
    rows = evaluate_stops(facts, _knobs(), now=NOW)
    assert any(r["stop"] == "WAITING_ON_WORLD" for r in rows)

    fresh = {
        "goals": [{"id": "todo:w", "status": "WAITING_ON_WORLD"}],
        "last_progress": {"todo:w": "2026-09-10T12:00:00Z"},
    }
    rows = evaluate_stops(fresh, _knobs(), now=NOW)
    assert not any(r["stop"] == "WAITING_ON_WORLD" for r in rows)


def test_deadline_near_page_follows_is_gated() -> None:
    gates = project_gates([], now=NOW, let_drive_ttl_days=30)
    facts = {
        "goals": [
            {
                "id": "todo:due",
                "status": "open",
                "deadline": "2026-09-13",
            }
        ],
        "next_move": {
            "goal_id": "todo:due",
            "class": "outbound_correspondence",
            "scope": "landlord",
            "draft": "draft",
        },
        "gates": gates,
    }
    rows = evaluate_stops(facts, _knobs(), now=NOW)
    deadline_rows = [r for r in rows if r["stop"] == "DEADLINE_NEAR"]
    assert len(deadline_rows) == 1
    assert deadline_rows[0]["page"] is True

    lifted = project_gates(
        [
            {
                "id": "a:5",
                "class": "let-drive",
                "gate_class": "outbound_correspondence",
                "scope": "landlord",
                "expiry": "2026-12-01T00:00:00Z",
            }
        ],
        now=NOW,
        let_drive_ttl_days=30,
    )
    facts["gates"] = lifted
    rows = evaluate_stops(facts, _knobs(), now=NOW)
    deadline_rows = [r for r in rows if r["stop"] == "DEADLINE_NEAR"]
    assert deadline_rows[0]["page"] is False
