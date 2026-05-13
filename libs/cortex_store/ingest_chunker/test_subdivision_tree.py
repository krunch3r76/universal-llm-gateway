"""Unit tests for the subdivision-tree chunker (spec § 3.2).

Covers:
  * Single-depth statute (a) ... (b) ... emits one chunk per leaf with
    pinpoints ``a`` and ``b``.
  * Three-depth nested statute emits dotted-path pinpoints
    (e.g. ``f-1-B``) — the canonical ``cortex://legal_source:rtc-63.2#f-1-B``
    form from spec § 2.3.
  * Preamble text before the first ``(a)`` is captured as a chunk with
    pinpoint ``preamble``.
  * Internal nodes with introductory text emit at the intermediate path.
  * Empty input emits one no-pinpoint chunk (no-loss invariant).
  * The dispatch surface ``chunk_for_authority`` falls back to the
    default chunker when ``authority_class`` is None or unrecognized.
"""

from __future__ import annotations

from cortex_store.ingest_chunker import chunk_for_authority
from cortex_store.ingest_chunker.subdivision_tree import (
    chunk_subdivision_tree,
)


def test_single_depth_statute_emits_one_chunk_per_letter() -> None:
    text = (
        "(a) The first subdivision text.\n"
        "(b) The second subdivision text.\n"
        "(c) The third subdivision text.\n"
    )
    chunks = chunk_subdivision_tree(text)
    pins = [c.pinpoint for c in chunks]
    assert pins == ["a", "b", "c"]
    assert "first subdivision" in chunks[0].text
    assert "second subdivision" in chunks[1].text


def test_three_depth_nested_statute_dotted_path() -> None:
    text = (
        "(f) For purposes of this section:\n"
        "(1) The following definitions apply:\n"
        "(A) First inner definition.\n"
        "(B) Second inner definition; this is the f-1-B leaf.\n"
        "(2) Another mid-level rule.\n"
    )
    chunks = chunk_subdivision_tree(text)
    pins = {c.pinpoint for c in chunks}
    assert "f-1-B" in pins
    assert "f-1-A" in pins
    assert "f-2" in pins
    target = next(c for c in chunks if c.pinpoint == "f-1-B")
    assert "Second inner definition" in target.text


def test_preamble_chunk_emitted() -> None:
    text = (
        "Section 63.2. Change in ownership.\n\n"
        "(a) The first subdivision.\n"
        "(b) The second subdivision.\n"
    )
    chunks = chunk_subdivision_tree(text)
    pins = [c.pinpoint for c in chunks]
    assert pins[0] == "preamble"
    assert "Change in ownership" in chunks[0].text


def test_internal_node_with_intro_text_emits_path_chunk() -> None:
    text = (
        "(f) For purposes of this section:\n"
        "(1) The following definitions apply:\n"
        "(A) First inner.\n"
    )
    chunks = chunk_subdivision_tree(text)
    pins = {c.pinpoint for c in chunks}
    # Both the leaf (f-1-A) and the internal nodes with intro text
    # (f, f-1) are addressable.
    assert "f-1-A" in pins
    assert "f" in pins
    assert "f-1" in pins


def test_empty_input_emits_single_no_pinpoint_chunk() -> None:
    chunks = chunk_subdivision_tree("")
    assert len(chunks) == 1
    assert chunks[0].pinpoint is None


def test_dispatch_falls_back_to_default_when_authority_class_none() -> None:
    text = "Just some prose.\n\nA second paragraph."
    chunks = chunk_for_authority(text, authority_class=None)
    assert all(c.pinpoint is None for c in chunks)
    assert chunks[0].text


def test_dispatch_falls_back_to_default_for_unknown_class() -> None:
    text = "Just some prose."
    chunks = chunk_for_authority(text, authority_class="unknown_class")
    assert all(c.pinpoint is None for c in chunks)


def test_dispatch_routes_statute_to_subdivision_tree() -> None:
    text = "(a) First.\n(b) Second."
    chunks = chunk_for_authority(text, authority_class="statute")
    pins = [c.pinpoint for c in chunks]
    assert pins == ["a", "b"]


def test_dispatch_routes_probate_code_to_subdivision_tree() -> None:
    text = "(a) Probate first.\n(b) Probate second."
    chunks = chunk_for_authority(text, authority_class="probate_code")
    pins = [c.pinpoint for c in chunks]
    assert pins == ["a", "b"]



def test_out_of_order_marker_attaches_and_logs(caplog) -> None:
    """A depth-3 marker before any depth-1/2 ancestor is clamped and warned."""
    import logging

    text = "(B) Orphan upper-letter marker before any (a) or (1).\n"
    with caplog.at_level(logging.WARNING, logger="cortex-api.ingest_chunker.subdivision_tree"):
        chunks = chunk_subdivision_tree(text)
    pins = [c.pinpoint for c in chunks]
    # Clamped to depth 1 — attached as a top-level node with label "B"
    assert pins == ["B"]
    assert any("out-of-order marker" in r.message for r in caplog.records)


def test_duplicate_sibling_labels_collision_warned(caplog) -> None:
    """Two ``(a)`` siblings under root emit a collision warning."""
    import logging

    text = "(a) First subdivision.\n(a) Duplicate-label sibling.\n"
    with caplog.at_level(logging.WARNING, logger="cortex-api.ingest_chunker.subdivision_tree"):
        chunks = chunk_subdivision_tree(text)
    pins = [c.pinpoint for c in chunks]
    # Both chunks are emitted with the same pinpoint — the resolver
    # will return only one when keyed by (source_uri, pinpoint).
    assert pins == ["a", "a"]
    assert any("duplicate sibling label" in r.message for r in caplog.records)


def test_depth4_roman_marker_logged_as_body(caplog) -> None:
    """Lowercase roman ``(i)`` inside a depth-3 node is logged + kept as body."""
    import logging

    text = (
        "(a) Outer.\n"
        "(1) Mid.\n"
        "(A) Inner upper.\n"
        "(i) Roman small — out of scope for v1 depth-tree.\n"
    )
    with caplog.at_level(logging.DEBUG, logger="cortex-api.ingest_chunker.subdivision_tree"):
        chunks = chunk_subdivision_tree(text)
    # The (i) line is treated as body of the (A) node — content preserved.
    a_chunk = next(c for c in chunks if c.pinpoint == "a-1-A")
    assert "Roman small" in a_chunk.text
    # And a debug log mentions the marker-like miss.
    assert any("marker-like" in r.message for r in caplog.records)
