"""Dispatch op registry integrity tests."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops import _OPS


@pytest.mark.offline
def test_all_op_specs_resolve_to_callable() -> None:
    for key in sorted(_OPS):
        handler = _OPS[key]
        assert callable(handler), key
