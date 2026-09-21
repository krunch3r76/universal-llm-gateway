"""Phase-2 occupancy thread resolution."""

from __future__ import annotations

from bus_watch.loop_tape import loop_tape_thread


def test_loop_tape_defaults_to_root() -> None:
    assert loop_tape_thread("10479") == "10479"
    assert loop_tape_thread("10479", {}) == "10479"
    assert loop_tape_thread("10479", {"loop_thread": ""}) == "10479"
    assert loop_tape_thread("10479", {"loop_thread": None}) == "10479"


def test_loop_tape_first_nonempty_wins() -> None:
    assert loop_tape_thread("10479", {"loop_thread": 11876}) == "11876"
    assert (
        loop_tape_thread(
            "10479",
            {"gear": "3-wake-on-attention"},
            {"loop_thread": "11876"},
        )
        == "11876"
    )
