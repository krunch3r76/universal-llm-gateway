"""Stargate calls from the pipeline tool carry the bound surface."""

from __future__ import annotations

import pytest
from request_profile import bind_request
from tools.pipeline import _stargate_headers


@pytest.mark.offline
def test_surface_life_header() -> None:
    with bind_request("default", surface="life"):
        assert _stargate_headers() == {"X-ULG-Surface": "life"}
    assert _stargate_headers() == {}
