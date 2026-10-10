"""Shared handoff letter: grok profile, stamped profile, stamp, binding, lag."""

from __future__ import annotations

import pytest
from continuity_tape.handoff import (
    HANDOFF_MAX_BYTES,
    bare_messages_sha,
    handoff_binding,
    handoff_binding_stamped,
    handoff_lag_turns,
    handoff_sha12,
    place_handoff,
    stamp_handoff,
    validate_handoff,
)

pytestmark = pytest.mark.offline

_SHA = "ab" * 32


def _grok(**overrides: object) -> dict:
    letter: dict = {
        "house_cp": "house-cp:h:1",
        "messages_sha256": _SHA,
        "as_of_message_index": 1,
        "author": "grok-bot-hm1",
        "mission": "resume the house",
        "state": "open",
        "next_acts": ["read the card"],
        "load_bearing": ["intent is not in the transcript"],
    }
    letter.update(overrides)
    return letter


def _stamped(**overrides: object) -> dict:
    letter: dict = {
        "author": "cursor",
        "mission": "ship the letter",
        "where_we_left_off": "seal write",
        "state": "open",
        "next_acts": ["stamp the file"],
        "load_bearing": ["the tip is empty"],
    }
    letter.update(overrides)
    return letter


def test_grok_profile_accepts_lane1_letter() -> None:
    assert validate_handoff(_grok()) is None
    assert validate_handoff(_grok(), profile="grok") is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"house_cp": "nope"},
        {"messages_sha256": "short"},
        {"as_of_message_index": 0},
        {"author": "  "},
        {"mission": "has\nnewline"},
        {"state": 1},
        {"next_acts": []},
        {"load_bearing": []},
    ],
)
def test_grok_profile_rejects_each_required(overrides: dict) -> None:
    code, field = validate_handoff(_grok(**overrides)) or ("", "")
    assert code == "admit.handoff_invalid"
    assert field


def test_stamped_profile_accepts_author_letter() -> None:
    letter = _stamped(
        in_flight="wip",
        rulings=["r1"],
        files="a.py",
        later=["soon"],
        do_not_trust="chat",
        as_of_turn=3,
        kept="extra",
    )
    assert validate_handoff(letter, profile="stamped") is None


@pytest.mark.parametrize(
    "drop",
    ["author", "mission", "where_we_left_off", "state", "next_acts", "load_bearing"],
)
def test_stamped_missing_required(drop: str) -> None:
    letter = _stamped()
    del letter[drop]
    code, field = validate_handoff(letter, profile="stamped") or ("", "")
    assert code == "checkpoint.handoff_invalid"
    assert field == drop or field == "load_bearing"


def test_stamped_invalid_shapes() -> None:
    cases = [
        _stamped(author=""),
        _stamped(mission=""),
        _stamped(mission="a\nb"),
        _stamped(where_we_left_off="  "),
        _stamped(where_we_left_off="x" * 601),
        _stamped(state=1),
        _stamped(next_acts=["ok", 1]),
        _stamped(load_bearing=[]),
        _stamped(load_bearing=[], load_bearing_reason="  "),
        _stamped(as_of_turn=True),
        _stamped(as_of_turn=0),
        _stamped(later=1),
        "x",
    ]
    for letter in cases:
        defect = validate_handoff(letter, profile="stamped")
        assert defect is not None
        assert defect[0] == "checkpoint.handoff_invalid"


def test_empty_load_bearing_ok_with_reason() -> None:
    letter = _stamped(load_bearing=[], load_bearing_reason="none yet")
    assert validate_handoff(letter, profile="stamped") is None


@pytest.mark.parametrize(
    "key",
    [
        "session_id",
        "messages_sha256",
        "as_of_message_index",
        "house",
        "transcript_id",
        "surface",
        "stamped_at",
        "turns_at_seal",
        "house_cp",
    ],
)
def test_pipeline_owned_key_refused(key: str) -> None:
    letter = _stamped(**{key: "nope"})
    code, field = validate_handoff(letter, profile="stamped") or ("", "")
    assert code == "checkpoint.handoff_invalid"
    assert field == key


def test_size_cap() -> None:
    letter = _stamped(mission="m" * (HANDOFF_MAX_BYTES + 8))
    code, _field = validate_handoff(letter, profile="stamped") or ("", "")
    assert code == "checkpoint.handoff_too_large"
    grok = _grok(mission="m" * (HANDOFF_MAX_BYTES + 8))
    gcode, _ = validate_handoff(grok) or ("", "")
    assert gcode == "admit.handoff_too_large"


def test_stamp_and_binding() -> None:
    letter = _stamped(as_of_turn=2)
    stamped = stamp_handoff(
        letter,
        session_id="cursor-1",
        messages_sha256=_SHA,
        message_count=4,
        thread="15902",
        transcript_id="tid",
        surface="cursor",
        turn_count=9,
        now="2026-10-09T18:00:00+00:00",
    )
    assert stamped["author"] == "cursor"
    assert stamped["session_id"] == "cursor-1"
    assert stamped["messages_sha256"] == _SHA
    assert stamped["as_of_message_index"] == 4
    assert stamped["house"] == "agent-bus:15902"
    assert stamped["transcript_id"] == "tid"
    assert stamped["surface"] == "cursor"
    assert stamped["stamped_at"] == "2026-10-09T18:00:00Z"
    assert stamped["turns_at_seal"] == 9
    assert "session_id" not in letter
    messages = [{}, {}, {}, {}]
    assert handoff_binding_stamped(
        stamped, messages=messages, messages_sha256=_SHA, session_id="cursor-1"
    ) == ("bound", [])
    assert handoff_sha12(stamped) != "none"
    assert handoff_lag_turns(stamped) == 7


def test_binding_stale_legs_shape_missing() -> None:
    messages = [{}]
    base = stamp_handoff(
        _stamped(),
        session_id="s",
        messages_sha256=_SHA,
        message_count=1,
        thread="1",
        transcript_id="t",
        surface="cursor",
        turn_count=1,
        now="2026-10-09T00:00:00Z",
    )
    assert handoff_binding_stamped(
        None, messages=messages, messages_sha256=_SHA, session_id="s"
    ) == ("missing", [])
    assert handoff_binding_stamped(
        "x", messages=messages, messages_sha256=_SHA, session_id="s"
    ) == ("stale", ["shape"])
    for key, bad in (
        ("messages_sha256", "ff" * 32),
        ("as_of_message_index", 9),
        ("session_id", "other"),
    ):
        broken = dict(base)
        broken[key] = bad
        status, reasons = handoff_binding_stamped(
            broken, messages=messages, messages_sha256=_SHA, session_id="s"
        )
        assert status == "stale"
        assert key in reasons


def test_lag_none_when_not_ints() -> None:
    assert handoff_lag_turns(None) is None
    assert handoff_lag_turns({"turns_at_seal": True, "as_of_turn": 1}) is None
    assert handoff_lag_turns({"turns_at_seal": 3}) is None


def test_place_handoff_stamp_carry_none() -> None:
    dumped: dict = {}
    stamp = {
        "session_id": "s",
        "messages_sha256": _SHA,
        "message_count": 1,
        "thread": "9",
        "transcript_id": "t",
        "surface": "cursor",
        "turn_count": 2,
        "now": "2026-10-09T00:00:00Z",
    }
    assert (
        place_handoff(dumped, letter=_stamped(), prior=None, extend=False, stamp=stamp)
        == "stamped"
    )
    assert dumped["handoff"]["house"] == "agent-bus:9"
    prior = dict(dumped["handoff"])
    dumped2: dict = {}
    assert (
        place_handoff(dumped2, letter=None, prior=prior, extend=True, stamp=None)
        == "carried"
    )
    assert dumped2["handoff"] == prior
    status, reasons = handoff_binding_stamped(
        dumped2["handoff"],
        messages=[{}, {}],
        messages_sha256="cd" * 32,
        session_id="s",
    )
    assert status == "stale"
    assert "messages_sha256" in reasons
    dumped3: dict = {"handoff": {"old": True}}
    assert (
        place_handoff(dumped3, letter=None, prior=None, extend=False, stamp=None)
        == "none"
    )
    assert dumped3["handoff"] == {"old": True}
    dumped4: dict = {}
    assert (
        place_handoff(dumped4, letter=None, prior=None, extend=False, stamp=None)
        == "none"
    )
    assert "handoff" not in dumped4


def test_grok_binding_unchanged() -> None:
    letter = _grok()
    assert handoff_binding(
        letter, messages=[{}], messages_sha256=_SHA, house="h", n=1
    ) == ("bound", [])
    assert bare_messages_sha("sha256:" + _SHA) is None
    assert bare_messages_sha(_SHA) == _SHA
