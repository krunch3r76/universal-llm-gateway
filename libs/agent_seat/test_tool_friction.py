"""Tests for ToolFrictionTracker (C1: distinct-turn halt) and classify_tool_failure (C2: generalised cortex/observability classification)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from agent_seat.tool_friction import ToolFrictionTracker, classify_tool_failure


@dataclass
class FakeCall:
    turn: int
    name: str
    arguments: dict
    result: str
    ok: bool
    elapsed_ms: float = 0.0


def make_cortex_call(
    turn: int,
    inner_tool: str,
    inner_args: dict,
    result: str = "error",
    ok: bool = False,
) -> FakeCall:
    return FakeCall(
        turn=turn,
        name="cortex",
        arguments={"tool": inner_tool, "arguments": json.dumps(inner_args)},
        result=result,
        ok=ok,
    )


# ---------------------------------------------------------------------------
# C1 — distinct-turn halt predicate
# ---------------------------------------------------------------------------


def test_observe_cross_turn_same_key_trips() -> None:
    tracker = ToolFrictionTracker()
    call1 = make_cortex_call(1, "entity_get", {"entity_id": "foo"})
    call2 = make_cortex_call(2, "entity_get", {"entity_id": "foo"})
    tracker.observe(call1)
    assert not tracker.should_stop
    tracker.observe(call2)
    assert tracker.should_stop


def test_observe_same_turn_parallel_failures_dont_trip() -> None:
    # KEY BUG TEST: N calls with the same key all on turn=1 must NOT halt.
    tracker = ToolFrictionTracker()
    call = make_cortex_call(1, "entity_get", {"entity_id": "foo"})
    for _ in range(5):
        tracker.observe(call)
    assert not tracker.should_stop


def test_observe_same_turn_duplicate_key_doesnt_trip() -> None:
    tracker = ToolFrictionTracker()
    for _ in range(3):
        tracker.observe(make_cortex_call(1, "entity_get", {"entity_id": "foo"}))
    assert not tracker.should_stop


# ---------------------------------------------------------------------------
# C2 — classify_tool_failure generalisation
# ---------------------------------------------------------------------------


def test_classify_cortex_entity_get_distinguishes_entities() -> None:
    f1 = classify_tool_failure(
        "cortex",
        {"tool": "entity_get", "arguments": json.dumps({"entity_id": "foo"})},
        "error",
    )
    f2 = classify_tool_failure(
        "cortex",
        {"tool": "entity_get", "arguments": json.dumps({"entity_id": "bar"})},
        "error",
    )
    assert f1["tool"] == "cortex.entity_get"
    assert f2["tool"] == "cortex.entity_get"
    assert f1["target"] == "foo"
    assert f2["target"] == "bar"
    key1 = (f1["tool"], f1["code"], f1["target"])
    key2 = (f2["tool"], f2["code"], f2["target"])
    assert key1 != key2


def test_classify_wrapped_cortex_uses_namespaced_tool_name() -> None:
    failure = classify_tool_failure(
        "cortex",
        {"tool": "entity_update", "arguments": json.dumps({"id": "abc"})},
        "error",
    )
    assert failure["tool"] == "cortex.entity_update"
    assert failure["target"] == "abc"


def test_classify_observability_uses_operation_target() -> None:
    args = {"params": {"operation": "pipeline-trace", "execution_id": "exec-123"}}
    failure = classify_tool_failure("observability", args, "error")
    assert failure["tool"] == "observability.pipeline-trace"
    assert failure["target"] == "pipeline-trace:exec-123"
    assert failure["code"] == "tool_error"


def test_classify_observability_schema_error_uses_missing_field_target() -> None:
    args = {"params": {"operation": "pipeline-trace"}}
    failure = classify_tool_failure("observability", args, "error")
    assert failure["tool"] == "observability.pipeline-trace"
    assert failure["target"] == "pipeline-trace:execution_id"
    assert failure["code"] == "missing_required_argument"


def test_classify_same_observability_args_across_two_turns_trips() -> None:
    tracker = ToolFrictionTracker()
    args = {"params": {"operation": "pipeline-trace", "execution_id": "exec-123"}}
    tracker.observe(
        FakeCall(turn=1, name="observability", arguments=args, result="error", ok=False)
    )
    tracker.observe(
        FakeCall(turn=2, name="observability", arguments=args, result="error", ok=False)
    )
    assert tracker.should_stop


def test_classify_different_observability_args_dont_collapse() -> None:
    f1 = classify_tool_failure(
        "observability",
        {"params": {"operation": "pipeline-trace", "execution_id": "exec-1"}},
        "error",
    )
    f2 = classify_tool_failure(
        "observability",
        {"params": {"operation": "pipeline-trace", "execution_id": "exec-2"}},
        "error",
    )
    assert f1["target"] != f2["target"]


def test_classify_malformed_inner_json_falls_back_to_stable_hash() -> None:
    args = {"tool": "entity_get", "arguments": "NOT_VALID_JSON"}
    failure = classify_tool_failure("cortex", args, "error")
    assert failure["target"].startswith("args:")
    assert len(failure["target"]) > 5


def test_classify_cortex_entity_create_409_preserved() -> None:
    args = {"tool": "entity_create", "arguments": json.dumps({"id": "test-entity"})}
    failure = classify_tool_failure(
        "cortex", args, '{"error": "HTTP 409: Entity already exists"}'
    )
    assert failure["code"] == "entity_exists"
    assert failure["tool"] == "cortex.entity_create"
    assert failure["target"] == "test-entity"


# ---------------------------------------------------------------------------
# build_summary — distinct_turns field
# ---------------------------------------------------------------------------


def test_build_summary_includes_distinct_turns_and_raw_count() -> None:
    tracker = ToolFrictionTracker()
    calls = [
        make_cortex_call(1, "entity_get", {"entity_id": "foo"}),
        make_cortex_call(
            1, "entity_get", {"entity_id": "foo"}
        ),  # same turn — still counts raw
        make_cortex_call(2, "entity_get", {"entity_id": "foo"}),  # different turn
    ]
    for c in calls:
        tracker.observe(c)
    summary = tracker.build_summary(execution_id="ex-1", turns_used=3, tool_calls=calls)
    assert summary["failed_tools"]
    item = summary["failed_tools"][0]
    assert item["count"] == 3
    assert item["distinct_turns"] == [1, 2]


# ---------------------------------------------------------------------------
# Misc / regression
# ---------------------------------------------------------------------------


def test_should_skip_agent_consult_cap_unchanged() -> None:
    tracker = ToolFrictionTracker()
    args = {"tool": "agent_consult"}
    skip1 = tracker.should_skip("dispatch", args, remaining_turns=5)
    assert skip1 is None  # first call allowed — counter incremented
    skip2 = tracker.should_skip("dispatch", args, remaining_turns=5)
    assert skip2 is not None
    assert skip2.reason == "agent_consult_cap"


def test_observe_unicode_and_special_chars_in_args_hash_stable() -> None:
    # Inner args have no priority keys → falls through to stable args hash.
    args = {
        "tool": "entity_search",
        "arguments": json.dumps({"filter": "héllo wörld 日本語"}),
    }
    f1 = classify_tool_failure("cortex", args, "error")
    f2 = classify_tool_failure("cortex", args, "error")
    assert f1["target"].startswith("args:")
    assert f1["target"] == f2["target"]
