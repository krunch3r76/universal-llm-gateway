from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_repo_root = str(Path(__file__).resolve().parents[2])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from services.rag.admin_routes._helpers import (  # noqa: E402
    _article_status_row,
    _build_source_status_item,
    _collect_source_status_items,
)


def test_article_status_row_omits_abstract() -> None:
    row = _article_status_row(
        {
            "source_path": "/corpus/paper.pdf",
            "filename": "paper.pdf",
            "title": "Example",
            "abstract": "should not surface",
            "scope": "research",
            "content_hash": "abc",
            "subdirectory": "papers",
        }
    )
    assert row is not None
    assert row.title == "Example"
    assert not hasattr(row, "abstract")


def test_build_source_status_item_includes_queue_state_and_article() -> None:
    prop_idx = MagicMock()
    prop_idx.get_source_item_data.return_value = {
        "is_indexed": True,
        "indexed_at": "2026-07-12T10:00:00+00:00",
        "queue_row": {
            "state": "in_flight",
            "attempts": 2,
            "last_error": None,
            "position": 3,
        },
        "contextualized_chunks": 0,
    }
    prop_idx.get_source_pipeline_state.return_value = {
        "is_indexed": True,
        "queue_row": {"state": "in_flight"},
        "contextualized_chunks": 0,
    }
    prop_idx.get_extraction_queue_count.return_value = 7
    prop_idx.get_article_row.return_value = {
        "source_path": "/corpus/paper.pdf",
        "filename": "paper.pdf",
        "title": "Example",
        "authors": "",
        "venue": "",
        "published_date": "",
        "doi": "",
        "abstract": "",
        "scope": "research",
        "content_hash": "abc",
        "subdirectory": "papers",
        "comments": "",
    }

    item = _build_source_status_item("/corpus/paper.pdf", prop_idx)

    assert item.pipeline_stage == "queued"
    assert item.queue_state == "in_flight"
    assert item.queue_attempts == 2
    assert item.article is not None
    assert item.article.title == "Example"
    assert item.file_exists is False


@pytest.mark.offline
def test_unresolved_arxiv_id_returns_registered_item() -> None:
    prop_idx = MagicMock()
    prop_idx.resolve_source_paths.return_value = []

    items = _collect_source_status_items(
        prop_idx, sources=None, arxiv_ids=["2408.09121"], filenames=None
    )

    assert len(items) == 1
    assert items[0].source_path == "2408.09121"
    assert items[0].pipeline_stage == "registered"
    assert items[0].file_exists is False
    assert items[0].article is None
    assert items[0].contextualized_chunks == 0


@pytest.mark.offline
def test_unresolved_filename_returns_registered_item() -> None:
    prop_idx = MagicMock()
    prop_idx.resolve_source_paths.return_value = []

    items = _collect_source_status_items(
        prop_idx,
        sources=None,
        arxiv_ids=None,
        filenames=["arxiv-2408.09121.pdf"],
    )

    assert len(items) == 1
    assert items[0].source_path == "arxiv-2408.09121.pdf"
    assert items[0].pipeline_stage == "registered"
    assert items[0].file_exists is False
    assert items[0].article is None
    assert items[0].contextualized_chunks == 0


@pytest.mark.offline
def test_arxiv_id_resolves_via_indexed_sources_only() -> None:
    indexed_path = "/corpus/research/arxiv-2408.09121.pdf"
    prop_idx = MagicMock()
    prop_idx.resolve_source_paths.return_value = [indexed_path]
    prop_idx.get_source_item_data.return_value = {
        "is_indexed": True,
        "indexed_at": "2026-07-12T10:00:00+00:00",
        "queue_row": None,
        "contextualized_chunks": 0,
    }
    prop_idx.get_source_pipeline_state.return_value = {
        "is_indexed": True,
        "queue_row": None,
        "contextualized_chunks": 0,
    }
    prop_idx.get_extraction_queue_count.return_value = 0
    prop_idx.get_article_row.return_value = None

    items = _collect_source_status_items(
        prop_idx, sources=None, arxiv_ids=["2408.09121"], filenames=None
    )

    prop_idx.resolve_source_paths.assert_called_once_with(arxiv_ids=["2408.09121"])
    assert len(items) == 1
    assert items[0].source_path == indexed_path
    assert items[0].pipeline_stage == "chunked"
