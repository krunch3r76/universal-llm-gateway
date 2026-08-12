"""Tests for subagent/Task capture on cursor-sdk effects_manifest (item 9 / AC-9b, AC-9c)."""

from __future__ import annotations

import json

import pytest
from implement_admission.closeout_models import EffectsManifest

from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_manifest import build_effects_manifest
from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)
from services.git_integration_worker.cursor_sdk_subagent_capture import (
    SUBAGENTS_SURFACE,
    ensure_subagents_surface,
    entry_from_subagent_message,
    merge_stream_subagent_calls,
    subagent_type_from_stream_args,
)

pytestmark = pytest.mark.offline


def _closeout_payload(manifest: EffectsManifest, **kwargs: object) -> dict[str, object]:
    body = build_implement_closeout_body(
        dispatch_id="dispatch-1",
        outcome=SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=1000,
            tool_call_count=1,
            effects_manifest=manifest,
        ),
        degraded_reason=None,
        sidecar_ref="workspaces://repo/sidecar.md",
        result_bytes=100,
        thread_id="thread-1",
        work_item_ref="todo:foo",
        effects_manifest=manifest,
        **kwargs,
    )
    return json.loads(body)


def test_build_effects_manifest_always_emits_empty_subagents_surface() -> None:
    manifest = build_effects_manifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        turns=[],
    )
    section = manifest.surfaces[SUBAGENTS_SURFACE]
    assert section.entries == []
    assert section.surface == SUBAGENTS_SURFACE


def test_conversation_task_entry_on_subagents_surface() -> None:
    turns = [
        {
            "turn": {
                "steps": [
                    {
                        "type": "toolCall",
                        "message": {
                            "type": "Task",
                            "call_id": "call-explore-1",
                            "args": {
                                "subagent_type": "explore",
                                "description": "probe",
                                "prompt": "count files",
                            },
                        },
                    }
                ]
            }
        }
    ]
    manifest = build_effects_manifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        turns=turns,
    )
    entries = manifest.surfaces[SUBAGENTS_SURFACE].entries
    assert len(entries) == 1
    assert entries[0].op == "Task"
    assert entries[0].target == "explore"
    assert entries[0].identity == "call-explore-1"
    assert entries[0].detail is not None
    assert entries[0].detail["subagent_type"] == "explore"


def test_conversation_task_entry_wire_subagent_type_kind() -> None:
    turns = [
        {
            "turn": {
                "steps": [
                    {
                        "type": "toolCall",
                        "message": {
                            "type": "Task",
                            "call_id": "call-explore-wire",
                            "args": {
                                "subagentType": {"kind": "explore", "name": "Explore"},
                                "description": "breadth recon",
                            },
                        },
                    }
                ]
            }
        }
    ]
    manifest = build_effects_manifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        turns=turns,
    )
    entries = manifest.surfaces[SUBAGENTS_SURFACE].entries
    assert len(entries) == 1
    assert entries[0].target == "explore"
    assert entries[0].detail is not None
    assert entries[0].detail["subagent_type"] == "explore"
    assert "subagentType" not in entries[0].detail


def test_subagent_type_from_stream_args_wire_and_legacy() -> None:
    assert subagent_type_from_stream_args(
        "Task",
        {"subagentType": {"kind": "explore"}},
    ) == "explore"
    assert subagent_type_from_stream_args(
        "Task",
        {"subagentType": {"kind": "generalPurpose"}},
    ) == "generalPurpose"
    assert subagent_type_from_stream_args(
        "Task",
        {"subagent_type": "explore"},
    ) == "explore"


def test_entry_from_subagent_message_wire_subagent_type_kind() -> None:
    entry = entry_from_subagent_message(
        {
            "type": "Task",
            "call_id": "call-gp-wire",
            "args": {
                "subagentType": {"kind": "generalPurpose"},
                "description": "cheap probe",
            },
        }
    )
    assert entry is not None
    assert entry.target == "generalPurpose"
    assert entry.detail is not None
    assert entry.detail["subagent_type"] == "generalPurpose"


def test_merge_stream_subagent_calls_adds_missing_task() -> None:
    manifest = build_effects_manifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        turns=[],
    )
    merged = merge_stream_subagent_calls(
        manifest,
        (
            ToolCallObservation(
                call_id="stream-call-1",
                tool_name="Task",
                status="completed",
                arg_bytes=10,
                result_bytes=20,
                truncated_fields=(),
                subagent_type="generalPurpose",
            ),
        ),
    )
    assert merged is not None
    entries = merged.surfaces[SUBAGENTS_SURFACE].entries
    assert len(entries) == 1
    assert entries[0].identity == "stream-call-1"
    assert entries[0].target == "generalPurpose"


def test_closeout_subagents_explicit_empty_is_pass() -> None:
    manifest = build_effects_manifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        turns=[],
    )
    payload = _closeout_payload(manifest, capture_status="complete")
    subagents = payload["effects_manifest"]["surfaces"][SUBAGENTS_SURFACE]
    assert subagents["entries"] == []


def test_closeout_no_fabricated_subagents_when_no_task() -> None:
    manifest = ensure_subagents_surface(
        EffectsManifest(dispatch_id="dispatch-1", thread_id="thread-1")
    )
    payload = _closeout_payload(manifest, capture_status="complete")
    subagents = payload["effects_manifest"]["surfaces"][SUBAGENTS_SURFACE]
    assert subagents["entries"] == []
    assert payload["evidence_uris"]["cortex_assertions"] == []


def test_closeout_lists_task_invocations_ac9b() -> None:
    turns = [
        {
            "turn": {
                "steps": [
                    {
                        "type": "toolCall",
                        "message": {
                            "type": "Task",
                            "call_id": "call-gp-1",
                            "args": {
                                "subagent_type": "generalPurpose",
                                "description": "cheap probe",
                            },
                        },
                    }
                ]
            }
        }
    ]
    manifest = build_effects_manifest(
        dispatch_id="dispatch-1",
        thread_id="thread-1",
        turns=turns,
    )
    payload = _closeout_payload(manifest, capture_status="complete")
    entries = payload["effects_manifest"]["surfaces"][SUBAGENTS_SURFACE]["entries"]
    assert len(entries) == 1
    assert entries[0]["op"] == "Task"
    assert entries[0]["target"] == "generalPurpose"
