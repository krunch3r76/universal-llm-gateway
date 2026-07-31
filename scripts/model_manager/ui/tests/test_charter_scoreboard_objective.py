"""Scoreboard objective parse + monitor graft helpers."""

from __future__ import annotations

from pathlib import Path

from scripts.model_manager.ui.charter_scoreboard_objective import (
    objective_meta_event,
    parse_original_objective,
    path_from_scoreboard_uri,
    read_objective_from_uri,
    read_objective_for_root,
)
from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.board_lines import root_line_live
from scripts.model_manager.ui.dispatch_monitor.core.dtos import CharterRootRow
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event


def test_parse_original_objective_preserves_heading_suffix() -> None:
    md = """# Scoreboard

## Original objective (preserved)

Standing fleet conveyor: inventory eligible friction todos.

## Gated deliverables
"""
    assert parse_original_objective(md) == (
        "Standing fleet conveyor: inventory eligible friction todos."
    )


def test_parse_original_objective_missing_section() -> None:
    assert parse_original_objective("# Scoreboard\n\n## Next pickup\n") is None


def test_path_from_scoreboard_uri_rejects_traversal() -> None:
    assert path_from_scoreboard_uri("cortex://notes/../etc/passwd") is None
    assert path_from_scoreboard_uri("/mnt/torus/mcp-data/files/x.md") is None
    path = path_from_scoreboard_uri(
        "cortex://notes/system/threads/charter-friction-enroll-on-arrival-scoreboard.md"
    )
    assert path is not None
    assert path.name == "charter-friction-enroll-on-arrival-scoreboard.md"


def test_read_objective_prefers_named_scoreboard_uri(
    tmp_path: Path, monkeypatch
) -> None:
    files = tmp_path / "files"
    named = files / "notes/system/threads/named-scoreboard.md"
    named.parent.mkdir(parents=True)
    named.write_text(
        "# S\n\n## Original objective\n\nNamed URI objective wins.\n\n## Next\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(files))
    obj = read_objective_for_root(
        "6186",
        scoreboard_uri="cortex://notes/system/threads/named-scoreboard.md",
    )
    assert obj == "Named URI objective wins."
    assert (
        read_objective_from_uri(
            "cortex://notes/system/threads/named-scoreboard.md"
        )
        == "Named URI objective wins."
    )


def test_admitted_payload_and_meta_signal_set_objective() -> None:
    model = Model()
    objective = "Rewrite charter-runner conveyor from scratch."
    model.apply(
        Event(
            signals.CHARTER_ADMITTED,
            1_000,
            {"root": "6171", "worker_thread": "6172", "objective": objective},
        )
    )
    root = model.derive(2_000).roots[0]
    assert root.objective == objective

    model.apply(
        Event(
            signals.MONITOR_META_CHARTER_OBJECTIVE,
            3_000,
            {"root": "6171", "objective": "Updated objective text."},
        )
    )
    root = model.derive(4_000).roots[0]
    assert root.objective == "Updated objective text."


def test_meta_signal_grafts_pickup_gid_without_objective() -> None:
    model = Model()
    model.apply(
        Event(
            signals.MONITOR_META_CHARTER_OBJECTIVE,
            1_000,
            objective_meta_event("6185", pickup_gid="G9"),
        )
    )
    root = model.derive(2_000).roots[0]
    assert root.pickup_gid == "G9"
    assert root.objective is None


def test_root_line_live_renders_objective_on_tick_row() -> None:
    row = CharterRootRow(
        root_id="6171",
        state="in_flight",
        worker_thread="6172",
        skip_streak=0,
        objective="Standing fleet conveyor: enroll friction follow-ons.",
    )
    line = root_line_live(row, sdk_n=1, cdp_n=0, width=120)
    assert "obj: Standing fleet conveyor" in line
    assert "skip_reason" not in line


def test_root_line_live_prefers_arc_g_step_then_pickup_gid() -> None:
    with_pickup = CharterRootRow(
        root_id="6185",
        state="skipped",
        pickup_gid="G9",
        skip_reason="empty_hopper",
    )
    assert "g=G9" in root_line_live(with_pickup, sdk_n=0, cdp_n=0)
    with_admit = CharterRootRow(
        root_id="6185",
        state="in_flight",
        arc_g_step="G1",
        pickup_gid="G9",
    )
    assert "g=G1" in root_line_live(with_admit, sdk_n=0, cdp_n=0)
    assert "g=G9" not in root_line_live(with_admit, sdk_n=0, cdp_n=0)


def test_root_line_live_uses_bus_summary_when_objective_absent() -> None:
    row = CharterRootRow(
        root_id="6191",
        state="skipped",
        pickup_gid="G9",
        bus_slug="charter-friction-conveyor",
        bus_summary="Standing fleet conveyor for charter friction follow-ons",
        skip_reason="dormant",
    )
    line = root_line_live(row, sdk_n=0, cdp_n=0, width=140)
    assert "bus: Standing fleet conveyor" in line
    assert "obj:" not in line
    assert "dormant" not in line  # purpose wins over skip reason


def test_meta_signal_grafts_bus_identity() -> None:
    model = Model()
    model.apply(
        Event(
            signals.MONITOR_META_CHARTER_OBJECTIVE,
            1_000,
            objective_meta_event(
                "6191",
                pickup_gid="G9",
                bus_slug="charter-friction-conveyor",
                bus_summary="Standing fleet conveyor for charter friction follow-ons",
            ),
        )
    )
    root = model.derive(2_000).roots[0]
    assert root.bus_slug == "charter-friction-conveyor"
    assert root.bus_summary.startswith("Standing fleet conveyor")
    assert root.pickup_gid == "G9"
