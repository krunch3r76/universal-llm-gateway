"""SDK multi-row posture classifier — View labels for nested / id_split / parallel."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core.dtos import SdkDispatchRow
from scripts.model_manager.ui.dispatch_monitor.core.sdk_posture import (
    classify_sdk_live,
    posture_legend,
    row_role,
    sort_sdk_live,
)


def _row(
    dispatch_id: str,
    *,
    root_id: str | None = None,
    state: str = "running",
    tool_call_count: int | None = None,
    last_tool_name: str | None = None,
    idle_age_ms: int | None = 0,
) -> SdkDispatchRow:
    return SdkDispatchRow(
        dispatch_id=dispatch_id,
        root_id=root_id,
        state=state,
        tool_call_count=tool_call_count,
        last_tool_name=last_tool_name,
        idle_age_ms=idle_age_ms,
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


def test_id_split_same_root_without_park() -> None:
    live = [
        _row("fc8437a1", root_id="6186", idle_age_ms=120_000),
        _row(
            "71f4e2689440",
            root_id="6186",
            tool_call_count=23,
            last_tool_name="mcp",
            idle_age_ms=0,
        ),
    ]
    assert classify_sdk_live(live) == "id_split"
    assert row_role(live[0], live, "id_split") == "ghost"
    assert row_role(live[1], live, "id_split") == "live"
    ordered = sort_sdk_live(live, "id_split")
    assert ordered[0].dispatch_id == "71f4e2689440"
    assert "id_split" in (posture_legend("id_split") or "")


def test_parallel_distinct_roots() -> None:
    live = [
        _row("a", root_id="6186", tool_call_count=1),
        _row("b", root_id="6190", tool_call_count=2),
    ]
    assert classify_sdk_live(live) == "parallel"
    assert row_role(live[0], live, "parallel") == "para"
    assert "parallel" in (posture_legend("parallel") or "")
