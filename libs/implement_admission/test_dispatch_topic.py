"""Tests for dispatch topic extraction (G5.1)."""

from __future__ import annotations

from implement_admission.dispatch_topic import (
    conductor_mission_topic,
    derive_conductor_topic_from_packet,
    extract_dispatch_topic,
)


def test_extract_dispatch_topic_skips_yaml_frontmatter() -> None:
    body = "---\npacket_kind: implement\nwork_key: todo:foo\n---\n\nso_what: board topic\n"
    assert extract_dispatch_topic(body) == "board topic"
    assert extract_dispatch_topic("---\nonly frontmatter\n---\n") is None
    assert extract_dispatch_topic("---") is None


def test_extract_dispatch_topic_does_not_yield_frontmatter_delimiter() -> None:
    assert extract_dispatch_topic("---\n...\n---") is None


def test_extract_dispatch_topic_prefers_so_what() -> None:
    body = (
        "TYPE: DIRECTIVE\n"
        "so_what: ULG gains reliable closeout SMS\n"
        "intent: implement the board topic line\n"
    )
    assert extract_dispatch_topic(body) == "ULG gains reliable closeout SMS"


def test_extract_dispatch_topic_prefers_ulg_gain() -> None:
    body = "hello world\nulg_gain: operators see mission prose on the board\n"
    assert extract_dispatch_topic(body) == "operators see mission prose on the board"


def test_extract_dispatch_topic_prefers_intent_in_corpus() -> None:
    body = """<corpus>
Source: todo:foo
Intent: Conductor unify — sdk nest tree
</corpus>
"""
    assert extract_dispatch_topic(body) == "Conductor unify — sdk nest tree"


def test_extract_dispatch_topic_prefers_problem_in_corpus() -> None:
    body = """<corpus>
Problem: lease park restore ordering
</corpus>
"""
    assert extract_dispatch_topic(body) == "lease park restore ordering"


def test_extract_dispatch_topic_skips_conductor_session_scope() -> None:
    body = """<scope>
Conductor session for `todo:foo`.
Mechanical G5.1 on Lane B under conductor.
</scope>
"""
    assert extract_dispatch_topic(body) == "Mechanical G5.1 on Lane B under conductor."


def test_extract_dispatch_topic_skips_skill_and_metadata_lines() -> None:
    body = (
        "Use the `conductor` skill\n"
        "packet_kind: implement\n"
        "work_key: todo:foo\n"
        "<scope>\nReal scope prose here.\n</scope>\n"
    )
    assert extract_dispatch_topic(body) == "Real scope prose here."


def test_extract_dispatch_topic_caps_at_160() -> None:
    long = "word " * 80
    topic = extract_dispatch_topic(f"so_what: {long}")
    assert topic is not None
    assert len(topic) <= 161
    assert topic.endswith("…")


def test_conductor_mission_topic_caps_at_160() -> None:
    topic = conductor_mission_topic("x" * 200)
    assert len(topic) <= 161
    assert topic.endswith("…")


def test_derive_conductor_topic_from_packet_reads_corpus_intent() -> None:
    packet = """---
packet_kind: conductor
---
<corpus>
Intent: Conductor unify — sdk-nest-tree-fast
</corpus>
"""
    assert (
        derive_conductor_topic_from_packet(packet)
        == "Conductor unify — sdk-nest-tree-fast"
    )
