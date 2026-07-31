"""Golden parity for neutral model-card projection across substrates."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from cursor_capabilities import CURSOR_MODEL_CAPABILITIES, to_model_card_dict
from llm_adapters.capability_dispatch.registry import resolve
from llm_adapters.capability_dispatch.serialization import (
    to_model_card_dict as cloud_to_model_card_dict,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NEUTRAL_KEYS = frozenset({"knobs", "fixed_params"})


def _assert_neutral_shape(card: dict[str, object]) -> None:
    assert NEUTRAL_KEYS.issubset(card.keys())
    assert isinstance(card["knobs"], dict)
    assert isinstance(card["fixed_params"], dict)
    for knob in card["knobs"].values():
        assert isinstance(knob, dict)
        assert "accepted" in knob


def test_cloud_and_cursor_model_cards_share_neutral_shape() -> None:
    cloud_card = cloud_to_model_card_dict(resolve("anthropic/claude-opus-4-8"))
    cursor_card = to_model_card_dict(CURSOR_MODEL_CAPABILITIES["claude-opus-4-8"])

    _assert_neutral_shape(cloud_card)
    _assert_neutral_shape(cursor_card)
    assert cloud_card["api_surface"]
    assert "api_surface" not in cursor_card


@pytest.mark.parametrize(
    ("pattern", "path"),
    [
        ("capability_dispatch", REPO_ROOT / "libs" / "cursor_capabilities"),
        (
            "cursor_capabilities",
            REPO_ROOT / "libs" / "llm_adapters" / "capability_dispatch",
        ),
    ],
)
def test_zero_cross_imports_between_capability_packages(
    pattern: str, path: Path
) -> None:
    result = subprocess.run(
        ["rg", pattern, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout
