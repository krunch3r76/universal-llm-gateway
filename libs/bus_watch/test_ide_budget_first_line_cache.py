"""first_line_matches directory cache (cpu-pin hot path A)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from bus_watch.ide_budget import (
    _scan_first_line_matches,
    clear_first_line_matches_cache,
    first_line_matches,
)


def _write(root: Path, tid: str, *, first_user: str, mtime: float) -> None:
    path = root / tid / f"{tid}.jsonl"
    path.parent.mkdir(parents=True)
    row = {
        "role": "user",
        "message": {"content": [{"type": "text", "text": first_user}]},
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    import os

    os.utime(path, (mtime, mtime))


def test_first_line_cache_matches_uncached_and_avoids_reglob(tmp_path: Path) -> None:
    clear_first_line_matches_cache()
    _write(tmp_path, "a", first_user="resume 99 hop", mtime=1.0)
    _write(tmp_path, "b", first_user="resume 99 other", mtime=2.0)
    uncached = _scan_first_line_matches("resume 99", tmp_path)
    clear_first_line_matches_cache()
    glob_calls = 0
    real_glob = Path.glob

    def counting_glob(self, pattern):
        nonlocal glob_calls
        if pattern == "*/*.jsonl":
            glob_calls += 1
        return real_glob(self, pattern)

    with patch.object(Path, "glob", counting_glob):
        cached_once = first_line_matches("resume 99", tmp_path)
        cached_twice = first_line_matches("resume 99", tmp_path)
    assert cached_once == uncached
    assert cached_twice == uncached
    assert glob_calls == 1


def test_first_line_cache_invalidates_on_dir_mtime(tmp_path: Path) -> None:
    clear_first_line_matches_cache()
    _write(tmp_path, "a", first_user="resume 77", mtime=1.0)
    first = first_line_matches("resume 77", tmp_path)
    _write(tmp_path, "b", first_user="resume 77 new tab", mtime=3.0)
    import os

    os.utime(tmp_path, (5.0, 5.0))
    second = first_line_matches("resume 77", tmp_path)
    assert len(second) == len(first) + 1
