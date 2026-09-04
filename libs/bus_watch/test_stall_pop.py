"""Tests for stall_pop line format (Phase A)."""

from __future__ import annotations

import io

from bus_watch.stall_pop import emit_stall_pop


def test_emit_stall_pop_exact_format() -> None:
    buf = io.StringIO()
    emit_stall_pop("park_harvest_stall", stream=buf)
    assert buf.getvalue() == "stall-pop: park_harvest_stall\n"
