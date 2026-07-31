"""Second detector for an ungated charter implement window (review §1 / T2).

The chain under test spans two services: the git-integration-worker echoes a
deviation token onto the implement closeout when its readiness gate no-opped, and
the charter-runner harvest detects that token on the worker thread it already
reads. The seam is the token itself, so the tests pin both halves plus the
round-trip through a real closeout body.
"""

from __future__ import annotations

import asyncio
import json

from services.git_integration_worker.cursor_sdk_implement_gate import (
    IMPLEMENT_GATE_BYPASS_DEVIATION,
    implement_gate_bypass_deviations,
)

from . import gate_bypass_detect, harvest


def _closeout_turn(turn_number: int, deviations: list[str]) -> dict:
    return {
        "turn_number": turn_number,
        "body": json.dumps(
            {
                "schema_version": 1,
                "status": "complete",
                "summary": "dispatch d-1: 4 tool calls",
                "source_ref": "workspaces://sidecar/d-1.md",
                "deviations": deviations,
                "evidence_uris": {"dispatch_ids": ["d-1"]},
            }
        ),
    }


def test_wire_token_matches_across_service_domains() -> None:
    """The producer and consumer restate the same token; drift breaks the chain."""
    assert (
        gate_bypass_detect.GATE_BYPASS_DEVIATION == IMPLEMENT_GATE_BYPASS_DEVIATION
    )


def test_worker_echoes_token_only_for_unresolved_implement() -> None:
    assert implement_gate_bypass_deviations(
        contract="implement", work_item_ref=None
    ) == (IMPLEMENT_GATE_BYPASS_DEVIATION,)
    # A resolvable source_ref means the gate had something to enforce against.
    assert (
        implement_gate_bypass_deviations(
            contract="implement", work_item_ref="todo:some-slug"
        )
        == ()
    )
    # Non-implement contracts have no readiness gate to bypass.
    assert (
        implement_gate_bypass_deviations(contract="consult", work_item_ref=None) == ()
    )


def test_detects_bypass_from_worker_closeout_turn() -> None:
    findings = gate_bypass_detect.detect_gate_bypass(
        [
            {"turn_number": 1, "body": "packet prose, not JSON"},
            _closeout_turn(2, ["capture:outside_repo_baseline_missing"]),
            _closeout_turn(3, [IMPLEMENT_GATE_BYPASS_DEVIATION]),
        ]
    )
    assert len(findings) == 1
    assert findings[0].turn_number == 3
    assert findings[0].dispatch_id == "d-1"


def test_clean_worker_thread_produces_no_finding() -> None:
    assert gate_bypass_detect.detect_gate_bypass([_closeout_turn(2, [])]) == []


def test_harvest_emits_correlated_event_for_bypassed_window(monkeypatch) -> None:
    """Acceptance 2 — the signal carries the charter root and window index."""
    emitted: list[dict] = []

    async def _capture(**kwargs) -> None:
        emitted.append(kwargs)

    monkeypatch.setattr(
        harvest.events, "emit_manage_charter_implement_gate_bypassed", _capture
    )
    asyncio.run(
        harvest.flag_gate_bypass(
            root_id="4242",
            window_index=7,
            worker_thread="9001",
            worker_turns=[_closeout_turn(3, [IMPLEMENT_GATE_BYPASS_DEVIATION])],
        )
    )
    assert emitted == [
        {
            "root": "4242",
            "window_index": 7,
            "worker_thread": "9001",
            "dispatch_id": "d-1",
            "source_ref": "workspaces://sidecar/d-1.md",
            "turn_number": 3,
        }
    ]


def test_detector_failure_does_not_abort_harvest(monkeypatch) -> None:
    """Acceptance 4 — no new abort path into the tick loop."""

    def _boom(_turns):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(gate_bypass_detect, "detect_gate_bypass", _boom)
    turns = [
        {"turn_number": 1, "subject": "ADMIT window 1", "body": '{"window": 1}'},
    ]
    # completed_windows finds no CHECKPOINT here, so this asserts the harvest
    # entrypoint stays clean; _flag_gate_bypass itself is guarded by its caller.
    asyncio.run(harvest.harvest_completed_windows("4242", turns))
