from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_query_coverage_bias_under_test",
    Path(__file__).resolve().parent / "query_coverage_bias.py",
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
apply_query_coverage_bias = _mod.apply_query_coverage_bias


@dataclass(slots=True)
class _Chunk:
    content: str
    source: str
    indexed_at: str
    metadata: dict[str, object] = field(default_factory=dict)
    content_hash: str = ""
    score: float = 0.0


def test_enumeration_query_boosts_distinct_sections_from_anchor_source() -> None:
    chunks = [
        _Chunk(
            content="limit",
            source="order-types.html",
            indexed_at="ts",
            metadata={"section_path": "Limit Orders"},
            content_hash="a",
        ),
        _Chunk(
            content="stop loss",
            source="order-types.html",
            indexed_at="ts",
            metadata={"section_path": "Stop-loss and Take-profit Orders"},
            content_hash="b",
        ),
        _Chunk(
            content="fees",
            source="fees.html",
            indexed_at="ts",
            metadata={"section_path": "Fees"},
            content_hash="c",
        ),
    ]
    scores = {"a": 10.0, "b": 9.0, "c": 8.5}

    result = apply_query_coverage_bias(
        chunks,
        scores,
        query="What order types and execution options does the API support?",
        enabled=True,
        anchor_min_score_share=0.40,
        anchor_boost=1.5,
        section_cap=4,
    )

    assert result.applied is True
    assert result.anchor_source == "order-types.html"
    assert result.boosted_chunks == 2
    assert result.scores["b"] > scores["b"]
