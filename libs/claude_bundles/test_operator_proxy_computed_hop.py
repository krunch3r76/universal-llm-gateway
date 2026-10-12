"""Offline tests for computed hop live-leg projection."""

from __future__ import annotations

from typing import Any

import pytest

from claude_bundles.operator_proxy_live_leg import (
    GOVERNING_RULE,
    fetch_turns_to_closure,
    fold_lane,
    house_lanes,
)
from claude_bundles.operator_proxy_mission import ensure_operator_proxy_mission_prompt

pytestmark = pytest.mark.offline

_HR_SENTINEL = "HR-SENTINEL-KEEP"

_ROSTER = """
| N | Display name | Lane | Mailbox | State |
|---|---|---|---|---|
| 1 | Maestro Sidekick 1 | 15609 `maestro-sidekick` | `grok-bot` | live |
| 2 | Maestro Sidekick 2 | 15722 `maestro-sidekick-2` | `grok-bot-msk2` | live |
| 3 | Maestro Sidekick 3 | 15741 `maestro-sidekick-3` | `grok-bot-msk3` | live |
| 4 | Maestro Sidekick 4 | 15785 `maestro-sidekick-4` | `grok-bot-msk4` | live |
| 5 | Maestro Sidekick 5 | 15838 `maestro-sidekick-5` | `grok-bot-msk5` | live |
| N | Display name | Lane | Mailbox | State |
| 1 | Maestro Liaison 1 | 15760 `maestro-liaison-1` | `grok-bot-ml1` | live |
| 2 | Maestro Liaison 2 | 15784 `maestro-liaison-2` | `grok-bot-ml2` | live |
| 3 | Maestro Liaison 3 | 15857 `maestro-liaison-3` | `grok-bot-ml3` | live |
"""


def _standing_handoff_fixture() -> str:
    lines = [
        f"## HOUSE RULES\n{_HR_SENTINEL}\n",
    ]
    for n in range(184, 118, -1):
        heading = f"## CURRENT LEG {n} — HOP HANDOFF" if n >= 170 else f"## LEG {n} — CURRENT"
        lines.append(f"{heading}\nSENTINEL-LEG-{n}\n")
        if n == 181:
            lines.append("open sidecar-only ML1 todo:ml1\n")
    return "\n".join(lines)


def _turn(turn_number: int, subject: str) -> dict[str, Any]:
    return {"turn_number": turn_number, "subject": subject, "from": "web-anthropic"}


def _fake_fetch() -> dict[str, list[dict[str, Any]]]:
    return {
        "15722": [
            _turn(50, "REQUEST|investigate|decision:house-kinds-unify"),
            _turn(61, "REQUEST|implement|decision:maestro-hop-model-fable"),
            _turn(73, "REQUEST|freeform|recover-maestro-houses"),
            _turn(78, "CANCEL|recover-maestro-houses"),
            _turn(80, "REQUEST|investigate|todo:maestro-computed-hop"),
            _turn(81, "REQUEST|freeform|recover-maestro-houses"),
            _turn(82, "CANCEL|#73"),
            _turn(84, "PLAN|todo:maestro-seat-pipelines"),
            _turn(85, "DONE|implement|decision:maestro-hop-model-fable"),
            _turn(86, "PICKUP|todo:maestro-computed-hop"),
        ],
        "15760": [
            _turn(3, "REQUEST|freeform|todo:ml1"),
        ],
        "15857": [],
    }


def test_house_lanes_includes_gc1_and_operator() -> None:
    lanes = house_lanes(_ROSTER)
    assert lanes[:8] == [
        "15609",
        "15722",
        "15741",
        "15785",
        "15838",
        "15760",
        "15784",
        "15857",
    ]
    assert "15851" in lanes
    assert "12286" in lanes
    assert len(lanes) == 10


def test_computed_hop_rendering() -> None:
    calls: list[tuple[str, dict[str, Any] | None]] = []
    data = _fake_fetch()

    def fetch_turns(lane: str) -> list[dict[str, Any]]:
        calls.append((lane, None))
        if lane == "15857":
            raise RuntimeError("simulated bus down")
        return data.get(lane, [])

    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=_standing_handoff_fixture(),
        roster_text=_ROSTER,
        fetch_turns=fetch_turns,
    )

    assert "SENTINEL-LEG-" not in out
    assert "LEG 170" not in out
    assert _HR_SENTINEL in out
    assert out.index(_HR_SENTINEL) < out.index("## Live leg")

    assert "todo:maestro-computed-hop" in out
    assert "decision:house-kinds-unify" in out
    assert "todo:maestro-seat-pipelines" in out
    assert "todo:ml1" in out
    assert "decision:maestro-hop-model-fable" not in out
    assert "recover-maestro-houses" not in out

    for verb in ("REQUEST", "PICKUP", "PLAN"):
        if verb in GOVERNING_RULE:
            assert GOVERNING_RULE[verb].split(":")[0] in out

    assert "live leg unavailable for 15857" in out
    assert "consolidated section not found" not in out

    assert len(calls) == 10

    assert out.rindex("## Hard refusals") > out.index("## Hop request (data)")
    refusals = out.split("## Hard refusals", 1)[1]
    assert refusals.count("\n- ") >= 11
    assert "desired_model=cdp/opus-5.5-extra" in refusals

    twice = ensure_operator_proxy_mission_prompt(out)
    assert twice.rstrip("\n") == out.rstrip("\n")


def test_amendment_3_landed_does_not_mint_open_item() -> None:
    """LANDED is not a close verb; PICKUP stays open until DONE|FAILED."""
    turns = [
        _turn(1, "REQUEST|implement|todo:restart-me"),
        _turn(2, "LANDED|implement|todo:restart-me"),
        _turn(3, "PICKUP|implement|todo:restart-me"),
    ]
    items, _ = fold_lane("12286", turns)
    verbs = {item.verb for item in items if item.key == "todo:restart-me"}
    assert verbs == {"REQUEST", "PICKUP"}
    assert "LANDED" not in GOVERNING_RULE


def test_cancel_matches_work_key_before_turn_ref() -> None:
    turns = [
        _turn(10, "REQUEST|freeform|todo:alpha"),
        _turn(11, "CANCEL|todo:alpha"),
    ]
    items, _ = fold_lane("15722", turns)
    assert items == []


def test_cancel_turn_ref_fallback() -> None:
    turns = [
        _turn(81, "REQUEST|freeform|recover-maestro-houses"),
        _turn(82, "CANCEL|#81"),
    ]
    items, _ = fold_lane("15722", turns)
    assert items == []


def test_amendment_6_cancel_rejects_multiple_turn_refs() -> None:
    turns = [
        _turn(81, "REQUEST|freeform|todo:recover-maestro-houses"),
        _turn(82, "CANCEL|#81|#82"),
    ]
    items, _ = fold_lane("15722", turns)
    assert len(items) == 1
    assert items[0].key == "todo:recover-maestro-houses"


def test_amendment_6_cancel_turn_ref_must_match_request_turn() -> None:
    turns = [
        _turn(81, "REQUEST|freeform|todo:recover-maestro-houses"),
        _turn(82, "CANCEL|#73"),
    ]
    items, _ = fold_lane("15722", turns)
    assert len(items) == 1
    assert items[0].turn == 81


def test_amendment_4_act3_house_rules_inline_limit_fifty_only_on_degraded() -> None:
    out = ensure_operator_proxy_mission_prompt(
        "thread_id: 12286\n",
        standing_handoff_text=_standing_handoff_fixture(),
        roster_text=_ROSTER,
        fetch_turns=lambda _lane: [],
    )
    acts = out.split("## Your first acts, in order", 1)[1].split("## How this seat works", 1)[0]
    line3 = [line for line in acts.splitlines() if line.lstrip().startswith("3.")][0]
    assert "HOUSE RULES" in line3
    assert "limit 50" in line3
    assert "unavailable" in line3
    assert out.index(_HR_SENTINEL) < out.index("## Live leg")
    assert "SENTINEL-LEG-" not in out


def test_amendment_2_paging_reaches_open_beyond_first_page() -> None:
    """Open REQUEST #50 must appear when it lies outside the first 200-turn page."""
    open_turn = _turn(50, "REQUEST|investigate|decision:house-kinds-unify")
    filler = [_turn(i, f"STATUS|n{i}") for i in range(151, 251)]

    def page_fetch(
        _lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del last
        if after_turn == 0:
            return filler, True, 150
        if after_turn == 150:
            return [open_turn], False, None
        return [], False, None

    merged, paging_truncated = fetch_turns_to_closure("15722", page_fetch=page_fetch)
    items, truncated = fold_lane("15722", merged)
    assert paging_truncated is False
    assert truncated is False
    keys = {item.key for item in items}
    assert "decision:house-kinds-unify" in keys


def test_fold_lane_15722_real_subject_shapes() -> None:
    """Specimens from lane 15722 dry-run: PICKUP/DONE/PLAN/IMPLEMENT folding."""
    turns = [
        _turn(60, "PICKUP|implement|runbook:maestro-sidekick"),
        _turn(64, "PICKUP|implement|decision:maestro-hop-model-fable"),
        _turn(65, "DONE|implement|decision:maestro-hop-model-fable"),
        _turn(102, "PICKUP|decision:forward-only-git"),
        _turn(103, "PLAN|decision:forward-only-git"),
        _turn(105, "IMPLEMENT|decision:forward-only-git (ACK received)"),
        _turn(107, "DONE|decision:forward-only-git (ACK received)"),
    ]
    items, _truncated = fold_lane("15722", turns)
    keys = {(item.verb, item.key) for item in items}
    assert ("PICKUP", "runbook:maestro-sidekick") in keys
    assert ("PICKUP", "decision:maestro-hop-model-fable") not in keys
    assert ("PICKUP", "decision:forward-only-git") not in keys
    assert ("PLAN", "decision:forward-only-git") not in keys


def test_fetch_turns_stops_when_next_after_does_not_advance() -> None:
    """Stuck bus cursor must return partial turns with truncated=True (nit 1)."""
    calls = 0

    def page_fetch(
        _lane: str,
        *,
        after_turn: int = 0,
        last: int = 200,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        del last
        nonlocal calls
        calls += 1
        if calls == 1:
            return [_turn(5, "REQUEST|freeform|recover-alpha")], True, 5
        return [_turn(6, "STATUS|filler")], True, 5

    turns, paging_truncated = fetch_turns_to_closure("15722", page_fetch=page_fetch)
    assert paging_truncated is True
    assert calls == 2
    assert len(turns) == 2


def test_cancel_turn_ref_leaves_scheme_keyed_request_open() -> None:
    """CANCEL|#N must not close todo:-keyed items; CANCEL|todo:x must (nit 2)."""
    turns = [
        _turn(20, "REQUEST|implement|todo:alpha"),
        _turn(21, "CANCEL|#20"),
    ]
    items, _ = fold_lane("15722", turns)
    assert len(items) == 1
    assert items[0].key == "todo:alpha"

    turns_closed = [
        _turn(20, "REQUEST|implement|todo:alpha"),
        _turn(21, "CANCEL|todo:alpha"),
    ]
    items_closed, _ = fold_lane("15722", turns_closed)
    assert items_closed == []


def test_done_closes_pickup_even_when_request_already_closed() -> None:
    turns = [
        _turn(1, "REQUEST|implement|todo:x"),
        _turn(2, "PICKUP|implement|todo:x"),
        _turn(3, "DONE|implement|todo:x"),
    ]
    items, _ = fold_lane("15722", turns)
    assert items == []
