"""Unit tests for cursor-auto §2 CLOSEOUT relay selection."""

from __future__ import annotations

import json
from pathlib import Path

from services.git_integration_worker.cursor_auto.closeout_relay import (
    is_wrapper_manifest,
    looks_section2,
    select_closeout_relay_payload,
    status_from_section2,
    strip_machine_tail,
    synthesize_section2,
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


def test_synthesized_status_forced_partial_even_when_wrapper_complete():
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
    )
    assert payload.source == "section2_synthesized"
    assert payload.status == "partial"


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
    assert "wedge regression" in payload.body


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
    assert "unauthored" in payload.body.lower()


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
    assert payload.body == _SECTION2.strip()


def test_no_cortex_uri_synthesize_unchanged():
    payload = select_closeout_relay_payload(
        sdk_body=_T9_WRAPPER,
        sidecar_text=None,
        ledger_status="completed",
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
    assert "truncated" in payload.body.lower()
    assert uri in payload.body


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
    )
    assert payload.source == "section2_sidecar"
    assert _T9_OFFGIT_A in payload.body
    assert "no repo writes" not in payload.body.lower()
    assert "AC1 — PASS" in payload.body
    assert payload.status != "complete"


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
    )
    assert payload.source == "section2_sidecar"
    assert uri in payload.body
    assert payload.status != "complete"


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
    )
    assert payload.source == "section2_sidecar"
    assert payload.status == "complete"
    assert looks_section2(payload.body)
    assert "AC1 — PASS" in payload.body


def test_enforce_synthesized_partial_branches():
    """AC6 — synthesized §2 forced partial; authored source preserves status."""
    assert (
        enforce_synthesized_partial("complete", closeout_source="section2_synthesized")
        == "partial"
    )
    assert (
        enforce_synthesized_partial("complete", closeout_source="section2_sidecar")
        == "complete"
    )
