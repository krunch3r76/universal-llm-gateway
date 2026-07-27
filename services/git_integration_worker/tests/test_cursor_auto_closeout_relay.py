"""Unit tests for cursor-auto §2 CLOSEOUT relay selection."""

from __future__ import annotations

import json

from services.git_integration_worker.cursor_auto.closeout_relay import (
    is_wrapper_manifest,
    looks_section2,
    select_closeout_relay_payload,
    status_from_section2,
    strip_machine_tail,
    synthesize_section2,
)

_WRAPPER = json.dumps(
    {
        "schema_version": 1,
        "status": "partial",
        "files_created": [],
        "capture_status": "partial",
        "effects_manifest": {"schema_version": 1},
    }
)

_T9_OFFGIT_A = "cortex://notes/system/specs/operator-proxy-closeout-section2-relay.md"
_T9_OFFGIT_B = (
    "cortex://notes/system/reviews/closeout-honesty-spec-review-grok-2026-07-26.md"
)

_T9_WRAPPER = json.dumps(
    {
        "schema_version": 1,
        "status": "partial",
        "summary": "dispatch auto-5528t9: 12 tool calls",
        "files_created": [],
        "files_modified": [],
        "files_deleted": [],
        "effects": [],
        "files_offgit_produced": [_T9_OFFGIT_A, _T9_OFFGIT_B],
        "capture_status": "partial",
        "effects_manifest": {"schema_version": 1},
    }
)

_SECTION2 = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:**
1. AC1 — PASS — pytest said 44 passed

**deltas_to_spec:** none

**open_forks:** none
"""


def test_wrapper_manifest_detection():
    assert is_wrapper_manifest(_WRAPPER) is True
    assert is_wrapper_manifest(_SECTION2) is False


def test_section2_detection_requires_both_markers():
    assert looks_section2(_SECTION2) is True
    assert looks_section2("ac_verdict only") is False
    assert looks_section2(_WRAPPER) is False


def test_strip_machine_tail_drops_effects_manifest():
    blended = _SECTION2 + "\n## effects_manifest\n\n" + _WRAPPER
    stripped = strip_machine_tail(blended)
    assert "ac_verdict" in stripped
    assert "effects_manifest" not in stripped
    assert "schema_version" not in stripped


def test_select_prefers_section2_sidecar_over_wrapper():
    blended = _SECTION2 + "\n## effects_manifest\n\n" + _WRAPPER
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=blended,
        ledger_status="completed",
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"
    assert "ac_verdict" in payload.body
    assert "schema_version" not in payload.body


def test_select_synthesizes_section2_when_no_sidecar():
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=None,
        ledger_status="completed",
    )
    assert payload.source == "section2_synthesized"
    assert looks_section2(payload.body) is True
    assert payload.status == "partial"


def test_synthesized_section2_names_offgit_cortex_writes():
    payload = select_closeout_relay_payload(
        sdk_body=_T9_WRAPPER,
        sidecar_text=None,
        ledger_status="completed",
    )
    assert payload.source == "section2_synthesized"
    assert _T9_OFFGIT_A in payload.body
    assert _T9_OFFGIT_B in payload.body


def test_synthesized_section2_merges_offgit_even_when_effects_nonempty():
    repo_paths = [f"services/git_integration_worker/file{i}.py" for i in range(5)]
    offgit_uri = "cortex://notes/system/specs/truncation-hole-guard.md"
    wrapper = json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "files_created": [],
            "files_modified": [],
            "files_deleted": [],
            "effects": repo_paths,
            "files_offgit_produced": [offgit_uri],
            "capture_status": "partial",
            "effects_manifest": {"schema_version": 1},
        }
    )
    body = synthesize_section2(
        wrapper_text=wrapper,
        sidecar_text=None,
        dispatch_id="trunc-guard",
    )
    assert body is not None
    assert offgit_uri in body
    for path in repo_paths:
        assert path in body


def test_synthesized_section2_does_not_fabricate_pass():
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=None,
        ledger_status="completed",
    )
    assert "unauthored" in payload.body.lower()
    assert "PASS" not in payload.body


def test_synthesized_status_matches_wrapper_payload_status():
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=None,
        ledger_status="completed",
    )
    assert payload.status == "partial"


def test_authored_sidecar_still_preferred_over_synthesis():
    payload = select_closeout_relay_payload(
        sdk_body=_T9_WRAPPER,
        sidecar_text=_SECTION2,
        ledger_status="completed",
    )
    assert payload.source == "section2_sidecar"
    assert "machine-derived envelope" not in payload.body
    assert "unauthored — executor emitted no §2" not in payload.body


def test_wrapper_source_retained_for_non_manifest_prose():
    prose = "Executor finished but emitted plain prose only — no §2 markers."
    payload = select_closeout_relay_payload(
        sdk_body=prose,
        sidecar_text=None,
        ledger_status="completed",
    )
    assert payload.source == "wrapper"
    assert payload.body == prose


def test_status_from_section2_beats_ledger_contradiction():
    """5867 t24 class: ledger complete + wrapper partial → trust §2 status."""
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=_SECTION2,
        ledger_status="completed",
    )
    assert payload.status == "complete"
    assert status_from_section2(payload.body) == "complete"


def test_select_empty_when_nothing_captured():
    payload = select_closeout_relay_payload(
        sdk_body=None,
        sidecar_text=None,
        ledger_status="failed",
    )
    assert payload.source == "empty"
    assert payload.status == "blocked"
    assert "no cursor-sdk closeout" in payload.body
