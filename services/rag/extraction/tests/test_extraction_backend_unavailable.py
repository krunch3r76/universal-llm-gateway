"""Failed-poll backend-unavailable classification (Slice 3)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag.extraction import chroma_source
from services.rag.property_index import PropertyIndex


def _config() -> MagicMock:
    cfg = MagicMock()
    cfg.schema_version = 1
    cfg.extraction_model = "test-model"
    return cfg


def _rag_config() -> MagicMock:
    rag = MagicMock()
    rag.get_scope_for_path.return_value = "default"
    return rag


async def _property_index(tmp_path: Path) -> PropertyIndex:
    idx = PropertyIndex(db_path=tmp_path / "rag_metadata.db")
    await idx.start()
    return idx


async def _run_chroma(fn: Any, *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    if asyncio.iscoroutine(result):
        return await result
    return result


def _chroma_payload(ids: list[str]) -> dict[str, Any]:
    return {
        "ids": ids,
        "documents": [f"doc-{i}" for i in range(len(ids))],
        "metadatas": [{} for _ in ids],
    }


@pytest.mark.asyncio
async def test_failed_poll_all_503_does_not_burn_attempt(tmp_path: Path) -> None:
    """Map failures that are only backend-unavailable hold the retry budget."""
    ids = ["c0", "c1"]
    collection = MagicMock()
    property_index = await _property_index(tmp_path)
    try:
        fail_timing = {
            "pipeline_failed": True,
            "error_code": "MapPartialFailureError",
            "error_data": {
                "iteration_results": [
                    {
                        "index": 0,
                        "status": "failed",
                        "error_message": "HTTP 503 Service Unavailable",
                    },
                    {
                        "index": 1,
                        "status": "failed",
                        "error_message": "model_unavailable: no slot",
                    },
                ]
            },
        }

        with (
            patch.object(
                chroma_source,
                "run_chroma",
                AsyncMock(side_effect=_run_chroma),
            ),
            patch.object(
                chroma_source,
                "submit_extraction_pipeline",
                AsyncMock(return_value="exec-1"),
            ),
            patch.object(
                chroma_source,
                "poll_extraction_result",
                AsyncMock(return_value=([None, None], fail_timing)),
            ),
            patch.object(
                chroma_source, "chroma_get_where", return_value=_chroma_payload(ids)
            ),
            patch.object(
                chroma_source, "chroma_update_metadatas", new_callable=AsyncMock
            ),
        ):
            all_done, increment_attempt, category, *_ = await chroma_source.extract_source(
                "/s.json",
                collection=collection,
                config=_config(),
                rag_config=_rag_config(),
                property_index=property_index,
            )

        assert all_done is False
        assert increment_attempt is False
        assert category == "pipeline_output"
    finally:
        await property_index.stop()


@pytest.mark.asyncio
async def test_failed_poll_503_mixed_with_oversize_skips_without_burn(
    tmp_path: Path,
) -> None:
    ids = ["c0", "c1"]
    collection = MagicMock()
    property_index = await _property_index(tmp_path)
    try:
        fail_timing = {
            "pipeline_failed": True,
            "error_code": "MapPartialFailureError",
            "error_data": {
                "iteration_results": [
                    {
                        "index": 0,
                        "status": "failed",
                        "error_message": "Prompt too large for model m",
                    },
                    {
                        "index": 1,
                        "status": "failed",
                        "error_message": "no_feasible_gateway for model m",
                    },
                ]
            },
        }

        with (
            patch.object(
                chroma_source,
                "run_chroma",
                AsyncMock(side_effect=_run_chroma),
            ),
            patch.object(
                chroma_source,
                "submit_extraction_pipeline",
                AsyncMock(return_value="exec-1"),
            ),
            patch.object(
                chroma_source,
                "poll_extraction_result",
                AsyncMock(return_value=([None, None], fail_timing)),
            ),
            patch.object(
                chroma_source, "chroma_get_where", return_value=_chroma_payload(ids)
            ),
            patch.object(
                chroma_source, "chroma_update_metadatas", new_callable=AsyncMock
            ) as update_mock,
        ):
            all_done, increment_attempt, *_ = await chroma_source.extract_source(
                "/s.json",
                collection=collection,
                config=_config(),
                rag_config=_rag_config(),
                property_index=property_index,
            )

        assert all_done is False
        assert increment_attempt is False
        update_mock.assert_awaited()
    finally:
        await property_index.stop()
