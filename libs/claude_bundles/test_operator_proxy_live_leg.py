"""Offline tests for operator-proxy live-leg fold state and paging."""

from __future__ import annotations

from typing import Any

import pytest

from claude_bundles.operator_proxy_live_leg import (
    LaneFoldState,
    LiveLeg,
    OpenItem,
    fetch_turns_to_closure,
    project_live_leg,
    render_live_leg,
)

pytestmark = pytest.mark.offline

_TRUNCATION_LINE = (
    "(truncated: bus paging did not reach thread start; "
    "an open item may lie beyond the fetched window.)"
)


def test_ac5_open_item_set_survives_watermark() -> None:
    key = "15851:100:todo:old"
    state = {"15851": LaneFoldState(watermark=3000, open_keys=[key])}

    def _load() -> dict[str, LaneFoldState]:
        return dict(state)

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, last, compact, mark_read
        assert after_turn == 3000
        return [], False, None

    leg = project_live_leg(
        ["15851"],
        lambda _l: [],
        page_fetch=_page,
        fold_state_load=_load,
        fold_state_save=lambda _s: None,
    )
    text = render_live_leg(leg)
    assert "todo:old" in text or "15851" in text
    assert any(i.turn == 100 or "old" in i.key for i in leg.items)


def test_ac6_sixty_page_fetch_shows_truncation_line() -> None:
    calls = {"n": 0}

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, compact, mark_read
        calls["n"] += 1
        turn = after_turn + 1
        return (
            [{"turn_number": turn, "subject": "NOTE|x"}],
            True,
            turn,
        )

    _turns, truncated = fetch_turns_to_closure("1", page_fetch=_page, max_pages=60)
    assert truncated
    leg = LiveLeg(truncated=True)
    assert _TRUNCATION_LINE in render_live_leg(leg)


def test_ac7_warm_lane_at_most_two_page_calls() -> None:
    calls: list[int] = []

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, last, compact, mark_read
        calls.append(after_turn)
        n = after_turn + 1
        return ([{"turn_number": n, "subject": "NOTE|x"}], True, n)

    state = {"12286": LaneFoldState(watermark=50, open_keys=[])}
    project_live_leg(
        ["12286"],
        lambda _l: [],
        page_fetch=_page,
        fold_state_load=lambda: dict(state),
        fold_state_save=lambda _s: None,
    )
    assert len(calls) <= 2


def test_ac8_fold_state_unreadable_cold_start() -> None:
    def _load() -> dict[str, LaneFoldState] | None:
        return None

    def _page(
        lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
        compact: bool = True,
        mark_read: bool = False,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del lane, last, compact, mark_read
        assert after_turn == 0
        return [], False, None

    leg = project_live_leg(
        ["12286"],
        lambda _l: [],
        page_fetch=_page,
        fold_state_load=_load,
    )
    assert isinstance(leg, LiveLeg)


def test_render_declared_in_flight_header() -> None:
    assert "declared_in_flight: 3" in render_live_leg(LiveLeg(), declared_in_flight=3)
