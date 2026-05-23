"""Unit tests for rag_resolver.normalize_evidence_uri — cortex:// branch.

Validates that cortex:// URIs delegate to entity source_uri lookup rather
than performing literal type/slug substitution (Layer 2 fix for
todo:rag-resolver-cortex-uri-delegation).
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import pytest

from cortex_store import rag_resolver


def _make_cortex_conn_patch(fake_source_uri: str | None):
    """Return context-manager patches for cortex_store.db cortex_conn + query."""
    fake_conn = object()

    @contextlib.contextmanager
    def fake_cortex_conn():
        yield fake_conn

    def fake_query(conn, sql, params):
        assert conn is fake_conn, "query called with wrong connection"
        if fake_source_uri is None:
            return []
        return [{"source_uri": fake_source_uri}]

    return fake_cortex_conn, fake_query


def test_normalize_cortex_uri_delegates_to_entity_source_uri() -> None:
    """cortex:// must resolve via entity source_uri, not literal type/slug substitution.

    ∀ cortex://agent_skill/X: result = _FILES_ROOT / entity.source_uri, not
    _FILES_ROOT / agent_skill / X (the old broken literal substitution).
    """
    fake_cortex_conn, fake_query = _make_cortex_conn_patch(
        "agent-skills/cortex-provenance-discipline.md"
    )

    with (
        patch("cortex_store.db.cortex_conn", fake_cortex_conn),
        patch("cortex_store.db.query", fake_query),
    ):
        result = rag_resolver.normalize_evidence_uri(
            "cortex://agent_skill/cortex-provenance-discipline"
        )

    expected = (
        f"{rag_resolver._FILES_ROOT}/agent-skills/cortex-provenance-discipline.md"
    )
    assert result == expected, (
        f"Expected {expected!r} via source_uri delegation, got {result!r}. "
        "Literal substitution would have produced "
        f"{rag_resolver._FILES_ROOT}/agent_skill/cortex-provenance-discipline"
    )


def test_normalize_cortex_uri_transcript_plain_relative_source_uri() -> None:
    """cortex://transcript/X with plain-relative source_uri: prepend FILES_ROOT."""
    fake_cortex_conn, fake_query = _make_cortex_conn_patch(
        "notes/system/transcripts/web-2026-05-17-1700.md"
    )

    with (
        patch("cortex_store.db.cortex_conn", fake_cortex_conn),
        patch("cortex_store.db.query", fake_query),
    ):
        result = rag_resolver.normalize_evidence_uri(
            "cortex://transcript/web-2026-05-17-1700"
        )

    expected = (
        f"{rag_resolver._FILES_ROOT}/notes/system/transcripts/web-2026-05-17-1700.md"
    )
    assert result == expected


def test_normalize_cortex_uri_transcript_files_uri_source_uri() -> None:
    """cortex://transcript/X where entity source_uri is itself a files:// URI.

    The real transcript entity has source_uri='files://notes/system/transcripts/...'
    (not a plain relative path). _source_uri_to_absolute_path must normalize the
    nested files:// URI before prepending _FILES_ROOT.
    """
    fake_cortex_conn, fake_query = _make_cortex_conn_patch(
        "files://notes/system/transcripts/web-2026-05-17-1700.md"
    )

    with (
        patch("cortex_store.db.cortex_conn", fake_cortex_conn),
        patch("cortex_store.db.query", fake_query),
    ):
        result = rag_resolver.normalize_evidence_uri(
            "cortex://transcript/web-2026-05-17-1700"
        )

    expected = (
        f"{rag_resolver._FILES_ROOT}/notes/system/transcripts/web-2026-05-17-1700.md"
    )
    assert result == expected, (
        f"Expected {expected!r} but got {result!r}. "
        "files:// source_uri must be normalized before prepending _FILES_ROOT."
    )


def test_normalize_cortex_uri_entity_not_found_raises() -> None:
    """ValueError when entity is missing from the DB."""
    fake_cortex_conn, fake_query = _make_cortex_conn_patch(None)

    with (
        patch("cortex_store.db.cortex_conn", fake_cortex_conn),
        patch("cortex_store.db.query", fake_query),
        pytest.raises(ValueError, match="entity not found"),
    ):
        rag_resolver.normalize_evidence_uri("cortex://agent_skill/no-such-skill")


def test_normalize_cortex_uri_no_source_uri_raises() -> None:
    """ValueError when entity exists but source_uri is null."""
    fake_conn = object()

    @contextlib.contextmanager
    def _conn():
        yield fake_conn

    def _query(conn, sql, params):
        return [{"source_uri": None}]

    with (
        patch("cortex_store.db.cortex_conn", _conn),
        patch("cortex_store.db.query", _query),
        pytest.raises(ValueError, match="no source_uri"),
    ):
        rag_resolver.normalize_evidence_uri("cortex://agent_skill/no-source")


def test_normalize_non_cortex_schemes_unaffected() -> None:
    """Existing schemes must not be touched by the cortex:// change."""
    assert rag_resolver.normalize_evidence_uri(
        "workspaces://universal-llm-gateway/libs/foo.py"
    ).endswith("universal-llm-gateway/libs/foo.py")

    assert (
        rag_resolver.normalize_evidence_uri("files:///mnt/data/x.md")
        == "/mnt/data/x.md"
    )

    assert (
        rag_resolver.normalize_evidence_uri("https://example.com/doc")
        == "https://example.com/doc"
    )
