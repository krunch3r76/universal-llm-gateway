"""Schema reader/writer tests."""

from __future__ import annotations

import pytest

from session_store.models import ImmutableArchiveError, SchemaError
from session_store.schema import parse_transcript, render_transcript

SMOKE_TAIL = """## Turn 0009 — user

~~~~text
OK. And where does the ledger live?
~~~~
"""

MALFORMED_HEADING = """# Session bad

## Meta

session_id: bad

## Rollup



## Index

(none)

## Archive Map

(none)

## Turn 001 — user

~~~~text
bad heading width
~~~~
"""


def test_parse_smoke_tail_turn() -> None:
    text = (
        "# Session smoke-0001\n\n## Meta\n\nsession_id: smoke-0001\n\n"
        "## Rollup\n\n\n## Index\n\n(none)\n\n## Archive Map\n\n(none)\n\n"
        + SMOKE_TAIL
    )
    doc = parse_transcript(text)
    assert doc.turns[-1].n == 9
    assert doc.turns[-1].role == "user"
    assert "ledger" in doc.turns[-1].body


def test_rejects_malformed_turn_heading() -> None:
    with pytest.raises(SchemaError):
        parse_transcript(MALFORMED_HEADING)


def test_immutable_archive_writer_refusal() -> None:
    original = parse_transcript(
        "# Session arch-001\n\n## Meta\n\nsession_id: arch-001\n"
        "immutable: true\n\n## Rollup\n\n\n## Index\n\n(none)\n\n"
        "## Archive Map\n\n(none)\n"
    )
    mutated = parse_transcript(
        "# Session arch-001\n\n## Meta\n\nsession_id: arch-001\n"
        "immutable: true\n\n## Rollup\n\nchanged\n\n## Index\n\n(none)\n\n"
        "## Archive Map\n\n(none)\n"
    )
    with pytest.raises(ImmutableArchiveError):
        render_transcript(mutated, original=original)
