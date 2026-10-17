"""Acceptance tests for Prompt-too-large extraction skip policy (Slice 1)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag.extraction import chroma_source
from services.rag.knowledge_extractor import ExtractedKnowledge
from services.rag.property_index import PropertyIndex


def _knowledge(chunk_id: str) -> ExtractedKnowledge:
    return ExtractedKnowledge(entities=[], topics=["x"], chunk_id=chunk_id)


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
async def test_ac1_completed_partial_marks_oversize_skipped(tmp_path: Path) -> None:
    """Two successes, one Prompt too large null with hint — skip without attempt burn."""
    ids = ["c0", "c1", "c2"]
    collection = MagicMock()
    property_index = await _property_index(tmp_path)
    try:
        parsed = [_knowledge("c0"), None, _knowledge("c2")]
        timing = {
            "execution_id": "e1",
            "result_hints": [
                {
                    "index": 1,
                    "error_message": "Prompt too large for model m: ~9000 tokens",
                }
            ],
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
                AsyncMock(return_value=(parsed, timing)),
            ),
            patch.object(
                chroma_source, "chroma_get_where", return_value=_chroma_payload(ids)
            ),
            patch.object(
                chroma_source, "chroma_update_metadatas", new_callable=AsyncMock
            ) as update_mock,
            patch.object(
                chroma_source,
                "build_property_entries",
                return_value=[],
            ),
        ):
            collection.get.return_value = _chroma_payload(ids)
            all_done, increment_attempt, *_rest = await chroma_source.extract_source(
                "/s.json",
                collection=collection,
                config=_config(),
                rag_config=_rag_config(),
                property_index=property_index,
            )

        assert all_done is True
        assert increment_attempt is False
        update_calls = update_mock.await_args_list
        skipped_meta = next(
            m for call in update_calls for m in call.args[2] if m.get("extraction_skipped")
        )
        assert skipped_meta["extraction_skipped"] is True
        assert update_mock.await_count == 2
    finally:
        await property_index.stop()


@pytest.mark.asyncio
async def test_ac2_failed_all_oversize_resubmits_completed_once(tmp_path: Path) -> None:
    ids = ["c0", "c1", "c2"]
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
                        "error_message": "Prompt too large for model m",
                    },
                    {"index": 2, "status": "completed"},
                ]
            },
        }
        success_timing = {
            "execution_id": "e2",
            "result_hints": [],
        }

        submit_mock = AsyncMock(side_effect=["exec-1", "exec-2"])
        poll_mock = AsyncMock(
            side_effect=[
                ([None, None, None], fail_timing),
                ([_knowledge("c2")], success_timing),
            ]
        )

        with (
            patch.object(
                chroma_source,
                "run_chroma",
                AsyncMock(side_effect=_run_chroma),
            ),
            patch.object(chroma_source, "submit_extraction_pipeline", submit_mock),
            patch.object(chroma_source, "poll_extraction_result", poll_mock),
            patch.object(
                chroma_source, "chroma_get_where", return_value=_chroma_payload(ids)
            ),
            patch.object(
                chroma_source, "chroma_update_metadatas", new_callable=AsyncMock
            ) as update_mock,
            patch.object(chroma_source, "build_property_entries", return_value=[]),
        ):
            all_done, increment_attempt, *_ = await chroma_source.extract_source(
                "/s.json",
                collection=collection,
                config=_config(),
                rag_config=_rag_config(),
                property_index=property_index,
            )

        assert submit_mock.await_count == 2
        assert submit_mock.await_args_list[1].args[0] == ["c2"]
        assert all_done is True
        assert increment_attempt is False
        assert update_mock.await_count >= 2
    finally:
        await property_index.stop()


@pytest.mark.asyncio
async def test_ac3_mixed_failure_no_resubmit(tmp_path: Path) -> None:
    ids = ["c0", "c1", "c2"]
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
                        "status": "timeout",
                        "error_message": "TIMEOUT after 900.0s (request pending)",
                    },
                    {"index": 2, "status": "completed"},
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
                AsyncMock(return_value=([None, None, None], fail_timing)),
            ),
            patch.object(
                chroma_source, "chroma_get_where", return_value=_chroma_payload(ids)
            ),
            patch.object(
                chroma_source, "chroma_update_metadatas", new_callable=AsyncMock
            ),
            patch.object(
                chroma_source,
                "submit_extraction_pipeline",
                AsyncMock(return_value="exec-1"),
            ) as submit_mock,
        ):
            all_done, increment_attempt, *_ = await chroma_source.extract_source(
                "/s.json",
                collection=collection,
                config=_config(),
                rag_config=_rag_config(),
                property_index=property_index,
            )

        submit_mock.assert_awaited_once()
        assert all_done is False
        assert increment_attempt is True
    finally:
        await property_index.stop()


@pytest.mark.asyncio
async def test_ac4_message_only_single_oversize(tmp_path: Path) -> None:
    ids = ["c0"]
    collection = MagicMock()
    property_index = await _property_index(tmp_path)
    try:
        fail_timing = {
            "pipeline_failed": True,
            "error_code": "MapPartialFailureError",
            "error_message": "0/1 chunks failed\nPrompt too large for model",
            "error_data": None,
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
                AsyncMock(return_value=([None], fail_timing)),
            ),
            patch.object(
                chroma_source, "chroma_get_where", return_value=_chroma_payload(ids)
            ),
            patch.object(
                chroma_source, "chroma_update_metadatas", new_callable=AsyncMock
            ),
        ):
            all_done, increment_attempt, *_ = await chroma_source.extract_source(
                "/s.json",
                collection=collection,
                config=_config(),
                rag_config=_rag_config(),
                property_index=property_index,
            )

        assert all_done is True
        assert increment_attempt is False
    finally:
        await property_index.stop()


@pytest.mark.asyncio
async def test_ac5_message_partial_oversize(tmp_path: Path) -> None:
    ids = ["c0", "c1"]
    collection = MagicMock()
    property_index = await _property_index(tmp_path)
    try:
        fail_timing = {
            "pipeline_failed": True,
            "error_code": "MapPartialFailureError",
            "error_message": (
                "0/2 below threshold\n"
                "Iteration 0 (m → g): FAILED - Prompt too large for model\n"
                "Iteration 1 (m → g): TIMEOUT after 900.0s (request pending)"
            ),
            "error_data": None,
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
            all_done, increment_attempt, *_ = await chroma_source.extract_source(
                "/s.json",
                collection=collection,
                config=_config(),
                rag_config=_rag_config(),
                property_index=property_index,
            )

        assert all_done is False
        assert increment_attempt is True
    finally:
        await property_index.stop()


@pytest.mark.asyncio
async def test_ac6_skip_write_failure(tmp_path: Path) -> None:
    ids = ["c0"]
    collection = MagicMock()
    property_index = await _property_index(tmp_path)
    try:
        timing = {
            "execution_id": "e1",
            "result_hints": [
                {"index": 0, "error_message": "Prompt too large for model m"}
            ],
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
                AsyncMock(return_value=([None], timing)),
            ),
            patch.object(
                chroma_source, "chroma_get_where", return_value=_chroma_payload(ids)
            ),
            patch.object(
                chroma_source,
                "chroma_update_metadatas",
                AsyncMock(side_effect=RuntimeError("disk full")),
            ),
        ):
            all_done, increment_attempt, *_ = await chroma_source.extract_source(
                "/s.json",
                collection=collection,
                config=_config(),
                rag_config=_rag_config(),
                property_index=property_index,
            )

        assert all_done is False
        assert increment_attempt is True
    finally:
        await property_index.stop()
