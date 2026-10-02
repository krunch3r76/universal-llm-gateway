"""empty_reason classifier + retrieval metadata passthrough (a:37162)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

from systems.pipeline.core.executor.output_resolution import extract_retrieval_metadata
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.pipeline.core.step_config import StepConfig

_PARENT = Path(__file__).resolve().parent / "handlers"


def _load_empty_reason():
    spec = importlib.util.spec_from_file_location(
        "_empty_reason_under_test", _PARENT / "empty_reason.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_empty_reason_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_er = _load_empty_reason()


def test_index_miss() -> None:
    assert (
        _er.classify_accepted_scope_empty_reason(
            chunks_after_merge=0,
            junk_wiped_all=False,
            total_raw=0,
            queries_succeeded=3,
            queries_attempted=3,
        )
        == "index_miss"
    )


def test_index_miss_partial() -> None:
    assert (
        _er.classify_accepted_scope_empty_reason(
            chunks_after_merge=0,
            junk_wiped_all=False,
            total_raw=0,
            queries_succeeded=2,
            queries_attempted=3,
        )
        == "index_miss_partial"
    )


def test_junk_filtered() -> None:
    assert (
        _er.classify_accepted_scope_empty_reason(
            chunks_after_merge=0,
            junk_wiped_all=True,
            total_raw=5,
            queries_succeeded=2,
            queries_attempted=2,
        )
        == "junk_filtered"
    )


def test_filtered_after_cap() -> None:
    assert (
        _er.classify_accepted_scope_empty_reason(
            chunks_after_merge=0,
            junk_wiped_all=False,
            total_raw=4,
            queries_succeeded=2,
            queries_attempted=2,
        )
        == "filtered"
    )


def test_non_empty_has_no_reason() -> None:
    assert (
        _er.classify_accepted_scope_empty_reason(
            chunks_after_merge=3,
            junk_wiped_all=False,
            total_raw=3,
            queries_succeeded=1,
            queries_attempted=1,
        )
        is None
    )


def test_all_failed_path_classifier_shape() -> None:
    """Handler early-returns retrieval_unavailable before calling the classifier."""
    assert (
        _er.classify_accepted_scope_empty_reason(
            chunks_after_merge=0,
            junk_wiped_all=False,
            total_raw=0,
            queries_succeeded=0,
            queries_attempted=3,
        )
        == "index_miss_partial"
    )


def test_extract_retrieval_metadata_passes_empty_reason() -> None:
    context = MagicMock()
    context.get_output = lambda step_id: (
        StepOutput(
            raw="No relevant documents found in the knowledge base.",
            json={
                "chunks_found": 0,
                "scope": "llm_prompting",
                "scope_rejected": False,
                "scope_source": "user_override",
                "empty_reason": "retrieval_skipped",
                "effective_params": {"scope_key": "llm_prompting"},
            },
        )
        if step_id == "retrieve"
        else None
    )
    steps = [
        StepConfig(id="retrieve", type="rag_multi_retrieve_v1", depends_on=[]),
    ]
    meta = extract_retrieval_metadata(context, steps)
    assert meta is not None
    assert meta["empty_reason"] == "retrieval_skipped"
    assert meta["chunks_found"] == 0
