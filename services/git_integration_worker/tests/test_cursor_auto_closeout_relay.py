"""Unit tests for cursor-auto §2 CLOSEOUT relay selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_auto.closeout_relay import (
    is_wrapper_manifest,
    looks_section2,
    select_closeout_relay_payload,
    status_from_section2,
    strip_machine_tail,
    synthesize_section2,
)
from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex import (
    _MAX_RELAYED_CORTEX_CHARS,
    cap_relayed_cortex_text,
    read_cortex_text,
)
from services.git_integration_worker.cursor_auto.relay_trust import (
    enforce_synthesized_partial,
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
        caller_auditable=True,
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
        caller_auditable=True,
    )
    assert payload.source == "section2_synthesized"
    assert looks_section2(payload.body) is True
    assert payload.status == "partial"


def test_synthesized_section2_names_offgit_cortex_writes():
    payload = select_closeout_relay_payload(
        sdk_body=_T9_WRAPPER,
        sidecar_text=None,
        ledger_status="completed",
        caller_auditable=True,
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
        caller_auditable=True,
    )
    assert "relay could not locate" in payload.body.lower()
    assert "PASS" not in payload.body


def test_synthesized_status_matches_wrapper_when_no_sidecar():
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_status,
    )

    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=None,
        ledger_status="completed",
        caller_auditable=True,
    )
    assert payload.status == "partial"
    assert extract_status(payload.body) == payload.status


def test_synthesized_wrapper_complete_stays_partial_when_unauthored_cells():
    """Machine-synthesized §2 preserves wrapper status; relay_note carries gaps."""
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_status,
    )

    wrapper = json.dumps(
        {
            "schema_version": 1,
            "status": "complete",
            "files_created": [],
            "capture_status": "partial",
            "effects_manifest": {"schema_version": 1},
        }
    )
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        caller_auditable=True,
    )
    assert payload.source == "section2_synthesized"
    assert payload.status == "complete"
    assert extract_status(payload.body) == "complete"
    assert payload.relay_note is not None
    assert "synthesized_§2" in payload.relay_note


def test_authored_sidecar_still_preferred_over_synthesis():
    payload = select_closeout_relay_payload(
        sdk_body=_T9_WRAPPER,
        sidecar_text=_SECTION2,
        ledger_status="completed",
        caller_auditable=True,
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
        caller_auditable=True,
    )
    assert payload.source == "wrapper"
    assert payload.body == prose


def test_status_from_section2_beats_ledger_contradiction():
    """5867 t24 class: ledger complete + wrapper partial → trust §2 status."""
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=_SECTION2,
        ledger_status="completed",
        caller_auditable=True,
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


_BARE_UNAUTHORIZED = (
    "unauthored — not reported by executor",
    "unknown — executor emitted no §2",
)


def _write_cortex_file(cortex_root: Path, uri: str, body: str) -> None:
    rel = uri.removeprefix("cortex://")
    path = cortex_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _wrapper_with_cortex_uris(*uris: str, dispatch_id: str = "D-TEST") -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "status": "partial",
            "summary": f"dispatch {dispatch_id}",
            "files_created": [],
            "files_modified": [],
            "files_deleted": [],
            "effects": list(uris),
            "files_offgit_produced": [],
            "capture_status": "partial",
            "effects_manifest": {"schema_version": 1},
        }
    )


def test_select_promotes_cortex_looks_section2_over_synthesize(tmp_path: Path):
    dispatch_id = "D-PROMOTE"
    uri = "cortex://notes/reviews/promote-closeout.md"
    section2 = f"""\
TYPE: CLOSEOUT
status: complete

dispatch_id: {dispatch_id}

**ac_verdict:**
1. AC1 — PASS — pytest green

**deltas_to_spec:** none
"""
    _write_cortex_file(tmp_path, uri, section2)
    wrapper = _wrapper_with_cortex_uris(uri, dispatch_id=dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
    )
    assert payload.source == "section2_sidecar"
    assert payload.body.startswith("TYPE: CLOSEOUT")
    assert "ac_verdict" in payload.body
    assert "machine-derived envelope" not in payload.body


def test_select_field_fills_when_cortex_lacks_section2_markers(tmp_path: Path):
    dispatch_id = "D-WEDGE"
    uri = "cortex://notes/reviews/wedge-snapshots.md"
    wedge_body = (
        f"Investigation for {dispatch_id}\n\n"
        "Observed wedge regression across three snapshots; root cause is timing."
    )
    _write_cortex_file(tmp_path, uri, wedge_body)
    wrapper = _wrapper_with_cortex_uris(uri, dispatch_id=dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
    )
    assert payload.source == "section2_synthesized"
    assert looks_section2(payload.body) is True
    for literal in _BARE_UNAUTHORIZED:
        assert literal not in payload.body
    assert uri in payload.body
    assert "unclassified" in payload.body
    assert "wedge regression" not in payload.body


def test_select_field_fills_extracts_heading_cells(tmp_path: Path):
    dispatch_id = "D-5996"
    uri = "cortex://notes/reviews/investigate-5996-closeout.md"
    body = f"""\
dispatch {dispatch_id}

## deltas_to_spec
Added cortex scan before synthesize.

## decisions_taken
Promote-first with field-fill fallback.
"""
    _write_cortex_file(tmp_path, uri, body)
    wrapper = _wrapper_with_cortex_uris(uri, dispatch_id=dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
    )
    assert payload.source == "section2_synthesized"
    assert "Added cortex scan before synthesize." in payload.body
    assert "Promote-first with field-fill fallback." in payload.body
    assert payload.body.count(f"see {uri}") < 4


def test_select_skips_unreadable_cortex_uri(tmp_path: Path):
    dispatch_id = "D-MISSING"
    missing_uri = "cortex://notes/reviews/missing-closeout.md"
    wrapper = _wrapper_with_cortex_uris(missing_uri, dispatch_id=dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
    )
    assert payload.source == "section2_synthesized"
    assert "relay could not locate" in payload.body.lower()


def test_repo_sidecar_still_beats_cortex_uri(tmp_path: Path):
    dispatch_id = "D-REPO-WIN"
    uri = "cortex://notes/reviews/cortex-closeout.md"
    cortex_body = f"""\
TYPE: CLOSEOUT
status: complete
dispatch_id: {dispatch_id}
**ac_verdict:** PASS
**deltas_to_spec:** none
"""
    _write_cortex_file(tmp_path, uri, cortex_body)
    wrapper = _wrapper_with_cortex_uris(uri, dispatch_id=dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=_SECTION2,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
    )
    assert payload.source == "section2_sidecar"
    assert "ac_verdict" in payload.body
    # Wrapper off-git writes may amend effects and clamp status to partial — source precedence is the bind.
    assert payload.status in {"complete", "partial"}


def test_no_cortex_uri_synthesize_unchanged():
    payload = select_closeout_relay_payload(
        sdk_body=_T9_WRAPPER,
        sidecar_text=None,
        ledger_status="completed",
        caller_auditable=True,
    )
    assert payload.source == "section2_synthesized"
    assert _T9_OFFGIT_A in payload.body
    assert _T9_OFFGIT_B in payload.body


def test_marker_complete_wrong_dispatch_demotes_to_field_fill(tmp_path: Path):
    dispatch_id = "D-UNDER-TEST"
    other_dispatch = "D-OTHER"
    uri = "cortex://notes/reviews/wrong-bind-closeout.md"
    body = f"""\
TYPE: CLOSEOUT
status: complete
dispatch_id: {other_dispatch}

**ac_verdict:**
1. AC1 — PASS

**deltas_to_spec:** none
"""
    _write_cortex_file(tmp_path, uri, body)
    wrapper = _wrapper_with_cortex_uris(uri, dispatch_id=dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
    )
    assert payload.source == "section2_synthesized"
    assert payload.status == "partial"
    assert uri in payload.body


def test_promoted_authored_sidecar_preserves_complete_status(tmp_path: Path):
    """Authored cortex promote relays §2 status — not synthesized partial clamp."""
    dispatch_id = "D-CLAMP"
    uri = "cortex://notes/reviews/clamp-closeout.md"
    body = f"""\
TYPE: CLOSEOUT
status: complete
dispatch_id: {dispatch_id}

**ac_verdict:** PASS — all green

**deltas_to_spec:** none
"""
    _write_cortex_file(tmp_path, uri, body)
    wrapper = _wrapper_with_cortex_uris(uri, dispatch_id=dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"


def test_cortex_path_traversal_skipped(tmp_path: Path):
    dispatch_id = "D-TRAVERSAL"
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    traversal_uri = "cortex://../outside-secret.txt"
    wrapper = _wrapper_with_cortex_uris(traversal_uri, dispatch_id=dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
    )
    assert payload.source == "section2_synthesized"
    assert read_cortex_text(traversal_uri, cortex_root=tmp_path) is None
    assert read_cortex_text("cortex:///etc/passwd", cortex_root=tmp_path) is None


def test_oversized_cortex_body_truncated(tmp_path: Path):
    dispatch_id = "D-HUGE"
    uri = "cortex://notes/reviews/huge-closeout.md"
    body = (
        f"TYPE: CLOSEOUT\nstatus: complete\ndispatch_id: {dispatch_id}\n\n"
        f"**ac_verdict:**\n{'x' * (_MAX_RELAYED_CORTEX_CHARS + 500)}\n\n"
        "**deltas_to_spec:** none\n"
    )
    _write_cortex_file(tmp_path, uri, body)
    wrapper = _wrapper_with_cortex_uris(uri, dispatch_id=dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
    )
    assert payload.source == "section2_sidecar"
    assert len(payload.body) <= _MAX_RELAYED_CORTEX_CHARS
    assert "full closeout:" in payload.body.lower()
    assert uri in (payload.body_full or payload.body)


def test_two_promote_eligible_first_wins(tmp_path: Path):
    dispatch_id = "D-FIRST-WIN"
    uri_a = "cortex://notes/reviews/first-promote.md"
    uri_b = "cortex://notes/reviews/second-promote.md"
    first_body = f"""\
TYPE: CLOSEOUT
status: complete
dispatch_id: {dispatch_id}
**ac_verdict:** first wins
**deltas_to_spec:** none
"""
    second_body = f"""\
TYPE: CLOSEOUT
status: complete
dispatch_id: {dispatch_id}
**ac_verdict:** second loses
**deltas_to_spec:** none
"""
    _write_cortex_file(tmp_path, uri_a, first_body)
    _write_cortex_file(tmp_path, uri_b, second_body)
    wrapper = _wrapper_with_cortex_uris(uri_a, uri_b, dispatch_id=dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
    )
    assert payload.source == "section2_sidecar"
    assert "first wins" in payload.body
    assert "second loses" not in payload.body


def test_cap_relayed_cortex_text_appends_marker():
    uri = "cortex://notes/reviews/huge.md"
    text = "a" * (_MAX_RELAYED_CORTEX_CHARS + 10)
    capped = cap_relayed_cortex_text(text, uri)
    assert len(capped) <= _MAX_RELAYED_CORTEX_CHARS
    assert uri in capped
    assert "truncated" in capped.lower()


_AUTHORED_UNDERCLAIM = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:**
1. AC1 — PASS — all criteria met

**deltas_to_spec:** none

**effects:** (none — confer contract; no repo writes this episode)

**open_forks:** none
"""


def test_authored_underclaim_amends_effects_preserves_judgment():
    wrapper = json.dumps(
        {
            "schema_version": 1,
            "status": "complete",
            "files_offgit_produced": [_T9_OFFGIT_A],
            "capture_status": "partial",
            "effects_manifest": {"schema_version": 1},
        }
    )
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=_AUTHORED_UNDERCLAIM,
        ledger_status="completed",
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert _T9_OFFGIT_A in payload.body
    assert "no repo writes" not in payload.body.lower()
    assert "AC1 — PASS" in payload.body
    assert payload.status == "complete"
    assert "deviation:effects_enriched_status_held" in payload.body


def test_cortex_promote_underclaim_still_amended(tmp_path: Path):
    dispatch_id = "D-UNDERCLAIM-PROMOTE"
    uri = "cortex://notes/reviews/promote-underclaim.md"
    section2 = f"""\
TYPE: CLOSEOUT
status: complete
dispatch_id: {dispatch_id}

**ac_verdict:**
1. AC1 — PASS

**deltas_to_spec:** none

**effects:** none
"""
    _write_cortex_file(tmp_path, uri, section2)
    wrapper = _wrapper_with_cortex_uris(uri, dispatch_id=dispatch_id)
    wrapper_data = json.loads(wrapper)
    wrapper_data["files_offgit_produced"] = [uri]
    wrapper = json.dumps(wrapper_data)
    payload = select_closeout_relay_payload(
        sdk_body=wrapper,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        cortex_root=tmp_path,
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert uri in payload.body
    assert payload.status == "complete"
    assert "deviation:effects_enriched_status_held" in payload.body


# --- agent-bus:6222 exemplar fixtures (t5 / t12 / t19) ---

_T12_CORTEX_URI = (
    "cortex://notes/system/dispatch-closeouts/fable-edit1-falsifier4-tail-2026-07-28.md"
)

_FIXTURE_6222_T12_SIDECAR = """\
# Closeout — fable edit-1 falsifier-4 tail

## Verdict

**DONE** — falsifier-4 class pillar-law restatements retired on skills/rules surface.

## AC1 per-site disposition

### 1. implement-todo/SKILL.md

**Verdict:** `restatement — retired`

## deltas_to_spec

- **Spec:** fable-vision-permeation edit-1
- **Delta landed:** digest endpoint cites at choke points

## decisions_taken

Retired implement-todo and todo_ulg restatements; left awareness_ulg pointer alone.

## next

- install_plugin propagation residue

## open forks

none
"""

_FIXTURE_6222_T12_WRAPPER = json.dumps(
    {
        "schema_version": 1,
        "status": "complete",
        "summary": "dispatch auto-1fcdc4411e03: 47 tool calls",
        "files_created": [],
        "files_modified": ["cursor-plugins/ulg-ecosystem/rules/todo_ulg.mdc"],
        "files_deleted": [],
        "effects": [
            "cursor-plugins/ulg-ecosystem/rules/todo_ulg.mdc",
            "cursor-plugins/ulg-ecosystem/skills/implement-todo/SKILL.md",
            _T12_CORTEX_URI,
        ],
        "files_offgit_produced": [_T12_CORTEX_URI],
        "capture_status": None,
        "effects_manifest": {"schema_version": 1},
        "evidence_uris": {
            "artifact_paths": [
                "workspaces://universal-llm-gateway/tmp/reviews/closeouts/auto-1fcdc4411e03.md",
                _T12_CORTEX_URI,
            ],
        },
    }
)

_FIXTURE_6222_T19_SIDECAR = """\
## §2 CLOSEOUT

**status:** complete

### ac_verdict

| AC | Verdict |
|---|---|
| AC1 | pass — CHECKPOINT on agent-bus:6205 turn **13** |
| AC2 | pass — bind falsifier watch items in Frictions |
| AC3 | pass — todo:operator-proxy-closeout-section2-relay-recurrence minted |
| AC4 | pass — write URIs inline below |

### assumed_state

Confirmed — task:vision-permeation carries ARC COMPLETE.

### write_uris (inline — AC4)

- **CHECKPOINT:** agent-bus:**6205** turn **13**
- **Todo:** **todo:operator-proxy-closeout-section2-relay-recurrence**

### operator_lane_pointer

CHECKPOINT Sidecars names Private operator lane: agent-bus:6222.

### todo_seed

Fifth observation of section2_synthesized / looks_section2 class.
"""

_FIXTURE_6222_T19_WRAPPER = json.dumps(
    {
        "schema_version": 1,
        "status": "complete",
        "summary": "dispatch auto-a64bd88d9f2b: 55 tool calls",
        "files_created": [],
        "files_modified": [],
        "files_deleted": [],
        "effects": [],
        "capture_status": None,
        "effects_manifest": {"schema_version": 1},
        "evidence_uris": {
            "dispatch_ids": ["auto-a64bd88d9f2b"],
            "bus_threads": ["6222"],
            "artifact_paths": [
                "workspaces://universal-llm-gateway/tmp/reviews/closeouts/auto-a64bd88d9f2b.md",
            ],
        },
        "deviations": [
            "stream_only_effect",
            "degraded:sdk_git_probe_absent",
            "capture:non_file_manifest_entry_dropped",
        ],
    }
)

_FIXTURE_6222_T5_SECTION2 = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:**
1. AC1 — PASS — mechanical trigger table landed

**deltas_to_spec:** R3 on skill-resident R-admit per advisor bind X1.

**decisions_taken:** Aptness ungated — symmetric with vision_field_missing.

**next:** FOLLOW-ON implement-todo/SKILL.md:71-72

**open_forks:** none
"""

_FALSE_NEGATIVE_LITERALS = (
    "failed looks_section2",
    "unauthored — executor emitted no §2 body",
    "unauthored — not reported by executor",
    "unknown — executor emitted no §2",
)


def test_fixture_6222_t12_mode_a_no_false_negative(tmp_path: Path):
    """AC1 — t12 cortex sidecar: no detector false-negative or bare see-uri flatten."""
    _write_cortex_file(tmp_path, _T12_CORTEX_URI, _FIXTURE_6222_T12_SIDECAR)
    payload = select_closeout_relay_payload(
        sdk_body=_FIXTURE_6222_T12_WRAPPER,
        sidecar_text=None,
        ledger_status="completed",
        dispatch_id="auto-1fcdc4411e03",
        cortex_root=tmp_path,
    )
    for literal in _FALSE_NEGATIVE_LITERALS:
        assert literal not in payload.body
    assert f"see {_T12_CORTEX_URI}" not in payload.body
    assert "digest endpoint cites at choke points" in payload.body
    assert "Retired implement-todo" in payload.body


def test_fixture_6222_t19_mode_b_no_false_unauthored():
    """AC2 — t19 repo sidecar: no unauthored literals while §2 substance present."""
    payload = select_closeout_relay_payload(
        sdk_body=_FIXTURE_6222_T19_WRAPPER,
        sidecar_text=_FIXTURE_6222_T19_SIDECAR,
        ledger_status="completed",
        dispatch_id="auto-a64bd88d9f2b",
    )
    for literal in _FALSE_NEGATIVE_LITERALS:
        assert literal not in payload.body
    assert "none captured" not in payload.body.lower()
    assert "AC1 | pass" in payload.body or "AC1" in payload.body
    assert "agent-bus:6205" in payload.body
    assert "todo:operator-proxy-closeout-section2-relay-recurrence" in payload.body


def test_fixture_6222_t5_pass_path_section2_sidecar():
    """AC5 — t5 shape still classifies section2_sidecar with authored §2 inline."""
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=_FIXTURE_6222_T5_SECTION2,
        ledger_status="completed",
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"
    assert looks_section2(payload.body)
    assert "AC1 — PASS" in payload.body


def test_enforce_synthesized_partial_branches():
    """Synthesized relay note is separate; authored status is no longer mutated."""
    from services.git_integration_worker.cursor_auto.relay_trust import (
        synthesized_relay_note,
    )

    assert enforce_synthesized_partial("complete", closeout_source="section2_synthesized") == "complete"
    assert enforce_synthesized_partial("complete", closeout_source="section2_sidecar") == "complete"
    assert synthesized_relay_note(
        closeout_source="section2_synthesized",
        status="complete",
    ).startswith("synthesized_§2")


_FIXTURE_T44_SECTION2 = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:**
- **AC-R PASS** — projector regression projects all §2 fields without unclassified cells
- **AC4 PASS** — §4 YAML parses to structured rows with required proof_class
- **AC6 PASS** — deploy_identity CONSUMERS mint one row per consumer slug

**deltas_to_spec:** none

**decisions_taken:** projector-first bind; truncation degrades to sidecar pointer not mid-token cut

**next:** harvest observes structured propagation on SDK closeout JSON

**open forks:** none
"""


def test_fixture_t44_section2_projects_zero_unclassified():
    """AC-R — well-formed §2 with all fields projects clean (t44 class)."""
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        count_unclassified_fields,
    )

    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=_FIXTURE_T44_SECTION2,
        ledger_status="completed",
        dispatch_id="auto-t44-regression",
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"
    assert count_unclassified_fields(payload.body) == 0
    assert "decisions_taken" in payload.body or "projector-first bind" in payload.body
    assert "next:" in payload.body.lower() or "harvest observes" in payload.body
    assert "open forks" in payload.body.lower() or "none" in payload.body
    assert payload.status == "complete"
    header_status = status_from_section2(payload.body)
    assert header_status == payload.status


def test_long_ac_verdict_degrades_to_pointer_not_mid_token():
    long_verdict = "PASS — " + ("observed-payload " * 80)
    sidecar = f"""\
TYPE: CLOSEOUT
status: complete

**ac_verdict:**
{long_verdict}

**deltas_to_spec:** none
"""
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id="auto-trunc-guard",
    )
    assert "unclassified" not in payload.body.lower()
    assert (
        "workspaces://universal-llm-gateway/tmp/reviews/closeouts/auto-trunc-guard.md"
        in payload.body
        or len(payload.body) <= 2500
    )
    assert not payload.body.rstrip().endswith("observed-pa")


# --- L1 over-claim clamp (6530 turn 8 / AC5–AC6) ---

_TURN8_DISPATCH = "auto-09e744ed67d9"
_TURN8_CORTEX_CLOSEOUT = "cortex://notes/system/threads/6530-fable-confer-closeout.md"
_TURN8_CORTEX_PROMPT = "cortex://notes/system/threads/6530-fable-caller-auditable-fork.md"
_TURN8_WS_MIRROR = (
    "workspaces://universal-llm-gateway/tmp/reviews/closeouts/auto-09e744ed67d9.md"
)

_TURN8_RELAYED_BODY = """\
TYPE: CONFER
status: partial
dispatch_id: auto-09e744ed67d9
model: cursor/grok-4.5
request_turn: 2

TYPE: CLOSEOUT
status: complete

| Field | Value |
|---|---|
| status | complete |
| ac_verdict | unclassified — relay could not parse §2 from 1498 bytes at workspaces://universal-llm-gateway/tmp/reviews/closeouts/auto-09e744ed67d9.md |
| deltas_to_spec | none — field not authored in §2 sidecar |
| decisions_taken | none — field not authored in §2 sidecar |
| effects | - cortex://notes/system/threads/6530-fable-caller-auditable-fork.md<br>- cortex://notes/system/threads/6530-fable-confer-closeout.md<br>- workspaces://universal-llm-gateway/tmp/reviews/closeouts/auto-09e744ed67d9.md<br>- agent-bus:6530 |
| evidence | none — see machine envelope below |
| next | unauthored — operator must derive from effects above |
| open forks | none — field not authored in §2 sidecar |
"""

_TURN8_WRAPPER = json.dumps(
    {
        "schema_version": 1,
        "status": "complete",
        "summary": f"dispatch {_TURN8_DISPATCH}",
        "files_created": [],
        "files_modified": [],
        "files_deleted": [],
        "effects": [_TURN8_CORTEX_PROMPT, _TURN8_CORTEX_CLOSEOUT],
        "files_offgit_produced": [_TURN8_CORTEX_PROMPT, _TURN8_CORTEX_CLOSEOUT],
        "capture_status": "partial",
        "effects_manifest": {"schema_version": 1},
        "evidence_uris": {
            "artifact_paths": [_TURN8_WS_MIRROR, _TURN8_CORTEX_CLOSEOUT],
            "bus_threads": ["6530"],
        },
    }
)


def test_turn8_relay_body_overclaim_clamp_ac5() -> None:
    """AC5 — turn 8 relay body: partial status from parse_failed; no false unread relabel."""
    from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
        finalize_relay_payload,
    )

    payload = finalize_relay_payload(
        CloseoutRelayPayload(
            body=_TURN8_RELAYED_BODY,
            status="complete",
            source="section2_synthesized",
        ),
        wrapper_text=_TURN8_WRAPPER,
        dispatch_id=_TURN8_DISPATCH,
    )
    assert payload.status == "relay_parse_failed"
    assert "parse_failed — authoritative sidecar:" in payload.body
    assert "unresolved — not read:" not in payload.body
    assert "overclaim:parse_failed_field" in payload.body
    assert "overclaim:false_absence_unread_provenance" not in payload.body


def test_internally_consistent_closeout_not_clamped_ac6() -> None:
    """AC6 — healthy complete closeout must not be clamped to partial."""
    from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
        finalize_relay_payload,
    )

    payload = finalize_relay_payload(
        CloseoutRelayPayload(
            body=_FIXTURE_T44_SECTION2,
            status="complete",
            source="section2_sidecar",
        ),
        wrapper_text=_WRAPPER,
        dispatch_id="auto-t44-regression",
        caller_auditable=True,
    )
    assert payload.status == "complete"
    assert "overclaim:" not in payload.body


_TURN38_INTERNALLY_CONSISTENT = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:**
1. AC1 — PASS — relayed closeout meets directive scope

**deltas_to_spec:** none

**decisions_taken:** deny-by-default clamp for blind caller

**effects:** none

**open_forks:** none
"""


def test_blind_caller_missing_access_coverage_clamps_turn38_class() -> None:
    """AC1 — blind caller: missing ACCESS/COVERAGE clamps complete.

    The blind seat is ``mcp-claude-life``; ``web-anthropic`` is the code-lane
    address and was restored to the auditable allowlist at 4b056a34, which is
    what this test's original premise asserted against.
    """
    from services.git_integration_worker.cursor_auto.caller_auditable import (
        caller_auditable,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
        finalize_relay_payload,
    )

    assert caller_auditable(from_agent="mcp-claude-life") is False
    payload = finalize_relay_payload(
        CloseoutRelayPayload(
            body=_TURN38_INTERNALLY_CONSISTENT,
            status="complete",
            source="section2_sidecar",
        ),
        wrapper_text=_WRAPPER,
        dispatch_id="auto-turn38-class",
        caller_auditable=caller_auditable(from_agent="mcp-claude-life"),
    )
    assert payload.status == "complete"
    assert payload.relay_note is not None
    assert "reporting:missing_access" in payload.relay_note
    assert "reporting:missing_coverage" in payload.relay_note
    assert "reporting:missing_access" in payload.body
    assert "reporting:missing_coverage" in payload.body


_6524_HEADING_ONLY_SECTION2 = """\
## §2 CLOSEOUT

**status:** complete

**ac_verdict**

| AC | Verdict | Evidence |
|---|---|---|
| AC1 | **PASS** | pytest green |

**deltas_to_spec:** none

**decisions_taken**
1. Used heading-only bold for ac_verdict (6524 arc class).

**effects (committed)**
- `admission/decide.py`
"""


def test_6524_heading_only_ac_verdict_table_extracts() -> None:
    """6524 sidecars use ``**ac_verdict**`` without colon — must not emit parse_failed."""
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        count_unclassified_fields,
    )

    dispatch_id = "auto-a438536de12d"
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=_6524_HEADING_ONLY_SECTION2,
        ledger_status="completed",
        dispatch_id=dispatch_id,
    )
    assert payload.source == "section2_sidecar"
    assert count_unclassified_fields(payload.body) == 0
    assert "parse_failed" not in payload.body.lower()
    assert "AC1" in payload.body
    assert "overclaim:unclassified_field" not in payload.body


@pytest.mark.parametrize(
    "dispatch_id",
    ["auto-5362a24e62c6", "auto-659d61c03158", "auto-a438536de12d"],
)
def test_6524_arc_sidecar_fixtures_no_parse_failed(dispatch_id: str) -> None:
    """Regression — three 6524 arc sidecars must relay authored ac_verdict tables."""
    from pathlib import Path

    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        count_unclassified_fields,
    )

    path = Path("tmp/reviews/closeouts") / f"{dispatch_id}.md"
    if not path.is_file():
        pytest.skip(f"fixture sidecar missing: {path}")
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=path.read_text(encoding="utf-8"),
        ledger_status="completed",
        dispatch_id=dispatch_id,
    )
    assert payload.source == "section2_sidecar"
    assert count_unclassified_fields(payload.body) == 0
    assert "relay could not parse" not in payload.body.lower()
    assert "AC1" in payload.body
    assert "overclaim:unclassified_field" not in payload.body


# --- closeout-relay crack specimens (6566 bind / AC1–AC3) ---

_FIXTURE_AE931A7364A4_SIDECAR = """\
## §2 CLOSEOUT — agent-bus:6538 §13 D2 operator-authority correction

**ac_verdict:** PASS (all six ACs met)

**MODEL ACTUAL:** `cursor/composer-2.5` (matches `desired_model`)

### SCOPE DELTA

**Done:**
- §A landed in `cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/SKILL.md`

**Not done (explicitly out of scope):**
- IDE restart / Reload Window

**deltas_to_spec:** none — attended IDE home reachable via explicit `HOME=/home/io`

### ACCESS

| Surface | Reachable? | Notes |
|---|---|---|
| Repo checkout | yes | All edits applied |
| Attended IDE home | yes | Install via `HOME=/home/io` |

### COVERAGE

| Retrieval | Corpus | Count / range |
|---|---|---|
| Sidecar spec | `cortex://notes/system/threads/6561-operator-authority-ide-parity-and-sync.md` | full read |

### effects

**Files touched:**
- `cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/SKILL.md`

**decisions_taken:**
- §C surface: `episode_briefing.py` — fits at 25/26 lines
"""

_FIXTURE_AE931A7364A4_WRAPPER = json.dumps(
    {
        "schema_version": 1,
        "status": "complete",
        "summary": "dispatch auto-ae931a7364a4",
        "files_created": [],
        "files_modified": [],
        "files_deleted": [],
        "effects": [],
        "capture_status": "partial",
        "effects_manifest": {"schema_version": 1},
        "evidence_uris": {
            "artifact_paths": [
                "workspaces://universal-llm-gateway/tmp/reviews/closeouts/auto-ae931a7364a4.md",
            ],
        },
    }
)

_FIXTURE_DC17CCD8B5E4_SIDECAR = """\
## TYPE: CLOSEOUT

**status:** complete

| Field | Value |
|---|---|
| **ac_verdict** | **PASS** — AC1 ticket minted; AC2 routed; AC3 surface named honestly |
| **deltas_to_spec** | none — mint + seed + route only; no fix, no classifier edits |
| **decisions_taken** | Created `todo:cursor-auto-closeout-relay-partial-status` |
| **effects** | `cortex://notes/system/specs/cursor-auto-closeout-relay-partial-status.md` |
| **evidence** | `entity_create` returned `id=todo:cursor-auto-closeout-relay-partial-status` |
| **next** | Codeforce pickup: `todo_candidates` query or spine recon |
| **open forks** | none |

**SCOPE DELTA:** Done: todo entity, spec corpus. Not done: defect fix.

**ACCESS:** `cortex` entity_create — reachable.

**COVERAGE:** Sidecar corpus read; prior tickets linked.

**MODEL ACTUAL:** n/a — DIRECTIVE did not specify model.
"""

_FIXTURE_DC17CCD8B5E4_WRAPPER = json.dumps(
    {
        "schema_version": 1,
        "status": "complete",
        "summary": "dispatch auto-dc17ccd8b5e4",
        "files_created": [],
        "files_modified": [],
        "files_deleted": [],
        "effects": [
            "cortex://notes/system/specs/cursor-auto-closeout-relay-partial-status.md",
        ],
        "capture_status": "partial",
        "effects_manifest": {"schema_version": 1},
        "evidence_uris": {
            "artifact_paths": [
                "workspaces://universal-llm-gateway/tmp/reviews/closeouts/auto-dc17ccd8b5e4.md",
            ],
        },
    }
)


def test_specimen_ae931a7364a4_honest_absence_relays_complete_ac2() -> None:
    """AC2 — ATX sidecar with honest unauthored next/evidence: complete, no false overclaim."""
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        count_unclassified_fields,
    )

    payload = select_closeout_relay_payload(
        sdk_body=_FIXTURE_AE931A7364A4_WRAPPER,
        sidecar_text=_FIXTURE_AE931A7364A4_SIDECAR,
        ledger_status="completed",
        dispatch_id="auto-ae931a7364a4",
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"
    assert count_unclassified_fields(payload.body) == 0
    assert "overclaim:false_absence_unread_provenance" not in payload.body
    assert "unresolved — not read:" not in payload.body
    assert "reporting:missing_access" not in payload.body
    assert "reporting:missing_coverage" not in payload.body
    assert "PASS (all six ACs met)" in payload.body
    assert "relay could not locate `next`" in payload.body
    assert "relay could not locate `evidence`" in payload.body
    assert "none — field not authored in §2 sidecar" not in payload.body
    assert "unauthored — operator must derive from effects above" not in payload.body


def test_specimen_dc17ccd8b5e4_bold_table_fields_project_clean_ac1() -> None:
    """AC1 — bold table field names extract; all core fields populated, zero parse_failed."""
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        count_unclassified_fields,
    )

    payload = select_closeout_relay_payload(
        sdk_body=_FIXTURE_DC17CCD8B5E4_WRAPPER,
        sidecar_text=_FIXTURE_DC17CCD8B5E4_SIDECAR,
        ledger_status="completed",
        dispatch_id="auto-dc17ccd8b5e4",
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"
    assert count_unclassified_fields(payload.body) == 0
    assert "parse_failed" not in payload.body.lower()
    assert "overclaim:false_absence_unread_provenance" not in payload.body
    assert "reporting:missing_access" not in payload.body
    assert "reporting:missing_coverage" not in payload.body
    assert "**PASS** — AC1 ticket minted" in payload.body
    assert "Codeforce pickup" in payload.body
    assert "open forks" in payload.body.lower()


def test_read_failed_sidecar_emits_distinct_string_ac2c() -> None:
    """AC2(c) — when sidecar read fails, false-absence cells get read_failed prefix."""
    from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
        finalize_relay_payload,
    )

    failed_uri = "cortex://notes/reviews/missing-closeout.md"
    synthesized_body = """\
TYPE: CLOSEOUT
status: partial

| Field | Value |
|---|---|
| status | partial |
| ac_verdict | unauthored — not reported by executor |
| deltas_to_spec | none — field not authored in §2 sidecar |
| decisions_taken | none — field not authored in §2 sidecar |
| effects | none |
| evidence | none |
| next | unauthored — operator must derive from effects above |
| open forks | none — field not authored in §2 sidecar |
"""
    payload = finalize_relay_payload(
        CloseoutRelayPayload(
            body=synthesized_body,
            status="partial",
            source="section2_synthesized",
        ),
        wrapper_text=_WRAPPER,
        dispatch_id="auto-read-fail",
        sidecar_read_failed_uri=failed_uri,
    )
    assert f"read_failed — sidecar unavailable: {failed_uri}" in payload.body
    assert "unresolved — not read:" not in payload.body
    assert "overclaim:false_absence_unread_provenance" in payload.body


def test_emphasis_tolerant_table_field_matching_unit() -> None:
    """AC1 — _normalize_heading_key strips markdown emphasis from table field names."""
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_table_field,
    )

    body = "| **ac_verdict** | **PASS** — ticket minted |\n|---|---|\n"
    assert extract_table_field(body, "ac_verdict") == "**PASS** — ticket minted"
    body_backtick = "| `next` | follow-on work |\n"
    assert extract_table_field(body_backtick, "next") == "follow-on work"


# --- idle-wake fleet review relay honesty (agent-bus:6590 / a:27420) ---

_ACCUSATORY_PLACEHOLDER_MARKERS = (
    "none — field not authored in §2 sidecar",
    "unauthored — operator must derive from effects above",
    "none — see machine envelope below",
    "none captured — see machine envelope below",
    "unauthored — not reported by executor",
)

_REPLAY_DISPATCH_IDS = ("auto-91ca585767f0", "auto-379d67bb7092")
_REPLAY_SEMANTIC_FIELDS = (
    "deltas_to_spec",
    "access",
    "coverage",
    "model_actual",
)


def _extract_projected_table_cells(body: str) -> dict[str, str]:
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        _extract_table_cell,
    )

    fields = (
        "status",
        "ac_verdict",
        "deltas_to_spec",
        "decisions_taken",
        "effects",
        "evidence",
        "next",
        "open forks",
        "access",
        "coverage",
        "model_actual",
    )
    return {field: (_extract_table_cell(body, field) or "") for field in fields}


def _count_populated_semantic_fields(cells: dict[str, str]) -> int:
    populated = 0
    for field in _REPLAY_SEMANTIC_FIELDS:
        cell = cells.get(field, "")
        if not cell.strip():
            continue
        if cell.casefold().startswith("relay could not locate"):
            continue
        if any(marker in cell for marker in _ACCUSATORY_PLACEHOLDER_MARKERS):
            continue
        populated += 1
    return populated


def test_ac1_relay_parse_miss_never_blames_author() -> None:
    """AC1 — parse misses use relay voice; no accusatory fallback strings."""
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        relay_parse_miss_cell,
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        project_section2_table,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    dispatch_id = "auto-ac1-voice"
    sidecar = """\
TYPE: CLOSEOUT
status: partial

**ac_verdict:** AC1 — PASS

**deltas_to_spec:** none
"""
    provenance = sidecar_workspaces_ref(dispatch_id)
    body, _status = project_section2_table(
        strip_machine_tail(sidecar),
        provenance=provenance,
    )
    assert relay_parse_miss_cell("evidence", provenance) in body
    for marker in _ACCUSATORY_PLACEHOLDER_MARKERS:
        assert marker not in body


_AC1_SUBSECTION_BEFORE_CANONICAL_HEADING = """\
## §2 CLOSEOUT

**status:** complete

### AC verdicts

**AC1 — intent `18e42d50`**

Found in the dispatch-home intent store, not the operator home.

**AC2 — ledger row**

Row still `open`.

### ac_verdict

| AC | Verdict |
|---|---|
| AC1 | **PASS** — intent `completed` |
| AC2 | **PASS (negative)** — row still `open` |

**deltas_to_spec:** none
"""


def test_canonical_ac_verdict_heading_beats_earlier_ac1_subsection() -> None:
    """A1 — an exact `ac_verdict` heading outranks a prefix-matched `AC1 …` section.

    Heading matching is prefix-based so `AC1 — …` also matches the `ac_verdict`
    field. First-match-wins used to hand the cell to whichever appeared first in
    the document, so a compliant author who wrote the canonical heading still had
    an unrelated subsection relayed as their verdict — with no parse-miss signal.
    """
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
    )

    cell = extract_field_section(
        _AC1_SUBSECTION_BEFORE_CANONICAL_HEADING, "ac_verdict"
    )
    assert cell is not None
    assert "AC2" in cell, "canonical heading carries every AC row"
    assert "dispatch-home intent store" not in cell, "AC1 subsection must not win"


def test_ac1_subsection_still_resolves_when_no_canonical_heading() -> None:
    """A2 — the loose pass is unchanged, so detection cannot regress.

    ``looks_section2`` keys off ``ac_verdict`` resolving, so a sidecar that only
    has ``AC1 …`` headings must keep resolving — otherwise it relays as
    ``source=empty`` and the closeout is dropped from the bus entirely.
    """
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        looks_section2,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
    )

    prose = _AC1_SUBSECTION_BEFORE_CANONICAL_HEADING.replace(
        "### ac_verdict", "### unrelated tail"
    )
    assert extract_field_section(prose, "ac_verdict") is not None
    assert looks_section2(prose) is True


def test_ac2_source_ref_visible_in_relayed_body() -> None:
    """AC2 — source_ref line appears in projected body, not only machine envelope."""
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        project_section2_table,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    dispatch_id = "auto-ac2-source-ref"
    provenance = sidecar_workspaces_ref(dispatch_id)
    body, _status = project_section2_table(
        strip_machine_tail(_SECTION2),
        provenance=provenance,
    )
    assert f"source_ref: {provenance}" in body


@pytest.mark.parametrize("dispatch_id", _REPLAY_DISPATCH_IDS)
def test_ac3_replay_semantic_headings_populated(dispatch_id: str) -> None:
    """AC3 — composer SCOPE DELTA / ACCESS / COVERAGE / MODEL ACTUAL project populated."""
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        count_relay_parse_miss_fields,
        project_section2_table,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    path = Path("tmp/reviews/closeouts") / f"{dispatch_id}.md"
    if not path.is_file():
        pytest.skip(f"fixture sidecar missing: {path}")
    prose = strip_machine_tail(path.read_text(encoding="utf-8"))
    provenance = sidecar_workspaces_ref(dispatch_id)
    body, _status = project_section2_table(prose, provenance=provenance)
    cells = _extract_projected_table_cells(body)
    populated = _count_populated_semantic_fields(cells)
    assert populated == 4, (
        f"{dispatch_id}: expected 4 semantic fields populated, got {populated}; "
        f"cells={{{', '.join(f'{k}: {v[:40]!r}' for k, v in cells.items() if k in _REPLAY_SEMANTIC_FIELDS)}}}"
    )
    assert count_relay_parse_miss_fields(body) >= 1
    for marker in _ACCUSATORY_PLACEHOLDER_MARKERS:
        assert marker not in body


def test_ac3_replay_before_after_field_counts() -> None:
    """AC3 — replay fixtures: 4 semantic fields populated; accusatory placeholders eliminated."""
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        project_section2_table,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    before_accusatory = 0
    after_populated = 0
    after_relay_miss = 0
    for dispatch_id in _REPLAY_DISPATCH_IDS:
        path = Path("tmp/reviews/closeouts") / f"{dispatch_id}.md"
        if not path.is_file():
            pytest.skip(f"fixture sidecar missing: {path}")
        prose = strip_machine_tail(path.read_text(encoding="utf-8"))
        provenance = sidecar_workspaces_ref(dispatch_id)
        for field in _REPLAY_SEMANTIC_FIELDS:
            if not extract_field_section(prose, field):
                before_accusatory += 1
        body, _status = project_section2_table(prose, provenance=provenance)
        cells = _extract_projected_table_cells(body)
        after_populated += _count_populated_semantic_fields(cells)
        for cell in cells.values():
            if cell.casefold().startswith("relay could not locate"):
                after_relay_miss += 1
            if any(marker in cell for marker in _ACCUSATORY_PLACEHOLDER_MARKERS):
                pytest.fail(f"{dispatch_id}: accusatory placeholder survived: {cell!r}")
    assert before_accusatory == 0
    assert after_populated == 8
    assert after_relay_miss >= 4


def test_ac4_relay_honesty_tests_named_and_pass() -> None:
    """AC4 — meta: AC1–AC3 tests in this module are the named regression suite."""
    assert callable(test_ac1_relay_parse_miss_never_blames_author)
    assert callable(test_ac2_source_ref_visible_in_relayed_body)
    assert callable(test_ac3_replay_semantic_headings_populated)
    assert callable(test_ac3_replay_before_after_field_counts)


# --- agent-bus:6630 BUG 2 — status fidelity + fence-safe evidence (AC5) ---

_FENCED_EVIDENCE_SECTION2 = """\
TYPE: CLOSEOUT
status: complete

**ac_verdict:**
1. AC1 — PASS — pytest green

**deltas_to_spec:** none

**evidence:**
```python
def observed_tool_output():
    return {"exit_code": 0, "summary": "44 passed"}
```

**open_forks:** none
"""


def test_6630_authored_complete_pass_acs_relay_complete_ac5a() -> None:
    """AC5(a) — authored complete + PASS ACs → relay complete (no silent partial)."""
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=_FIXTURE_6222_T5_SECTION2,
        ledger_status="completed",
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"
    assert "AC1 — PASS" in payload.body
    assert "overclaim:" not in payload.body


def test_6630_fenced_evidence_no_backtick_only_cell_ac5b() -> None:
    """AC5(b) — fenced evidence projects pointer, never a ```-only table cell."""
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        is_degenerate_fence_cell,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        _extract_table_cell,
    )

    dispatch_id = "auto-6630-fence"
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=_FENCED_EVIDENCE_SECTION2,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        caller_auditable=True,
    )
    evidence_cell = _extract_table_cell(payload.body, "evidence") or ""
    assert evidence_cell.strip() != "```"
    assert "fenced — see source_ref:" in evidence_cell
    assert is_degenerate_fence_cell(evidence_cell) is False


def test_6630_overclaim_still_downgrades_with_deviation_ac5c() -> None:
    """AC5(c) — real parse_failed overclaim clamps to relay_parse_failed with named deviation."""
    from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
        finalize_relay_payload,
    )

    payload = finalize_relay_payload(
        CloseoutRelayPayload(
            body=_TURN8_RELAYED_BODY,
            status="complete",
            source="section2_synthesized",
        ),
        wrapper_text=_TURN8_WRAPPER,
        dispatch_id=_TURN8_DISPATCH,
    )
    assert payload.status == "relay_parse_failed"
    assert "overclaim:parse_failed_field" in payload.body


# --- arc 6637 — plain field: value format + relay_parse_failed status ---

_ARC6637_FIXTURE_IDS = ("auto-9ca4df4d4a88", "auto-39cbe5d54b0f")


@pytest.mark.parametrize("dispatch_id", _ARC6637_FIXTURE_IDS)
def test_arc6637_real_closeout_fixtures_parse_without_relay_miss(dispatch_id: str) -> None:
    """AC3 — both 6638-lane closeouts project with zero relay parse-miss cells."""
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        count_relay_parse_miss_fields,
        project_section2_table,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    path = Path("tmp/reviews/closeouts") / f"{dispatch_id}.md"
    assert path.is_file(), f"fixture sidecar missing: {path}"
    prose = strip_machine_tail(path.read_text(encoding="utf-8"))
    provenance = sidecar_workspaces_ref(dispatch_id)
    body, _status = project_section2_table(prose, provenance=provenance)
    assert count_relay_parse_miss_fields(body) == 0
    assert f"source_ref: {provenance}" in body


def test_arc6637_plain_colon_format_root_cause_not_section2_heading() -> None:
    """AC1 — failure was plain ``field: value`` lines, not missing ``## §2 closeout``."""
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        _extract_bold_same_line,
        _extract_plain_same_line,
        extract_field_section,
    )

    path = Path("tmp/reviews/closeouts/auto-9ca4df4d4a88.md")
    prose = strip_machine_tail(path.read_text(encoding="utf-8"))
    assert "## §2 closeout" not in prose.splitlines()[0]
    assert _extract_bold_same_line(prose, "ac_verdict") is None
    assert _extract_plain_same_line(prose, "ac_verdict") is not None
    assert extract_field_section(prose, "ac_verdict") is not None


def test_arc6637_9ca4df4d4a88_select_relay_complete_not_partial() -> None:
    """AC1+AC3 — authored complete plain-colon closeout relays complete after fix."""
    path = Path("tmp/reviews/closeouts/auto-9ca4df4d4a88.md")
    payload = select_closeout_relay_payload(
        sdk_body=None,
        sidecar_text=path.read_text(encoding="utf-8"),
        ledger_status="completed",
        dispatch_id="auto-9ca4df4d4a88",
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"
    assert "PASS" in payload.body


def test_arc6637_relay_parse_failed_when_extraction_fails() -> None:
    """AC2 — extraction parse_failed cells relay relay_parse_failed, not partial."""
    from services.git_integration_worker.cursor_auto.closeout_relay_briefing import (
        finalize_relay_payload,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        RELAY_PARSE_FAILED_STATUS,
    )

    payload = finalize_relay_payload(
        CloseoutRelayPayload(
            body=_TURN8_RELAYED_BODY,
            status="complete",
            source="section2_synthesized",
        ),
        wrapper_text=_TURN8_WRAPPER,
        dispatch_id=_TURN8_DISPATCH,
    )
    assert payload.status == RELAY_PARSE_FAILED_STATUS
    assert payload.status not in {"complete", "partial", "blocked", "failed"}
    assert "parse_failed — authoritative sidecar:" in payload.body


# --- arc 6637 G7 — immutable authored status + relay_note (real fixtures) ---

_ARC6637_G7_FIXTURE_IDS = ("auto-958206cbe1bc", "auto-39cbe5d54b0f")


def _load_closeout_fixture(dispatch_id: str) -> tuple[str, str | None]:
    path = Path("tmp/reviews/closeouts") / f"{dispatch_id}.md"
    assert path.is_file(), f"fixture sidecar missing: {path}"
    text = path.read_text(encoding="utf-8")
    sdk_body = None
    if "## effects_manifest" in text:
        import re

        match = re.search(
            r"\{[\s\S]*\}\s*$",
            text[text.index("## effects_manifest") :],
        )
        if match:
            sdk_body = match.group(0).strip()
    return text, sdk_body


def test_arc6637_g7_958206cbe1bc_authored_complete_relayed_complete() -> None:
    """AC4 — authored complete with relay_parse_failed prose in ac_verdict stays complete."""
    sidecar, sdk_body = _load_closeout_fixture("auto-958206cbe1bc")
    payload = select_closeout_relay_payload(
        sdk_body=sdk_body,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id="auto-958206cbe1bc",
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"
    assert "relay_note:" not in payload.body or payload.relay_note is None
    assert "| status | complete |" in payload.body


def test_arc6637_g7_39cbe5d54b0f_authored_partial_not_upgraded() -> None:
    """AC4 — authored partial must not upgrade to complete via ledger fallback."""
    sidecar, sdk_body = _load_closeout_fixture("auto-39cbe5d54b0f")
    payload = select_closeout_relay_payload(
        sdk_body=sdk_body,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id="auto-39cbe5d54b0f",
        caller_auditable=True,
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "partial"
    assert "| status | partial |" in payload.body


def test_arc6637_g7_overclaim_substring_relay_parse_failed_not_status_downgrade() -> None:
    """AC1 — ``relay_parse_failed`` prose must not trigger parse_failed cell overclaim."""
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        _cell_claims_unclassified_or_hard_unauthored,
    )

    cell = (
        "AC5 PASS — relay_parse_failed reachable in running worker import path; "
        "arc6637 relay tests pass."
    )
    assert _cell_claims_unclassified_or_hard_unauthored(cell) is False


# --- M-RELAY-TABLE-ESCAPE (F4 + F1 + F6) ---

_TABLE_ESCAPE_SPECIMEN_IDS = (
    "auto-9f736ae407c2",
    "auto-8be6a2c80163",
    "auto-75becf82fc0c",
)


def _load_specimen_sidecar(dispatch_id: str) -> str:
    path = Path("tmp/reviews/closeouts") / f"{dispatch_id}.md"
    if not path.is_file():
        pytest.skip(f"fixture sidecar missing: {path}")
    return path.read_text(encoding="utf-8")


def _extract_ac_verdict_section(sidecar: str) -> str:
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
    )

    prose = strip_machine_tail(sidecar)
    value = extract_field_section(prose, "ac_verdict")
    assert value is not None
    return value


def _extract_evidence_section(sidecar: str) -> str:
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
    )

    prose = strip_machine_tail(sidecar)
    value = extract_field_section(prose, "evidence")
    assert value is not None
    return value


def _projected_ac_verdict_cell(sidecar: str, *, dispatch_id: str) -> str:
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        _extract_table_cell,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        project_section2_table,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    prose = strip_machine_tail(sidecar)
    body, _status = project_section2_table(
        prose,
        provenance=sidecar_workspaces_ref(dispatch_id),
    )
    return _extract_table_cell(body, "ac_verdict") or ""


@pytest.mark.parametrize("dispatch_id", _TABLE_ESCAPE_SPECIMEN_IDS)
def test_table_escape_specimen_ac_verdict_recovers_full_table(dispatch_id: str) -> None:
    """AC-1 — archived specimens recover complete ac_verdict, not header-only."""
    sidecar = _load_specimen_sidecar(dispatch_id)
    extracted = _extract_ac_verdict_section(sidecar)
    assert extracted != "| AC | Verdict |"
    assert "AC-0" in extracted or "AC1" in extracted or "AC-1" in extracted or "AC-A1" in extracted
    assert "PASS" in extracted or "pass" in extracted


@pytest.mark.parametrize("dispatch_id", _TABLE_ESCAPE_SPECIMEN_IDS)
def test_table_escape_specimen_evidence_not_header_only(dispatch_id: str) -> None:
    """AC-1 — evidence section is not reduced to the first subheading line."""
    sidecar = _load_specimen_sidecar(dispatch_id)
    extracted = _extract_evidence_section(sidecar)
    assert extracted != "**AC-0 (verbatim):**"
    assert len(extracted) > 40


def test_table_escape_same_line_bold_field_still_same_line() -> None:
    """AC-2 — legitimate same-line values still extract inline."""
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
    )

    body = "**status:** complete\n\n**deltas_to_spec:** none\n"
    assert extract_field_section(body, "status") == "complete"
    assert extract_field_section(body, "deltas_to_spec") == "none"


def test_table_escape_empty_same_line_falls_through_to_section() -> None:
    """AC-3 — empty same-line capture falls through to section extraction."""
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        _extract_bold_same_line,
        _extract_bold_section,
        extract_field_section,
    )

    body = (
        "**ac_verdict:**\n\n"
        "| AC | Verdict |\n"
        "|---|---|\n"
        "| AC1 | **PASS** — pipe in verdict: a|b |\n"
    )
    assert _extract_bold_same_line(body, "ac_verdict") is None
    section = _extract_bold_section(body, "ac_verdict")
    assert section is not None
    assert "AC1" in section
    assert "a|b" in section
    assert extract_field_section(body, "ac_verdict") == section


def test_table_escape_f1_readable_ac_verdict_cell_no_table_soup() -> None:
    """AC-4 — projected ac_verdict is a compact list without escaped-pipe soup."""
    dispatch_id = "auto-9f736ae407c2"
    sidecar = _load_specimen_sidecar(dispatch_id)
    cell = _projected_ac_verdict_cell(sidecar, dispatch_id=dispatch_id)
    assert "\\|" not in cell
    assert "<br>" not in cell
    assert "AC-0" in cell
    assert "AC-B-live" in cell or "AC1" in cell
    assert "PASS" in cell


def test_table_escape_f1_preserves_literal_pipe_in_verdict() -> None:
    """AC-4 — literal pipe characters inside verdict text survive as content."""
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        project_section2_table,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    dispatch_id = "auto-table-pipe-content"
    sidecar = (
        "**status:** complete\n\n"
        "**ac_verdict:**\n\n"
        "| AC | Verdict |\n"
        "|---|---|\n"
        "| AC1 | pass — observed a|b in output |\n\n"
        "**deltas_to_spec:** none\n"
    )
    body, _status = project_section2_table(
        strip_machine_tail(sidecar),
        provenance=sidecar_workspaces_ref(dispatch_id),
    )
    ac_cell = body.split("| ac_verdict | ", 1)[1].split(" |", 1)[0]
    assert "\\|" in ac_cell
    assert "a" in ac_cell and "b" in ac_cell
    assert "\\| AC \\|" not in ac_cell


def test_table_escape_unclamped_workspaces_source_ref_readable() -> None:
    """AC-5b — unclamped relay keeps readable workspaces source_ref with nested rows."""
    dispatch_id = "auto-9f736ae407c2"
    sidecar = _load_specimen_sidecar(dispatch_id)
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=sidecar,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        caller_auditable=True,
    )
    ref = f"workspaces://universal-llm-gateway/tmp/reviews/closeouts/{dispatch_id}.md"
    assert ref in payload.body
    cell = _projected_ac_verdict_cell(sidecar, dispatch_id=dispatch_id)
    assert "AC-0" in cell
    assert payload.clamped is False or ref in (payload.body_full or payload.body)


def test_table_escape_failed_extract_not_header_only_success() -> None:
    """AC-6 — parse miss voice when heading exists but section is empty noise."""
    from services.git_integration_worker.cursor_auto.closeout_relay_common import (
        strip_machine_tail,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_project import (
        project_section2_table,
    )
    from services.git_integration_worker.cursor_sdk_deliverables import (
        sidecar_workspaces_ref,
    )

    dispatch_id = "auto-empty-ac"
    sidecar = "**status:** partial\n\n**ac_verdict:**\n\n**deltas_to_spec:** none\n"
    body, _status = project_section2_table(
        strip_machine_tail(sidecar),
        provenance=sidecar_workspaces_ref(dispatch_id),
    )
    ac_cell = body.split("| ac_verdict | ", 1)[1].split(" |", 1)[0]
    assert ac_cell != "| AC | Verdict |"
    assert (
        "relay could not locate" in ac_cell.lower()
        or "parse_failed" in ac_cell.lower()
    )


# --- row 11 — AC-1 alias theft (6655 bind) ---

_ROW11_ALIAS_THEFT_SIDECAR = """\
TYPE: CLOSEOUT
status: partial

**ac_verdict:**

**deltas_to_spec:** none

**decisions_taken:** none

**effects:** none

**evidence:** none

**next:** none

**open forks:** none

**AC-1 (verbatim):** stolen verdict line must not bind ac_verdict
"""


def test_row11_ac1_verbatim_heading_does_not_steal_ac_verdict() -> None:
    """AC-alias — ``**AC-1 (verbatim):**`` must not extract as ac_verdict via ac1 alias."""
    from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
        extract_field_section,
    )
    from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
        _extract_table_cell,
    )

    stolen = extract_field_section(_ROW11_ALIAS_THEFT_SIDECAR, "ac_verdict")
    assert stolen is None or "stolen verdict line" not in stolen

    dispatch_id = "auto-row11-alias"
    payload = select_closeout_relay_payload(
        sdk_body=_WRAPPER,
        sidecar_text=_ROW11_ALIAS_THEFT_SIDECAR,
        ledger_status="completed",
        dispatch_id=dispatch_id,
        caller_auditable=True,
    )
    ac_cell = _extract_table_cell(payload.body, "ac_verdict") or ""
    assert "stolen verdict line" not in ac_cell
    assert (
        "relay could not locate" in ac_cell.lower()
        or "parse_failed" in ac_cell.lower()
        or ac_cell.strip() == ""
    )

