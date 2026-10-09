"""liaison-monitor-wake card URI follows house_pools."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "liaison-monitor-wake.py"


def _card_uri(root_id: str) -> str:
    spec = importlib.util.spec_from_file_location("liaison_monitor_wake", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._card_uri(root_id)


def test_card_uri_continuity_card_only_house(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    card = tmp_path / "notes/system/threads/10479-continuity-card.md"
    card.parent.mkdir(parents=True)
    card.write_text("# continuity card\n", encoding="utf-8")
    assert _card_uri("10479") == (
        "cortex://notes/system/threads/10479-continuity-card.md"
    )
