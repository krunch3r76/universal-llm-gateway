"""Terminal load wait must match hybrid/full model ids by routing_key."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402

from systems.federation.master.orchestration.load_terminal_wait import (  # noqa: E402
    _model_matches,
)


@pytest.mark.parametrize(
    ("event_model_id", "expected"),
    [
        (
            "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid",
            "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768",
        ),
        (
            "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768",
            ModelId.parse(
                "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
            ),
        ),
    ],
)
def test_model_matches_hybrid_vs_routing_key(
    event_model_id: str, expected: str | ModelId
) -> None:
    assert _model_matches({"model_id": event_model_id}, expected) is True


def test_model_matches_rejects_unrelated_routing_key() -> None:
    assert (
        _model_matches(
            {"model_id": "qwen3-14b-q4-k-m-40960"},
            "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768",
        )
        is False
    )
