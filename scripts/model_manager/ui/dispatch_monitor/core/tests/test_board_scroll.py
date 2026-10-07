"""Viewport scroll replaces the per-section row ceiling."""

from scripts.model_manager.ui.dispatch_monitor.core.curses_board import (
    clamp_scroll,
    scroll_to_show_logical_line,
    section_row_caps,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.tail_selection import (
    TailTarget,
    advance_section,
    targets_in_section,
)


def test_section_caps_keep_every_row() -> None:
    caps = section_row_caps(sdk_n=40, cdp_n=12, roots_n=9, aside_n=4, attention_n=15)
    assert caps == {
        "attention": 15,
        "cdp": 12,
        "sdk": 40,
        "roots": 9,
        "aside": 4,
    }


def test_clamp_scroll_stops_at_the_last_screen() -> None:
    # header 3 + 20 content lines, screen 10 → 13 content rows hidden, max scroll 13
    assert clamp_scroll(0, content_end=23, screen_h=10) == 0
    assert clamp_scroll(100, content_end=23, screen_h=10) == 13
    assert clamp_scroll(-4, content_end=23, screen_h=10) == 0


def test_clamp_scroll_when_content_fits() -> None:
    assert clamp_scroll(5, content_end=8, screen_h=24) == 0


def test_scroll_to_show_logical_line_below_fold() -> None:
    # logical line 40 on screen_h=20 → scroll so row appears
    assert (
        scroll_to_show_logical_line(
            0, 40, content_end=50, screen_h=20
        )
        == 21
    )


def test_advance_section_cycles() -> None:
    assert advance_section("attention", 1) == "tick"
    assert advance_section("tick", -1) == "attention"


def test_targets_in_section_filters_sdk_cdp() -> None:
    targets = [
        TailTarget("cursor-sdk", "a", "a"),
        TailTarget("cdp", "b", "b"),
    ]
    assert len(targets_in_section(targets, "sdk")) == 1
    assert len(targets_in_section(targets, "tick")) == 0
