"""Relation tag paint for SDK live lines."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core.board_lines import sdk_live_line
from scripts.model_manager.ui.dispatch_monitor.core.board_relations import sdk_relation_tags
from scripts.model_manager.ui.dispatch_monitor.core.dtos import RelationEdge, SdkDispatchRow


def _row(
    dispatch_id: str,
    *,
    root_id: str | None = "6164",
    thread_id: str | None = "9800",
    nest_under: str | None = None,
    resume_of: str | None = None,
) -> SdkDispatchRow:
    return SdkDispatchRow(
        dispatch_id=dispatch_id,
        root_id=root_id,
        thread_id=thread_id,
        state="running",
        nest_under=nest_under,
        resume_of=resume_of,
    )


def test_sdk_relation_tags_nest_under() -> None:
    relations = (
        RelationEdge(
            kind="nest_under",
            from_id="parent-dispatch",
            to_id="child-dispatch",
            evidence_signal="frontier.sdk.worker.dispatched",
        ),
    )
    tags = sdk_relation_tags(
        _row("child-dispatch", root_id="6164", thread_id="6164"),
        relations,
    )
    assert tags == ["child-of:nest=parent-dispat…"]


def test_sdk_relation_tags_root_dispatch_and_thread_lane() -> None:
    relations = (
        RelationEdge(
            kind="root_dispatch",
            from_id="6164",
            to_id="abc123",
            evidence_signal="correlation.root_dispatch",
        ),
    )
    row = _row("abc123", root_id="6164", thread_id="9800")
    tags = sdk_relation_tags(row, relations)
    assert tags == ["child-of:t=6164"]


def test_sdk_relation_tags_resume_of_outbound() -> None:
    relations = (
        RelationEdge(
            kind="resume_of",
            from_id="new-dispatch",
            to_id="prior-dispatch",
            evidence_signal="frontier.sdk.worker.resumed",
        ),
    )
    tags = sdk_relation_tags(_row("new-dispatch", resume_of="prior-dispatch"), relations)
    assert tags == ["resume-of=prior-dispatch", "child-of:t=6164"]


def test_sdk_live_line_paints_relation_tags() -> None:
    child = _row(
        "child-dispatch",
        root_id="6164",
        thread_id="9800",
        nest_under="parent-dispatch",
    )
    parent = _row("parent-dispatch", root_id="6164", thread_id="6164")
    relations = (
        RelationEdge(
            kind="nest_under",
            from_id="parent-dispatch",
            to_id="child-dispatch",
            evidence_signal="frontier.sdk.worker.dispatched",
        ),
        RelationEdge(
            kind="root_dispatch",
            from_id="6164",
            to_id="child-dispatch",
            evidence_signal="correlation.root_dispatch",
        ),
    )
    line = sdk_live_line(
        child,
        live=[parent, child],
        posture="nested",
        width=200,
        relations=relations,
    )
    assert "child-of:nest=parent-dispat" in line
    assert "child-of:t=6164" in line
