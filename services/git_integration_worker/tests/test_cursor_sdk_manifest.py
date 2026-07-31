"""Unit tests for cursor-sdk effects manifest builder."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_capture_status import ChangeSet
from services.git_integration_worker.cursor_sdk_closeout import (
    SdkRunOutcome,
    degraded_implement_reason,
)
from services.git_integration_worker.cursor_sdk_manifest import (
    build_effects_manifest,
    classify_mcp_capture_branch,
    merge_artifact_paths,
    merge_stream_tool_calls,
    repo_change_set_from_manifest,
    resolve_repo_change_set,
)
from services.git_integration_worker.cursor_sdk_stream_capture import ToolCallObservation


def _toolcall_step(message: dict) -> object:
    return type(
        "ToolCallConversationStep", (), {"type": "toolCall", "message": message}
    )()


def _dict_toolcall_step(message: dict) -> dict:
    return {"type": "toolCall", "message": message}


def _agent_turn(*steps: object) -> object:
    agent_turn = type("AgentConversationTurn", (), {"steps": tuple(steps)})()
    return type("ConversationTurn", (), {"turn": agent_turn})()


def test_classify_branch_a_when_mcp_toolcall_present() -> None:
    turn = _agent_turn(
        _toolcall_step(
            {
                "type": "mcp",
                "args": {
                    "toolName": "cortex",
                    "providerIdentifier": "user-vortex",
                    "args": {"tool": "entity_get", "arguments": {"id": "todo:x"}},
                },
            }
        ),
        _toolcall_step({"type": "shell", "args": {"command": "echo hi"}}),
    )
    assert classify_mcp_capture_branch([turn]) == "A"


def test_classify_branch_b_without_mcp_toolcall() -> None:
    turn = _agent_turn(
        _toolcall_step({"type": "write", "args": {"path": "services/foo.py"}}),
        _toolcall_step({"type": "shell", "args": {"command": "echo hi"}}),
    )
    assert classify_mcp_capture_branch([turn]) == "B"


def test_classify_no_capture_when_no_recognized_toolcalls() -> None:
    assert classify_mcp_capture_branch([]) == "NO_CAPTURE"
    turn = _agent_turn(_toolcall_step({"type": "thinking"}))
    assert classify_mcp_capture_branch([turn]) == "NO_CAPTURE"


def test_no_capture_hard_stop_degrades_implement_closeout() -> None:
    outcome = SdkRunOutcome(
        body="done",
        status="finished",
        duration_ms=100,
        tool_call_count=1,
        capture_branch="NO_CAPTURE",
    )
    assert degraded_implement_reason(outcome) == "no_capture_evidence"


def test_build_effects_manifest_multi_surface_branch_a() -> None:
    turn = _agent_turn(
        _toolcall_step({"type": "write", "args": {"path": "services/foo.py"}}),
        _toolcall_step(
            {
                "type": "mcp",
                "args": {
                    "toolName": "cortex",
                    "providerIdentifier": "user-vortex",
                    "args": {"tool": "entity_get", "arguments": {"id": "todo:abc"}},
                },
            }
        ),
        _toolcall_step(
            {
                "type": "mcp",
                "args": {
                    "toolName": "agent_bus",
                    "providerIdentifier": "user-vortex",
                    "args": {"thread_id": "2809", "turn_number": 12},
                },
            }
        ),
    )
    manifest = build_effects_manifest(
        dispatch_id="d1",
        thread_id="t1",
        turns=[turn],
        capture_branch="A",
    )
    assert manifest.external_effects == "scoped_out"
    assert "repo" in manifest.surfaces
    assert "cortex" in manifest.surfaces
    assert "agent_bus" in manifest.surfaces
    assert manifest.coverage["repo"] == "complete"
    repo_cs, _, _ = repo_change_set_from_manifest(manifest)
    assert repo_cs is not None
    assert "services/foo.py" in repo_cs.created


def test_resolve_repo_change_set_unions_git_and_manifest_paths() -> None:
    turn = _agent_turn(
        _toolcall_step({"type": "edit", "args": {"path": "services/a.py"}}),
        _toolcall_step({"type": "shell", "args": {"command": "python generate.py"}}),
    )
    manifest = build_effects_manifest(
        dispatch_id="d-union",
        thread_id="t-union",
        turns=[turn],
        capture_branch="B",
    )
    git_change_set = ChangeSet(
        created=("services/b.py",),
        modified=("services/a.py",),
        deleted=(),
    )
    resolved, _, _ = resolve_repo_change_set(
        manifest=manifest,
        git_change_set=git_change_set,
    )
    assert "services/a.py" in resolved.modified
    assert "services/b.py" in resolved.created


def test_nested_arguments_target_extraction() -> None:
    turn = _agent_turn(
        _toolcall_step(
            {
                "type": "mcp",
                "args": {
                    "toolName": "cortex",
                    "providerIdentifier": "user-vortex",
                    "args": {
                        "tool": "assert",
                        "arguments": '{"entity_id":"notes/system/specs/foo.md"}',
                    },
                },
            }
        ),
        _toolcall_step(
            {
                "type": "mcp",
                "args": {
                    "toolName": "agent_bus",
                    "providerIdentifier": "user-vortex",
                    "args": {
                        "tool": "send",
                        "arguments": '{"thread":"2809","turn_number":42}',
                    },
                },
            }
        ),
        _toolcall_step(
            {
                "type": "mcp",
                "args": {
                    "toolName": "fs",
                    "providerIdentifier": "user-vortex",
                    "args": {
                        "tool": "read",
                        "arguments": '{"sandbox":"cortex","path":"notes/foo.md"}',
                    },
                },
            }
        ),
    )
    manifest = build_effects_manifest(
        dispatch_id="d-nested",
        thread_id="t-nested",
        turns=[turn],
        capture_branch="A",
    )
    cortex_entry = manifest.surfaces["cortex"].entries[0]
    assert cortex_entry.target == "notes/system/specs/foo.md"
    bus_entry = manifest.surfaces["agent_bus"].entries[0]
    assert bus_entry.target == "2809#42"
    fs_entry = manifest.surfaces["fs"].entries[0]
    assert fs_entry.target == "cortex:notes/foo.md"


def test_rag_surface_not_bucketed_under_fs() -> None:
    turn = _agent_turn(
        _toolcall_step(
            {
                "type": "mcp",
                "args": {
                    "toolName": "rag",
                    "providerIdentifier": "user-vortex",
                    "args": {
                        "tool": "upsert_article",
                        "arguments": '{"op":"upsert_article","source_hash":"abc123"}',
                    },
                },
            }
        ),
    )
    manifest = build_effects_manifest(
        dispatch_id="d-rag",
        thread_id="t-rag",
        turns=[turn],
        capture_branch="A",
    )
    assert "rag" in manifest.surfaces
    assert "fs" not in manifest.surfaces
    assert manifest.surfaces["rag"].entries[0].target == "upsert_article"


def test_merge_mcp_event_entries_skips_non_mapping_events() -> None:
    manifest = build_effects_manifest(
        dispatch_id="d-bad-events",
        thread_id="t-bad-events",
        turns=[],
        capture_branch="B",
        mcp_events=["not-a-mapping", None, {"tool_name": "manage", "operation": "sync_restart"}],
    )
    assert "service" in manifest.surfaces
    assert len(manifest.surfaces["service"].entries) == 1


def test_iter_tool_call_messages_supports_dict_shaped_steps() -> None:
    turn = {
        "turn": {
            "steps": [
                _dict_toolcall_step(
                    {"type": "write", "args": {"path": "services/dict.py"}}
                )
            ]
        }
    }
    manifest = build_effects_manifest(
        dispatch_id="d-dict",
        thread_id="t-dict",
        turns=[turn],
        capture_branch="B",
    )
    assert "repo" in manifest.surfaces
    assert manifest.surfaces["repo"].entries[0].target == "services/dict.py"


def test_agent_bus_new_slug_target_extraction() -> None:
    turn = _agent_turn(
        _toolcall_step(
            {
                "type": "mcp",
                "args": {
                    "toolName": "agent_bus",
                    "providerIdentifier": "user-vortex",
                    "args": {
                        "tool": "send",
                        "arguments": '{"new_slug":"2823-reviewer-reply","to":"reviewer"}',
                    },
                },
            }
        ),
    )
    manifest = build_effects_manifest(
        dispatch_id="d-new-slug",
        thread_id="t-new-slug",
        turns=[turn],
        capture_branch="A",
    )
    bus_entry = manifest.surfaces["agent_bus"].entries[0]
    assert bus_entry.target == "2823-reviewer-reply"


def test_build_effects_manifest_never_raises_on_bad_wire() -> None:
    turn = _agent_turn(_toolcall_step({"type": "toolCall"}))
    manifest = build_effects_manifest(
        dispatch_id="d2",
        thread_id="t2",
        turns=[turn],
    )
    assert manifest.dispatch_id == "d2"


def test_merge_stream_tool_calls_adds_missing_repo_path() -> None:
    manifest = build_effects_manifest(
        dispatch_id="d-stream",
        thread_id="t-stream",
        turns=[],
        capture_branch="B",
    )
    tool_calls = (
        ToolCallObservation(
            call_id="c1",
            tool_name="write",
            status="completed",
            arg_bytes=10,
            result_bytes=0,
            truncated_fields=(),
            target_path="services/from_stream.py",
        ),
    )
    merged = merge_stream_tool_calls(manifest, tool_calls)
    assert merged is not None
    paths = {entry.target for entry in merged.surfaces["repo"].entries}
    assert "services/from_stream.py" in paths
    assert "stream" in merged.capture_sources


def test_merge_stream_tool_calls_dedupes_existing_paths() -> None:
    turn = _agent_turn(
        _toolcall_step({"type": "write", "args": {"path": "services/existing.py"}}),
    )
    manifest = build_effects_manifest(
        dispatch_id="d-dedupe",
        thread_id="t-dedupe",
        turns=[turn],
        capture_branch="B",
    )
    tool_calls = (
        ToolCallObservation(
            call_id="c1",
            tool_name="write",
            status="completed",
            arg_bytes=10,
            result_bytes=0,
            truncated_fields=(),
            target_path="services/existing.py",
        ),
    )
    merged = merge_stream_tool_calls(manifest, tool_calls)
    assert merged is not None
    repo_entries = merged.surfaces["repo"].entries
    assert sum(1 for entry in repo_entries if entry.target == "services/existing.py") == 1


def test_merge_artifact_paths_empty_noop() -> None:
    manifest = build_effects_manifest(
        dispatch_id="d-artifacts",
        thread_id="t-artifacts",
        turns=[],
        capture_branch="B",
    )
    assert merge_artifact_paths(manifest, [], source_repo=None) is manifest


def test_merge_artifact_paths_folds_non_empty() -> None:
    manifest = build_effects_manifest(
        dispatch_id="d-artifacts",
        thread_id="t-artifacts",
        turns=[],
        capture_branch="B",
    )
    merged = merge_artifact_paths(
        manifest,
        ["artifacts/output.md"],
        source_repo=None,
    )
    assert merged is not None
    paths = {entry.target for entry in merged.surfaces["repo"].entries}
    assert "artifacts/output.md" in paths
    assert "artifacts" in merged.capture_sources
