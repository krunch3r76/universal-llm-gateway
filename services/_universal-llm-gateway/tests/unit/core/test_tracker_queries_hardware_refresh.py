"""Hardware VRAM refresh before preflight / post-unload RESOURCE_UPDATE."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_root = Path(__file__).resolve().parents[3]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.core.resources.tracker_queries import refresh_hardware_vram_probe  # noqa: E402


def test_refresh_hardware_vram_probe_records_probe() -> None:
    tracker = MagicMock()
    with patch(
        "src.core.resources.tracker_queries.get_vram_info",
        return_value={"available_vram_mb": 12_000, "total_vram_mb": 32_000},
    ), patch(
        "src.core.resources.tracker_queries.get_ram_info",
        return_value={"available_ram_mb": 16_000, "total_ram_mb": 64_000},
    ):
        out = refresh_hardware_vram_probe(tracker)
    assert out["available_vram_mb"] == 12_000
    assert tracker._last_hardware_available_vram_mb == 12_000
