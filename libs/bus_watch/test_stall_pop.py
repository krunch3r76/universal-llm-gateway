"""Tests for stall_pop line format (Phase A)."""

from __future__ import annotations

import io
import re
from pathlib import Path

from bus_watch.stall_pop import emit_stall_pop, should_emit_stall_pop


def test_emit_stall_pop_exact_format() -> None:
    buf = io.StringIO()
    emit_stall_pop("park_harvest_stall", stream=buf)
    assert buf.getvalue() == "stall-pop: park_harvest_stall\n"


def test_should_emit_stall_pop_debounces_per_episode() -> None:
    """A-4: ≤1 stall-pop line per stall episode; reset when stall clears."""
    last: str | None = None
    emit, last = should_emit_stall_pop(
        last_reason=last, reason="park_harvest_stall", stall_active=True
    )
    assert emit
    assert last == "park_harvest_stall"

    emit, last = should_emit_stall_pop(
        last_reason=last, reason="park_harvest_stall", stall_active=True
    )
    assert not emit
    assert last == "park_harvest_stall"

    _, last = should_emit_stall_pop(
        last_reason=last, reason="park_harvest_stall", stall_active=False
    )
    assert last is None

    emit, last = should_emit_stall_pop(
        last_reason=last, reason="park_harvest_stall", stall_active=True
    )
    assert emit
    assert last == "park_harvest_stall"


def test_bus_watch_no_services_imports() -> None:
    """A-7 / W4: libs/bus_watch must not import services.*."""
    root = Path(__file__).resolve().parent
    pattern = re.compile(r"(?:^|\s)(?:from|import)\s+services[\.\s]")
    violations: list[str] = []
    for path in sorted(root.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            violations.append(path.name)
    assert violations == []
