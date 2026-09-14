"""The navigator's throttle state must survive the ticker's next write.

``liaison-induce.py --fire`` records ``last_induction_at``; the gear-3 ticker
loop holds its counters in memory and rewrites the whole state file each tick,
absorbing only ``OPERATOR_KEYS`` from disk first. While the induction keys were
absent from that list the loop dropped them within one poll (~160s).

That is not a cosmetic loss. ``navigator_grace_elapsed`` is computed from
``last_induction_at``; with it gone the grace always reads elapsed, single-flight
becomes the only brake, and a periodic navigator clock re-fires a cdp wake as
soon as the previous one completes instead of honouring the 900s grace.
"""

from __future__ import annotations

import time

import pytest

from bus_watch.navigator_wake import evaluate_navigator_wake
from bus_watch.tick_state import (
    OPERATOR_KEYS,
    absorb_operator_edits,
    load_state,
    update_state,
)

_NAV_KEYS = (
    "last_induction_at",
    "last_induction_fingerprint",
    "navigator_commissions_by_night",
    "navigator_commissions_tonight",
)


@pytest.mark.offline
@pytest.mark.parametrize("key", _NAV_KEYS)
def test_navigator_keys_are_operator_owned(key: str) -> None:
    assert key in OPERATOR_KEYS, (
        f"{key} is written by the liaison-induce one-shot and never derived by "
        "the loop, so the loop must absorb it or it is clobbered every tick"
    )


@pytest.mark.offline
def test_ticker_write_preserves_a_fresh_induction(tmp_path) -> None:  # noqa: ANN001
    """End-to-end shape of the bug: fire, then let the loop save over it."""
    path = tmp_path / "liaison-10479.tick.json"
    update_state(path, lambda s: s.update({"ticks": 1, "register": "autonomous"}))

    fired_at = time.time()
    update_state(
        path,
        lambda s: s.update(
            {"last_induction_at": fired_at, "last_induction_fingerprint": "abc123"}
        ),
    )

    # The loop's in-memory state predates the fire and never carries these keys.
    loop_state = {"ticks": 2, "register": "autonomous"}
    absorb_operator_edits(loop_state, path)
    update_state(path, lambda s: s.update(loop_state))

    on_disk = load_state(path)
    assert on_disk["last_induction_at"] == fired_at, (
        "the ticker's next write erased the navigator's grace anchor"
    )
    assert on_disk["last_induction_fingerprint"] == "abc123"


@pytest.mark.offline
def test_grace_actually_holds_after_a_ticker_write(monkeypatch) -> None:  # noqa: ANN001
    """The consequence the persistence protects: a recent wake blocks re-fire.

    ``navigator_single_flight`` reads the real on-disk lock for the root, so it
    is stubbed here — otherwise this asserts against whatever the live house is
    doing rather than against the grace clause under test.
    """
    monkeypatch.setattr(
        "bus_watch.navigator_wake.navigator_single_flight_held", lambda _root: False
    )
    digest = {
        "root": {"id": "10479"},
        "policy": {
            "navigator_model": "cdp/opus-5-high",
            "navigator_model_source": "override",
            "navigator_grace_seconds": 900,
        },
    }
    just_fired = {"last_induction_at": time.time()}
    evaluation = evaluate_navigator_wake(
        digest, just_fired, register="autonomous"
    )
    assert evaluation["fire"] is False
    assert evaluation["skip_reason"] == "grace_not_elapsed"

    # ...and the same evaluation with the anchor lost (the pre-fix state) fires.
    assert evaluate_navigator_wake(digest, {}, register="autonomous")["fire"] is True
