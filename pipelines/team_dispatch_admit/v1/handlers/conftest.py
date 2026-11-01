"""House watch-dir isolation for team_dispatch_admit handler tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def house_watch_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from implement_admission.house_testing import isolate_watch_dir

    return isolate_watch_dir(monkeypatch, tmp_path)


@pytest.fixture
def pinned_cdp_roles(monkeypatch: pytest.MonkeyPatch):
    from implement_admission.house_testing import pin_cdp_roles

    return pin_cdp_roles(monkeypatch)
