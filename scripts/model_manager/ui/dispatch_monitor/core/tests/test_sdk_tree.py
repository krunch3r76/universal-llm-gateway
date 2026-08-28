"""G5.2 — depth-1 nest tree paint + fast/cond alignment."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.board_lines import sdk_live_line
from scripts.model_manager.ui.dispatch_monitor.core.dtos import SdkDispatchRow
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.core.sdk_posture import (
    classify_sdk_live,
    row_role,
)
from scripts.model_manager.ui.dispatch_monitor.core.sdk_tree import (
    nest_pointer,
    nest_under_edges,
    sort_sdk_tree,
    tree_glyph,
)


def _row(
    dispatch_id: str,
    *,
    root_id: str = "6164",
    thread_id: str = "9675",
    state: str = "running",
    model: str = "composer-2.5",
    nest_under: str | None = None,
    topic: str | None = None,
    fast: bool | None = None,
    packet_kind: str | None = None,
    elapsed_ms: int = 372_000,
    idle_age_ms: int = 8_000,
    emitters_seen: tuple[str, ...] = ("worker",),
) -> SdkDispatchRow:
    return SdkDispatchRow(
        dispatch_id=dispatch_id,
        state=state,
        root_id=root_id,
        thread_id=thread_id,
        model=model,
        nest_under=nest_under,
        topic=topic,
        fast=fast,
        packet_kind=packet_kind,
        elapsed_ms=elapsed_ms,
        idle_age_ms=idle_age_ms,
        emitters_seen=emitters_seen,
    )


def test_family_mislabel_orphan_no_child_glyph_parallel_bar() -> None:
    """Unrelated live peer + nested orphan → parallel bar, no └─ on orphan."""
    orphan = _row(
        "orphan-child",
        nest_under="dead-parent",
        thread_id="9676",
        model="grok-4.6",
    )
    peer = _row("unrelated-peer", root_id="6190", thread_id="9680")
    live = [orphan, peer]
    assert classify_sdk_live(live) == "parallel"
    assert row_role(orphan, live, "parallel") == "para"
    edges = nest_under_edges(live)
    assert orphan.dispatch_id not in edges
    assert tree_glyph(orphan, edges=edges, cyclic=set()) == "  "
    line = sdk_live_line(orphan, live=live, posture="parallel", width=200)
    assert "└─" not in line
    assert "↳ dead-parent" in line
    assert "nest=" not in line


def test_depth_one_tree_and_paint_target_shape() -> None:
    parent = _row(
        "7a7ffee9c2de-645fb167",
        fast=True,
        packet_kind="conductor",
        topic="SDK nest tree + fast",
        elapsed_ms=372_000,
        idle_age_ms=8_000,
    )
    child = _row(
        "5c6ca43b8cbf-9d4c3c7b",
        nest_under="7a7ffee9c2de-645fb167",
        thread_id="9676",
        model="grok-4.6",
        fast=False,
        elapsed_ms=244_000,
        idle_age_ms=0,
        emitters_seen=("git_integration_worker",),
    )
    live = [parent, child]
    assert classify_sdk_live(live) == "nested"
    ordered = sort_sdk_tree(live)
    assert ordered[0].dispatch_id == parent.dispatch_id
    assert ordered[1].dispatch_id == child.dispatch_id
    parent_line = sdk_live_line(parent, live=live, posture="nested", width=200)
    child_line = sdk_live_line(child, live=live, posture="nested", width=200)
    assert "7a7ffee9c2de-645fb167" in parent_line
    assert parent_line.index("7a7ffee9") < parent_line.index("running")
    assert " fast" in parent_line
    assert " cond" in parent_line
    assert "SDK nest tree + fast" in parent_line
    assert child_line.startswith("  └─ 5c6ca43b8cbf-9d4c3c7b")
    assert " fast=no" in child_line
    assert " cond" not in child_line
    assert "[giw]" in child_line
    assert "nest=" not in parent_line
    assert "nest=" not in child_line


def test_cycle_and_self_ref_degrade() -> None:
    a = _row("child-a", nest_under="child-b", thread_id="1")
    b = _row("child-b", nest_under="child-a", thread_id="2", model="grok-4.6")
    live = [a, b]
    edges = nest_under_edges(live)
    assert a.dispatch_id in edges and b.dispatch_id in edges
    cyclic = {a.dispatch_id, b.dispatch_id}
    assert tree_glyph(a, edges=edges, cyclic=cyclic) == "  "
    assert "↳ child-b" in nest_pointer(
        a,
        live_ids={r.dispatch_id for r in live},
        edges=edges,
        cyclic=cyclic,
    )
    self_ref = _row("solo-self", nest_under="solo-self")
    solo_edges = nest_under_edges([self_ref])
    assert solo_edges == {}
    assert (
        nest_pointer(
            self_ref,
            live_ids={"solo-self"},
            edges=solo_edges,
            cyclic=set(),
        )
        == "↳ solo-self"
    )


def test_fast_cond_omit_rules() -> None:
    fast_true = _row("fast-y", fast=True)
    fast_false = _row("fast-n", fast=False)
    fast_unknown = _row("fast-u", model="composer-2.5-fast")
    conductor = _row("cond-y", packet_kind="conductor")
    implement = _row("cond-n", packet_kind="implement")
    assert " fast" in sdk_live_line(fast_true, width=200)
    assert " fast=no" in sdk_live_line(fast_false, width=200)
    unknown_line = sdk_live_line(fast_unknown, width=200)
    assert " fast=no" not in unknown_line
    assert " fast cond" not in unknown_line
    assert " fast " not in unknown_line.split("composer-2.5-fast")[0]
    assert " cond" in sdk_live_line(conductor, width=200)
    assert " cond " not in sdk_live_line(implement, width=200)


def test_fold_queued_dispatched_stamps_before_terminal() -> None:
    model = Model()
    dispatch_id = "disp-g52-fold"
    model.apply(
        Event(
            signals.SDK_WORKER_QUEUED,
            1_000,
            {
                "dispatch_id": dispatch_id,
                "execution_id": dispatch_id,
                "model": "composer-2.5",
                "packet_kind": "conductor",
                "model_knobs_requested": {"fast": "true"},
                "topic": "Queued mission text",
            },
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            2_000,
            {
                "dispatch_id": dispatch_id,
                "execution_id": dispatch_id,
                "model_knobs_requested": {"fast": "false"},
                "topic": "Dispatched override",
            },
        )
    )
    row = next(r for r in model.derive(3_000).sdk if r.dispatch_id == dispatch_id)
    assert row.packet_kind == "conductor"
    assert row.fast is True
    assert row.topic == "Queued mission text"
    assert row.nest_under is None
    model.apply(
        Event(
            signals.SDK_PIPELINE_COMPLETED,
            4_000,
            {
                "dispatch_id": dispatch_id,
                "execution_id": dispatch_id,
                "status": "completed",
            },
        )
    )
    done = next(r for r in model.derive(5_000).sdk if r.dispatch_id == dispatch_id)
    assert done.packet_kind == "conductor"
    assert done.fast is True
    assert done.topic == "Queued mission text"


def test_nest_under_first_writer_wins() -> None:
    model = Model()
    dispatch_id = "disp-nest-fw"
    model.apply(
        Event(
            signals.SDK_WORKER_QUEUED,
            1_000,
            {
                "dispatch_id": dispatch_id,
                "execution_id": dispatch_id,
                "nest_under": "parent-first",
            },
        )
    )
    model.apply(
        Event(
            signals.SDK_WORKER_DISPATCHED,
            2_000,
            {
                "dispatch_id": dispatch_id,
                "execution_id": dispatch_id,
                "nest_under": "parent-second",
            },
        )
    )
    row = next(r for r in model.derive(3_000).sdk if r.dispatch_id == dispatch_id)
    assert row.nest_under == "parent-first"
