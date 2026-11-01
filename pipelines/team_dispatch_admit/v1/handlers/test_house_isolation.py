"""House watch-dir isolation for handler tests."""

from __future__ import annotations

from pathlib import Path


def test_watch_dir_isolation_active_handlers(tmp_path: Path) -> None:
    import bus_watch.fable_lock

    assert str(bus_watch.fable_lock.WATCH_DIR).startswith(str(tmp_path))
