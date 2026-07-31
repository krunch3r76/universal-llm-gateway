"""Tests for §6.10 compaction-pointer read semantics."""

from __future__ import annotations

from cortex_store.compaction import (
    POINTER_SQL_LIKE,
    SUMMARY_SQL_LIKE,
    apply_compaction_filter,
    extract_summary_ids,
    filter_compaction_pointers,
    is_compaction_pointer,
    is_consolidation_summary,
    is_tombstone_only,
    synthesize_predicate_summary,
)


def _a(
    claim: str,
    *,
    id: int = 1,
    superseded_by: int | None = None,
) -> dict:
    return {
        "id": id,
        "claim": claim,
        "superseded_by": superseded_by,
        "confidence": "confirmed",
        "created_at": "2026-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Detector tests
# ---------------------------------------------------------------------------


class TestIsCompactionPointer:
    def test_exact_match(self) -> None:
        assert is_compaction_pointer("Compacted into archive summary 8170")

    def test_case_insensitive(self) -> None:
        assert is_compaction_pointer("compacted into archive summary 123")
        assert is_compaction_pointer("COMPACTED INTO ARCHIVE SUMMARY 99")

    def test_no_match_archived_to(self) -> None:
        assert not is_compaction_pointer("Archived to note:hei-economics — economics.")

    def test_no_match_summary(self) -> None:
        assert not is_compaction_pointer("archive summary for account:hei")

    def test_no_match_empty(self) -> None:
        assert not is_compaction_pointer("")

    def test_no_match_partial(self) -> None:
        # Missing trailing integer
        assert not is_compaction_pointer("Compacted into archive summary ")


class TestIsConsolidationSummary:
    def test_exact_match(self) -> None:
        assert is_consolidation_summary(
            "archive summary for account:hometap-investment"
        )

    def test_case_insensitive(self) -> None:
        assert is_consolidation_summary("Archive Summary: HEI investment history")
        assert is_consolidation_summary("ARCHIVE SUMMARY of children")

    def test_no_match_pointer(self) -> None:
        assert not is_consolidation_summary("Compacted into archive summary 8170")

    def test_no_match_live_state(self) -> None:
        assert not is_consolidation_summary("Live operative state of the deal")

    def test_no_match_empty(self) -> None:
        assert not is_consolidation_summary("")


# ---------------------------------------------------------------------------
# is_tombstone_only tests
# ---------------------------------------------------------------------------


class TestIsTombstoneOnly:
    def test_empty_list_returns_false(self) -> None:
        # ∀ empty: not tombstoned — it is empty
        assert not is_tombstone_only([])

    def test_single_pointer_returns_true(self) -> None:
        assert is_tombstone_only(["Compacted into archive summary 42"])

    def test_all_pointers_returns_true(self) -> None:
        assert is_tombstone_only(
            [
                "Compacted into archive summary 100",
                "Compacted into archive summary 200",
            ]
        )

    def test_mixed_pointer_and_summary_returns_false(self) -> None:
        # consolidation summary is NOT a compaction pointer
        assert not is_tombstone_only(
            [
                "Compacted into archive summary 100",
                "archive summary of HEI children",
            ]
        )

    def test_single_live_claim_returns_false(self) -> None:
        assert not is_tombstone_only(["Live operative state of the deal"])

    def test_single_archived_to_claim_returns_false(self) -> None:
        # "Archived to …" is not the same pattern as "Compacted into …"
        assert not is_tombstone_only(["Archived to note:hei — economics."])

    def test_case_insensitive_pointer_counts(self) -> None:
        assert is_tombstone_only(["compacted into archive summary 99"])


# ---------------------------------------------------------------------------
# synthesize_predicate_summary tests
# ---------------------------------------------------------------------------


class TestSynthesizePredicateSummary:
    def test_empty_edges_empty_children_returns_empty_string(self) -> None:
        assert synthesize_predicate_summary([], []) == ""

    def test_single_edge_type_no_children(self) -> None:
        result = synthesize_predicate_summary(
            [{"type_id": "related_to", "count": 3}], []
        )
        assert result == "related_to(3)"

    def test_multiple_edge_types_no_children(self) -> None:
        result = synthesize_predicate_summary(
            [
                {"type_id": "related_to", "count": 3},
                {"type_id": "mentions", "count": 1},
            ],
            [],
        )
        assert result == "related_to(3); mentions(1)"

    def test_no_edge_types_with_archival_children(self) -> None:
        # Tombstone nav-hint path: entity has no live relationships, only archives_to edges
        result = synthesize_predicate_summary([], ["archive:001"])
        assert result == "archived_into([archive:001])"

    def test_edge_types_with_single_child(self) -> None:
        result = synthesize_predicate_summary(
            [{"type_id": "related_to", "count": 2}],
            ["archive:001"],
        )
        assert result == "related_to(2); archived_into([archive:001])"

    def test_multiple_children_joined_with_comma(self) -> None:
        result = synthesize_predicate_summary([], ["archive:001", "archive:002"])
        assert result == "archived_into([archive:001, archive:002])"

    def test_edge_types_before_archival_in_output(self) -> None:
        # edge-type parts always precede the archival_into part
        result = synthesize_predicate_summary(
            [{"type_id": "has_tag", "count": 5}, {"type_id": "related_to", "count": 2}],
            ["archive:child"],
        )
        assert result == "has_tag(5); related_to(2); archived_into([archive:child])"

    def test_deterministic_across_calls(self) -> None:
        args = ([{"type_id": "t", "count": 1}], ["c1", "c2"])
        assert synthesize_predicate_summary(*args) == synthesize_predicate_summary(
            *args
        )


# ---------------------------------------------------------------------------
# apply_compaction_filter tests
# ---------------------------------------------------------------------------


class TestApplyCompactionFilterPassthrough:
    def test_empty_list(self) -> None:
        result, meta = apply_compaction_filter([])
        assert result == []
        assert meta is None

    def test_no_compaction_pattern_unchanged(self) -> None:
        assertions = [_a("Live operative state", id=1), _a("Another live fact", id=2)]
        result, meta = apply_compaction_filter(assertions)
        assert result == assertions
        assert meta is None

    def test_include_flag_bypasses_all_filtering(self) -> None:
        pointer = _a("Compacted into archive summary 8170", id=1)
        summary = _a("archive summary of HEI", id=2)
        live = _a("Live state", id=3)
        assertions = [pointer, summary, live]
        result, meta = apply_compaction_filter(
            assertions, include_compaction_pointers=True
        )
        assert result == assertions
        assert meta is None


class TestApplyCompactionFilterMixed:
    def test_summary_first_pointers_last(self) -> None:
        live = _a("Live operative state", id=3)
        summary = _a("archive summary of HEI children", id=2)
        p1 = _a("Compacted into archive summary 8170", id=1)
        p2 = _a("Compacted into archive summary 8170", id=4)
        # Simulate DB order (created_at DESC: newest first)
        assertions = [p1, p2, live, summary]

        result, meta = apply_compaction_filter(assertions)

        assert result[0] is summary, "Summary must be first"
        assert live in result
        assert p1 in result
        assert p2 in result
        assert result.index(p1) > result.index(live), "Pointers must follow live state"
        assert result.index(p2) > result.index(live), "Pointers must follow live state"

        assert meta is not None
        assert meta["mode"] == "pointers_deprioritized"
        assert meta["pointer_count"] == 2
        assert meta["summary_count"] == 1

    def test_multiple_summaries_all_surface_first(self) -> None:
        s1 = _a("archive summary batch A", id=10)
        s2 = _a("archive summary batch B", id=11)
        p1 = _a("Compacted into archive summary 10", id=1)
        live = _a("Current status", id=5)
        assertions = [p1, live, s1, s2]

        result, meta = apply_compaction_filter(assertions)

        assert result[0] in (s1, s2)
        assert result[1] in (s1, s2)
        assert result[2] is live
        assert result[3] is p1
        assert meta is not None
        assert meta["pointer_count"] == 1
        assert meta["summary_count"] == 2

    def test_superseded_assertions_preserved_in_stream(self) -> None:
        """Superseded assertions in the response stream must not be lost."""
        live_summary = _a("archive summary", id=2)
        old_pointer = _a("Compacted into archive summary 99", id=1, superseded_by=99)
        # old_pointer is superseded — not active. So not tombstone-only.
        assertions = [old_pointer, live_summary]
        result, meta = apply_compaction_filter(assertions)

        assert live_summary in result
        assert old_pointer in result
        assert meta is not None
        assert meta["mode"] == "pointers_deprioritized"


class TestApplyCompactionFilterTombstone:
    def test_tombstone_collapse_returns_summary_and_hint(self) -> None:
        summary = _a("archive summary for account:hometap-investment", id=100)
        p1 = _a("Compacted into archive summary 100", id=1)
        p2 = _a("Compacted into archive summary 100", id=2)
        # All active assertions are pointers; summary is superseded (old)
        summary_superseded = {**summary, "superseded_by": 999}
        assertions = [p1, p2, summary_superseded]

        result, meta = apply_compaction_filter(
            assertions,
            archives_to_children=["note:hei-economics", "event:hei-history"],
        )

        assert meta is not None
        assert meta["mode"] == "tombstone_collapsed"
        assert meta["pointer_count"] == 2
        assert "note:hei-economics" in meta["children"]
        assert "event:hei-history" in meta["children"]
        assert "archived → see children" in meta["navigation_hint"]
        # Only the summary is in the result (pointers folded)
        assert summary_superseded in result
        assert p1 not in result
        assert p2 not in result

    def test_tombstone_no_children_degrades_gracefully(self) -> None:
        p1 = _a("Compacted into archive summary 100", id=1)
        result, meta = apply_compaction_filter([p1], archives_to_children=None)

        assert meta is not None
        assert meta["mode"] == "tombstone_collapsed"
        assert meta["children"] == []
        assert "[]" in meta["navigation_hint"]

    def test_tombstone_with_empty_children_list(self) -> None:
        p1 = _a("Compacted into archive summary 100", id=1)
        result, meta = apply_compaction_filter([p1], archives_to_children=[])

        assert meta is not None
        assert meta["mode"] == "tombstone_collapsed"
        assert meta["children"] == []

    def test_active_summary_prevents_tombstone(self) -> None:
        """An active consolidation summary means the entity is NOT tombstone-only."""
        active_summary = _a("archive summary of children", id=2)
        p1 = _a("Compacted into archive summary 2", id=1)
        assertions = [p1, active_summary]

        result, meta = apply_compaction_filter(assertions)

        # active = [p1, active_summary] — active_summary is not a pointer
        # → NOT tombstone-only → pointers_deprioritized
        assert meta is not None
        assert meta["mode"] == "pointers_deprioritized"
        assert result[0] is active_summary
        assert result[-1] is p1

    def test_aggregate_filter_strict_excludes_pointers(self) -> None:
        """todo:cortex-aggregate-compaction-filter — strict-exclude semantics."""
        live = _a("Live operative state", id=1)
        p1 = _a("Compacted into archive summary 8170", id=2)
        summary = _a("archive summary of HEI", id=3)
        p2 = _a("Compacted into archive summary 8170", id=4)
        kept, count = filter_compaction_pointers([live, p1, summary, p2])

        assert count == 2
        assert kept == [live, summary]

    def test_aggregate_filter_override_returns_all(self) -> None:
        live = _a("Live operative state", id=1)
        p1 = _a("Compacted into archive summary 8170", id=2)
        kept, count = filter_compaction_pointers(
            [live, p1], include_compaction_pointers=True
        )
        assert count == 0
        assert kept == [live, p1]

    def test_aggregate_filter_empty_list(self) -> None:
        kept, count = filter_compaction_pointers([])
        assert kept == []
        assert count == 0

    def test_aggregate_filter_no_pointers_unchanged(self) -> None:
        live = _a("Live state", id=1)
        summary = _a("archive summary X", id=2)
        kept, count = filter_compaction_pointers([live, summary])
        assert count == 0
        assert kept == [live, summary]

    def test_extract_summary_ids_dedups(self) -> None:
        rows = [
            _a("Compacted into archive summary 100", id=1),
            _a("Compacted into archive summary 100", id=2),
            _a("Compacted into archive summary 200", id=3),
            _a("Live state", id=4),
        ]
        ids = sorted(extract_summary_ids(rows))
        assert ids == [100, 200]

    def test_extract_summary_ids_empty_when_no_pointers(self) -> None:
        rows = [_a("Live state", id=1), _a("archive summary X", id=2)]
        assert extract_summary_ids(rows) == []

    def test_pointer_sql_like_matches_pointer_re(self) -> None:
        """The SQL LIKE pattern used by aggregate SQL filters (boot
        recent_mentions, stats) must agree with the regex used by the
        Python-side filter — drift would cause double-classification."""
        positive = "Compacted into archive summary 12345"
        negative_summary = "archive summary of children"
        negative_archived_to = "Archived to note:hei — economics."

        # POINTER_SQL_LIKE strips trailing % when matched against a literal claim
        prefix = POINTER_SQL_LIKE.rstrip("%")
        assert positive.startswith(prefix)
        assert is_compaction_pointer(positive)

        assert not negative_summary.startswith(prefix)
        assert not is_compaction_pointer(negative_summary)

        assert not negative_archived_to.startswith(prefix)
        assert not is_compaction_pointer(negative_archived_to)

    def test_entity_matching_friction_8211(self) -> None:
        """account:hometap-investment scenario: 13 pointers + 1 summary + live state."""
        summary = _a("archive summary for account:hometap-investment", id=8170)
        pointers = [
            _a("Compacted into archive summary 8170", id=i) for i in range(1, 14)
        ]
        live1 = _a("CURRENT STATUS: investment in underwriting", id=9000)
        live2 = _a("Monthly payment of $800 confirmed", id=9001)

        # Simulate list_assertions order (created_at DESC → newest first)
        assertions = pointers[::-1] + [live1, live2, summary]

        result, meta = apply_compaction_filter(assertions)

        assert meta is not None
        assert meta["mode"] == "pointers_deprioritized"
        assert meta["pointer_count"] == 13
        assert result[0] is summary, "Summary must surface first"
        assert result[1] in (live1, live2)
        assert result[2] in (live1, live2)
        # All 13 pointers must be at the end
        for p in pointers:
            idx = result.index(p)
            assert idx >= 3, f"Pointer {p['id']} must be after live assertions"


# ---------------------------------------------------------------------------
# SQL constant ↔ regex alignment regression guards
# ---------------------------------------------------------------------------


def _sql_like_prefix_match(claim: str, like_pattern: str) -> bool:
    """SQLite LIKE semantics for trailing-% patterns: case-insensitive prefix match."""
    prefix = like_pattern.rstrip("%").lower()
    return claim.lower().startswith(prefix)


class TestSummaryConstantAlignment:
    """SUMMARY_SQL_LIKE must stay aligned with _SUMMARY_RE across a claim corpus.

    Drift between the SQL-level filter and the Python-side regex would cause
    aggregate surfaces (FTS search, boot recent_mentions) to mis-classify rows.
    """

    _POSITIVES = [
        "archive summary of HEI children",
        "archive summary for account:hometap-investment",
        "Archive Summary: batch A",
        "ARCHIVE SUMMARY of legal proceedings",
        "archive summaryX",  # no separator — regex is prefix-only
    ]

    _NEGATIVES = [
        "Compacted into archive summary 8170",
        "Live operative state",
        "Archived to note:hei — economics.",
        "",
    ]

    def test_positives_match_both_sql_like_and_regex(self) -> None:
        for claim in self._POSITIVES:
            assert is_consolidation_summary(claim), f"regex miss on {claim!r}"
            assert _sql_like_prefix_match(claim, SUMMARY_SQL_LIKE), (
                f"SQL LIKE miss on {claim!r}"
            )

    def test_negatives_match_neither_sql_like_nor_regex(self) -> None:
        for claim in self._NEGATIVES:
            assert not is_consolidation_summary(claim), (
                f"regex false positive on {claim!r}"
            )
            assert not _sql_like_prefix_match(claim, SUMMARY_SQL_LIKE), (
                f"SQL LIKE false positive on {claim!r}"
            )

    def test_pointer_and_summary_sql_like_patterns_are_disjoint(self) -> None:
        # ∀ canonical pointer claim: does not match SUMMARY_SQL_LIKE
        # ∀ canonical summary claim: does not match POINTER_SQL_LIKE
        pointer_claims = [
            "Compacted into archive summary 8170",
            "compacted into archive summary 1",
        ]
        summary_claims = [
            "archive summary of HEI",
            "archive summary for account:foo",
        ]
        for claim in pointer_claims:
            assert not _sql_like_prefix_match(claim, SUMMARY_SQL_LIKE), (
                f"Pointer claim matched SUMMARY_SQL_LIKE: {claim!r}"
            )
        for claim in summary_claims:
            assert not _sql_like_prefix_match(claim, POINTER_SQL_LIKE), (
                f"Summary claim matched POINTER_SQL_LIKE: {claim!r}"
            )
