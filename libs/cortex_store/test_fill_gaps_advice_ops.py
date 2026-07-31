"""Regression tests for fill_gaps advisory op names."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops import _OP_SPECS
from cortex_store.dispatch_ops.ops_review import _GAP_FILL_ADVICE


@pytest.mark.offline
def test_gap_fill_advice_ops_are_registered_or_fs() -> None:
    allowed = set(_OP_SPECS) | {"fs"}
    bad = [
        (kind, advice["op"])
        for kind, advice in _GAP_FILL_ADVICE.items()
        if advice["op"] not in allowed
    ]
    assert bad == []
