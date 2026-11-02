"""SDK multi-row posture classifier — View labels for nested / id_split / parallel."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core.board_lines import (
    sdk_flight_fragment,
    sdk_unstarted_line,
)
from scripts.model_manager.ui.dispatch_monitor.core.dtos import SdkDispatchRow
from scripts.model_manager.ui.dispatch_monitor.core.sdk_posture import (
    ROW_TAG,
    classify_sdk_activity,
    classify_sdk_live,
    posture_legend,
    row_role,
    sdk_flight_label,
    sort_sdk_live,
    split_open_sdk,
)


def _row(
    dispatch_id: str,
    *,
    root_id: str | None = None,
    thread_id: str | None = None,
    state: str = "running",
    tool_call_count: int | None = None,
    last_tool_name: str | None = None,
    idle_age_ms: int | None = 0,
    elapsed_ms: int | None = None,
    parent_execution_id: str | None = None,
    review_child: bool = False,
    model: str | None = None,
    emitters_seen: tuple[str, ...] = (),
    caller_via: str | None = None,
) -> SdkDispatchRow:
    return SdkDispatchRow(
        dispatch_id=dispatch_id,
        root_id=root_id,
        thread_id=thread_id,
        state=state,
        tool_call_count=tool_call_count,
        last_tool_name=last_tool_name,
        idle_age_ms=idle_age_ms,
        elapsed_ms=elapsed_ms,
        parent_execution_id=parent_execution_id,
        review_child=review_child,
        model=model,
        emitters_seen=emitters_seen,
        caller_via=caller_via,
    )


def test_solo_when_single_live() -> None:
    live = [_row("a", root_id="6186")]
    assert classify_sdk_live(live) == "solo"
    assert posture_legend("solo") is None
    assert row_role(live[0], live, "solo") is None


def test_nested_when_parent_parked() -> None:
    live = [
        _row("parent", root_id="6186", state="parked_waiting", idle_age_ms=60_000),
        _row("child", root_id="6186", tool_call_count=3, last_tool_name="mcp"),
    ]
    assert classify_sdk_live(live) == "nested"
    assert row_role(live[0], live, "nested") == "parent"
    assert row_role(live[1], live, "nested") == "child"
    ordered = sort_sdk_live(live, "nested")
    assert ordered[0].dispatch_id == "parent"
    assert "nested" in (posture_legend("nested") or "")


def test_nested_when_review_child_linked() -> None:
    live = [
        _row("parent-exec", root_id="6164", tool_call_count=10),
        _row(
            "child-exec",
            root_id="6164",
            review_child=True,
            parent_execution_id="parent-exec",
            model="gpt-5.5",
        ),
    ]
    assert classify_sdk_live(live) == "nested"
    assert row_role(live[0], live, "nested") == "parent"
    assert row_role(live[1], live, "nested") == "child"


def test_never_active_rows_stay_unstarted_with_or_without_composer_workers() -> None:
    """Three never-active rows stay UNSTARTED; header counts LIVE only."""
    never = [
        _row(
            "idle-a",
            elapsed_ms=90_000,
            idle_age_ms=90_000,
            emitters_seen=("pipeline",),
            caller_via="mcp",
        ),
        _row(
            "idle-b",
            elapsed_ms=60_000,
            idle_age_ms=60_000,
            emitters_seen=("pipeline",),
            caller_via="mcp",
        ),
        _row(
            "idle-c",
            elapsed_ms=30_000,
            idle_age_ms=30_000,
            emitters_seen=("pipeline",),
            caller_via="mcp",
        ),
    ]
    workers = [
        _row(
            "composer-1",
            root_id="12286",
            thread_id="21698",
            model="composer-2.5",
            tool_call_count=4,
            elapsed_ms=20_000,
            idle_age_ms=1_000,
        ),
        _row(
            "composer-2",
            root_id="12286",
            thread_id="21707",
            model="composer-2.5",
            tool_call_count=2,
            elapsed_ms=15_000,
            idle_age_ms=500,
        ),
    ]
    assert [classify_sdk_activity(row) for row in never] == ["UNSTARTED"] * 3
    live, unstarted = split_open_sdk(never + workers)
    assert {row.dispatch_id for row in live} == {"composer-1", "composer-2"}
    assert [row.dispatch_id for row in unstarted] == ["idle-a", "idle-b", "idle-c"]
    assert sdk_flight_label(live, unstarted) == "sdk 2 +3 unstarted"
    assert sdk_flight_fragment(never + workers) == ("sdk 2 +3 unstarted", 20_000)
    assert len(live) == 2
    assert all(row_role(row, never + workers, "id_split") != "ghost" for row in never)
    assert "ghost" not in ROW_TAG
    assert posture_legend("id_split") is None
    assert "via=mcp" in sdk_unstarted_line(never[0], width=80)
    assert "el=1m30s" in sdk_unstarted_line(never[0], width=80)

    live_only, unstarted_only = split_open_sdk(never)
    assert live_only == []
    assert [classify_sdk_activity(row) for row in unstarted_only] == ["UNSTARTED"] * 3
    assert [row.dispatch_id for row in unstarted_only] == ["idle-a", "idle-b", "idle-c"]
    assert sdk_flight_label(live_only, unstarted_only) == "sdk 0 +3 unstarted"
    assert sdk_flight_fragment(never) == ("sdk 0 +3 unstarted", None)


def test_parallel_distinct_roots() -> None:
    live = [
        _row("a", root_id="6186", tool_call_count=1),
        _row("b", root_id="6190", tool_call_count=2),
    ]
    assert classify_sdk_live(live) == "parallel"
    assert row_role(live[0], live, "parallel") == "para"
    assert "parallel" in (posture_legend("parallel") or "")
