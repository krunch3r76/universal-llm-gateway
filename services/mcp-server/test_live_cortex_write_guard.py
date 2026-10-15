"""Self-test for the mcp-server live cortex write guard."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import live_cortex_write_guard as guard


def test_live_roots_are_a_union(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    roots = guard.live_cortex_files_roots()
    assert tmp_path.resolve() in roots
    assert (Path.home() / "mcp-data" / "files").resolve() in roots
    container = Path("/data/files")
    if container.is_dir():
        assert container.resolve() in roots


def test_guard_trips_on_live_root_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    live = tmp_path / "files"
    live.mkdir()
    monkeypatch.setattr(guard, "_LIVE_CORTEX_FILES_ROOTS", (live.resolve(),))
    target = live / "x"
    with pytest.raises(AssertionError, match="test write under live cortex root"):
        open(target, "w", encoding="utf-8").close()
    with pytest.raises(AssertionError, match="test replace under live cortex root"):
        os.replace(target, live / "y")
