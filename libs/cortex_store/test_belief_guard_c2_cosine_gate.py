"""C2 blocks on embedding cosine, never an FTS rank ratio.

A confirmed polarity clash blocks only when cosine is at least 0.80.
A lower cosine flags and stays allowed. Embeddings-unconfigured polarity
hits flag with ``retrieval_source=fts`` and ``c2_degraded=lexical``.
``thread:*`` anchors still skip the guard. The assert nudge's hybrid
recall is reused so the guard does not search a second time.
"""

from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from .belief_guard import (
    analyze_assertion_impact,
    clear_staged_candidate_recall,
    guard_assertion_write,
    stage_candidate_recall,
    take_staged_candidate_recall,
)
from .dispatch_ops.ops_assertions_write import _op_assert

_ENTITY = "decision:audit-posture"
_EXISTING = "Citation audit is complete"
_INCOMING = "Citation audit is incomplete"

_SCHEMA = """
CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT, name TEXT);
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT, claim TEXT, confidence TEXT, superseded_by INTEGER,
    predicate_form TEXT
);
CREATE VIRTUAL TABLE assertions_fts USING fts5(
    assertion_id UNINDEXED, indexed_text
);
"""


@pytest.fixture(autouse=True)
def _reset_staged_recall():
    clear_staged_candidate_recall()
    yield
    clear_staged_candidate_recall()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _fts_conn() -> sqlite3.Connection:
    conn = _conn()
    conn.executescript(_SCHEMA)
    return conn


def _seed_confirmed(conn: sqlite3.Connection, entity_id: str, claim: str) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence) "
        "VALUES (?, ?, 'confirmed')",
        (entity_id, claim),
    )
    aid = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, indexed_text) VALUES (?, ?)",
        (aid, claim),
    )
    conn.commit()
    return aid


def _fts_row(
    assertion_id: int,
    *,
    rank: float = -12.4,
    claim: str = _EXISTING,
    confidence: str = "confirmed",
) -> dict:
    return {
        "id": assertion_id,
        "claim": claim,
        "confidence": confidence,
        "entity_id": _ENTITY,
        "predicate_form": None,
        "rank": rank,
    }


@patch("cortex_store.belief_guard._entity_vector_search")
@patch("cortex_store.belief_guard._entity_fts_search")
def test_polarity_confirmed_cosine_below_threshold_flags_not_409(
    mock_fts, mock_vector
) -> None:
    """Cosine 0.79 would be rank-ratio 1.0 on a singleton FTS hit. Flag only."""
    mock_fts.return_value = [_fts_row(7, rank=-0.2)]
    mock_vector.return_value = [
        {"assertion_id": 7, "cosine_similarity": 0.79},
    ]

    result = guard_assertion_write(_conn(), _ENTITY, _INCOMING)

    assert result.allowed is True
    assert result.review_status == "flagged"
    assert result.block_detail is None
    assert result.contradiction_warnings is not None
    warning = result.contradiction_warnings[0]
    assert warning.similarity == 0.79
    assert warning.similarity != 1.0
    assert mock_fts.call_count == 1


@patch("cortex_store.belief_guard._entity_vector_search")
@patch("cortex_store.belief_guard._entity_fts_search")
def test_polarity_confirmed_cosine_at_threshold_blocks(mock_fts, mock_vector) -> None:
    mock_fts.return_value = [_fts_row(7, rank=-0.01)]
    mock_vector.return_value = [
        {"assertion_id": 7, "cosine_similarity": 0.80},
    ]

    result = guard_assertion_write(_conn(), _ENTITY, _INCOMING)

    assert result.allowed is False
    assert result.block_detail is not None
    assert result.block_detail["error"] == "contradiction_detected"
    assert result.block_detail["conflicts"][0]["similarity"] == 0.8
    assert mock_fts.call_count == 1


@patch(
    "cortex_store.belief_guard.cortex_embeddings.embed_query",
    side_effect=AssertionError("embedding HTTP must not run"),
)
@patch(
    "cortex_store.belief_guard.vector_store.is_initialized",
    return_value=False,
)
@patch(
    "cortex_store.belief_guard.cortex_embeddings.is_configured",
    return_value=False,
)
def test_embeddings_unconfigured_flags_lexical_never_409(
    _mock_configured, _mock_initialized, mock_embed, caplog: pytest.LogCaptureFixture
) -> None:
    conn = _fts_conn()
    _seed_confirmed(conn, _ENTITY, _EXISTING)

    with caplog.at_level(logging.WARNING, logger="cortex-api.belief-guard"):
        result = guard_assertion_write(conn, _ENTITY, _INCOMING)

    assert result.allowed is True
    assert result.review_status == "flagged"
    assert result.block_detail is None
    assert result.contradiction_warnings is not None
    warning = result.contradiction_warnings[0]
    assert warning.retrieval_source == "fts"
    assert warning.similarity == 0.0
    assert any("c2_degraded=lexical" in rec.message for rec in caplog.records)
    mock_embed.assert_not_called()
    conn.close()


@patch("cortex_store.belief_guard._entity_hybrid_search")
def test_thread_entity_bypasses_without_hybrid_search(mock_search) -> None:
    result = guard_assertion_write(_conn(), "thread:dispatch:cursor-replay", _INCOMING)

    assert result.allowed is True
    assert result.block_detail is None
    assert result.review_status is None
    mock_search.assert_not_called()


@patch("cortex_store.belief_guard._entity_vector_search")
@patch("cortex_store.belief_guard._entity_fts_search")
def test_guard_reuses_nudge_recall_without_second_search(mock_fts, mock_vector) -> None:
    mock_fts.return_value = [_fts_row(7, rank=-20.0)]
    mock_vector.return_value = [
        {"assertion_id": 7, "cosine_similarity": 0.91},
    ]

    analyze_assertion_impact(_conn(), _ENTITY, _INCOMING, "confirmed")
    assert mock_fts.call_count == 1
    recall = take_staged_candidate_recall(_ENTITY, _INCOMING)
    assert recall is not None
    mock_fts.reset_mock()
    mock_vector.reset_mock()
    stage_candidate_recall(_ENTITY, _INCOMING, recall)

    result = guard_assertion_write(_conn(), _ENTITY, _INCOMING)

    mock_fts.assert_not_called()
    mock_vector.assert_not_called()
    assert result.allowed is False
    assert result.block_detail is not None


def test_op_assert_passes_nudge_recall_to_create() -> None:
    """MCP assert threads the nudge search into create; create is not a second search."""
    from .belief_guard import CandidateRecall

    recall = CandidateRecall(scored=[], embeddings_unavailable=False)

    def _nudge(conn, entity_id, claim, confidence, predicate_form=None):
        stage_candidate_recall(entity_id, claim, recall)
        return None

    cm = MagicMock()
    cm.__enter__.return_value = MagicMock()
    cm.__exit__.return_value = False

    with (
        patch(
            "cortex_store.dispatch_ops.ops_assertions_write.cortex_conn",
            return_value=cm,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_assertions_write.resolve_entity_reference",
            return_value=SimpleNamespace(entity_id=_ENTITY),
        ),
        patch(
            "cortex_store.dispatch_ops.ops_assertions_write.build_assert_nudge",
            side_effect=_nudge,
        ),
        patch(
            "cortex_store.dispatch_ops.ops_assertions_write._create_assertion_impl",
            return_value={"was_new": True, "item": {"id": 1}},
        ) as mock_create,
        patch("cortex_store.dispatch_ops.ops_assertions_write.record"),
    ):
        result = _op_assert(
            entity_id=_ENTITY,
            claim=_INCOMING,
            confidence="confirmed",
            evidence="unit",
        )

    assert "error" not in result
    assert mock_create.call_args.kwargs["candidate_recall"] is recall
