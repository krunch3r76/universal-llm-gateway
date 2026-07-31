"""HTTP status classification for indexing failures (rag-initial-reindex-504-retry)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.rag.config import RagConfig
from services.rag.indexing_failure_classifier import (
    classify_http_status_error as _classify_http_status_error,
)
from services.rag.indexing_failure_classifier import (
    classify_indexing_failure as _classify_indexing_failure,
)
from services.rag.rag_service import startup_cleanup, state


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "http://test")
    resp = httpx.Response(status_code=status, request=req)
    return httpx.HTTPStatusError("upstream failure", request=req, response=resp)


def test_http_504_transient() -> None:
    assert _classify_http_status_error(_http_error(504)) == (
        "transient",
        "http_504",
    )


def test_http_503_transient() -> None:
    assert _classify_http_status_error(_http_error(503)) == (
        "transient",
        "http_503",
    )


def test_http_500_transient_generic_5xx() -> None:
    assert _classify_http_status_error(_http_error(500)) == (
        "transient",
        "http_5xx",
    )


def test_http_429_transient() -> None:
    assert _classify_http_status_error(_http_error(429)) == (
        "transient",
        "http_429",
    )


def test_http_404_permanent_client_error() -> None:
    assert _classify_http_status_error(_http_error(404)) == (
        "permanent",
        "http_client_error",
    )


def test_http_404_not_unclassified_via_indexing_classifier() -> None:
    category, reason = _classify_indexing_failure(_http_error(404), chunk_count=0)
    assert category == "permanent"
    assert reason == "http_client_error"
    assert (category, reason) != ("transient", "unclassified")


def test_timeout_error_branch_isolation() -> None:
    assert _classify_indexing_failure(TimeoutError("slow"), chunk_count=0) == (
        "transient",
        "timeout",
    )


def test_value_error_branch_isolation() -> None:
    assert _classify_indexing_failure(ValueError("bad"), chunk_count=0) == (
        "transient",
        "unclassified",
    )


@pytest.mark.asyncio
async def test_reconcile_5xx_counts_transient(tmp_path: Path) -> None:
    src = tmp_path / "doc.md"
    src.write_text("x", encoding="utf-8")
    prop_index = MagicMock()
    prop_index.get_pending_files.return_value = [str(src)]
    prop_index.clear_pending = AsyncMock()

    state._property_index = prop_index
    state._event_bus = None

    with patch(
        "services.rag.rag_service.startup_cleanup.indexing._index_file",
        new=AsyncMock(side_effect=_http_error(504)),
    ):
        await startup_cleanup._reconcile_pending(
            RagConfig(watch_directories=[], scopes={})
        )

    prop_index.clear_pending.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_4xx_counts_permanent_and_clears_pending(
    tmp_path: Path,
) -> None:
    src = tmp_path / "doc.md"
    src.write_text("x", encoding="utf-8")
    prop_index = MagicMock()
    prop_index.get_pending_files.return_value = [str(src)]
    prop_index.clear_pending = AsyncMock()

    state._property_index = prop_index
    state._event_bus = None

    with patch(
        "services.rag.rag_service.startup_cleanup.indexing._index_file",
        new=AsyncMock(side_effect=_http_error(404)),
    ):
        await startup_cleanup._reconcile_pending(
            RagConfig(watch_directories=[], scopes={})
        )

    prop_index.clear_pending.assert_awaited_once_with(str(src))
