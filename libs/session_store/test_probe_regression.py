"""Probe regression: embedded ATX inside fence preserves section count."""

from session_store.schema import parse_transcript, render_transcript, section_count

PROBE_TRANSCRIPT = """# Session probe-5867-schema

## Meta

session_id: probe-5867-schema
turn_count: 4
schema_version: 1

## Rollup

Arc: user checking Option 3 status.

## Index

0001 user: asks about pipeline CDP branch status
0002 assistant: confirms Option 3 in tree, uncommitted
0003 user: pastes markdown containing ATX headings inside fence
0004 assistant: acknowledges fenced content

## Archive Map

(none)

## Turn 0001 — user

~~~~text
What is the status of the Option 3 pipeline CDP branch?
~~~~

## Turn 0002 — assistant

tools: none

~~~~text
Option 3 is implemented in tree; commit state unverified.
~~~~

## Turn 0003 — user

~~~~text
Here is my doc draft:

## My Fake Heading

Some content with ```backticks``` inside.
~~~~

## Turn 0004 — assistant

~~~~text
Received the draft with the fake heading.
~~~~
"""


def test_probe_round_trip_section_count() -> None:
    doc1 = parse_transcript(PROBE_TRANSCRIPT)
    count1 = section_count(doc1)
    rendered = render_transcript(doc1)
    doc2 = parse_transcript(rendered)
    count2 = section_count(doc2)
    assert count1 == count2 == 9
    assert doc2.turns[2].body == doc1.turns[2].body
