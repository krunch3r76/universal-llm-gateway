"""Unit tests for dispatch example value normalization (llr-fix-c)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from tools.agent_bus.fetch import _fetch_dispatch, _get_dispatch


def test_normalize_production_fetch_dispatch_coerces_last_string() -> None:
    from _dispatch_example_normalize import normalize_dispatch_example_values

    assert normalize_dispatch_example_values(_fetch_dispatch, {"last": "1"}) == {
        "last": 1
    }


def test_normalize_production_get_dispatch_coerces_turn_numbers() -> None:
    from _dispatch_example_normalize import normalize_dispatch_example_values

    assert normalize_dispatch_example_values(
        _get_dispatch, {"turn_numbers": ["1", "2"]}
    ) == {"turn_numbers": [1, 2]}