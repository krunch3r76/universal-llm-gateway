"""Property tests for dynamic fence escalation."""

from __future__ import annotations

import random
import string

import pytest

from session_store.fence import (
    choose_fence_char,
    extract_fenced,
    fence_length,
    longest_run,
    wrap_fenced,
)
from session_store.models import SessionDoc, Turn
from session_store.schema import parse_transcript, render_transcript, section_count


def _single_turn_doc(body: str, *, turn_n: int = 1, role: str = "user") -> SessionDoc:
    return SessionDoc(
        session_id="hostile-probe",
        meta={
            "session_id": "hostile-probe",
            "schema_version": "1",
            "turn_count": str(turn_n),
        },
        rollup_text="",
        index_lines=[],
        archive_map_lines=[],
        turns=[Turn(n=turn_n, role=role, body=body)],
    )


def _assert_transcript_round_trip_stable(body: str, *, turn_n: int = 1, role: str = "user") -> None:
    doc = _single_turn_doc(body, turn_n=turn_n, role=role)
    count_before = section_count(doc)
    rendered = render_transcript(doc)
    doc2 = parse_transcript(rendered)
    rendered2 = render_transcript(doc2)
    assert rendered == rendered2
    assert section_count(doc2) == count_before
    assert doc2.turns[0].body == body


def _mandatory_hostile_bodies() -> list[tuple[str, str]]:
    cases: list[tuple[str, str]] = []
    for n in range(1, 9):
        cases.append((f"backtick_run_{n}", "`" * n))
        cases.append((f"tilde_run_{n}", "~" * n))
    cases.extend(
        [
            ("mixed_runs", "``~~~````~~"),
            ("run_at_first_byte", "````\nrest"),
            ("run_at_last_byte", "rest\n````"),
            ("fake_turn_heading", "## Turn 0007 — assistant\ninside body"),
            ("nested_fence", "outer\n````text\nnested body\n````\nouter tail"),
            ("crlf_endings", "line one\r\nline two\r\nline three"),
            ("no_trailing_newline", "content without trailing newline"),
            ("empty_body", ""),
        ]
    )
    return cases


MANDATORY_HOSTILE_BODIES = _mandatory_hostile_bodies()


def _random_tick_run(max_len: int, char: str) -> str:
    n = random.randint(0, max_len)
    return char * n


def _hostile_fragment() -> str:
    kind = random.randint(0, 7)
    if kind == 0:
        return "`" * random.randint(1, 8)
    if kind == 1:
        return "~" * random.randint(1, 8)
    if kind == 2:
        return "## Turn 0007 — assistant"
    if kind == 3:
        return "````text\nnested\n````"
    if kind == 4:
        return random.choice(["\r\n", "\r", "\n"])
    if kind == 5:
        bt = "`" * random.randint(1, 4)
        td = "~" * random.randint(1, 4)
        return bt + td
    if kind == 6:
        return random.choice(["", "x" * random.randint(0, 3)])
    return "".join(random.choices(string.printable, k=random.randint(1, 12)))


@pytest.mark.parametrize(("label", "body"), MANDATORY_HOSTILE_BODIES, ids=[label for label, _ in MANDATORY_HOSTILE_BODIES])
def test_hostile_fence_mandatory_round_trip(label: str, body: str) -> None:
    del label
    _assert_transcript_round_trip_stable(body)


def test_hostile_fence_property_random_compositions() -> None:
    for _ in range(150):
        parts = [_hostile_fragment() for _ in range(random.randint(1, 6))]
        if random.random() < 0.3:
            parts.insert(0, "`" * random.randint(1, 8))
        if random.random() < 0.3:
            parts.append("~" * random.randint(1, 8))
        body = "".join(parts)
        _assert_transcript_round_trip_stable(body)


def test_fence_length_exceeds_longest_run() -> None:
    for _ in range(200):
        body_parts = []
        for _i in range(random.randint(0, 8)):
            ch = random.choice(["`", "~"])
            body_parts.append(_random_tick_run(6, ch))
            body_parts.append("".join(random.choices(string.ascii_letters + "\n", k=5)))
        body = "".join(body_parts)
        ch = choose_fence_char(body)
        assert fence_length(body, ch) > longest_run(body, ch)


def test_fence_round_trip_random_content() -> None:
    for _ in range(100):
        body = "".join(
            random.choices(string.printable + "`~" * 3, k=random.randint(0, 200))
        )
        wrapped = wrap_fenced(body)
        assert extract_fenced(wrapped) == body
