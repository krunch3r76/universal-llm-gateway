"""Integration test: contract=sketch source_ref → route_contract lane B."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from systems.frontier_consult.implement_admission_bridge import (
    resolve_source_ref_to_packet,
)


@pytest.mark.offline
def test_sketch_source_ref_route_contract(tmp_path: Path) -> None:
    cortex = MagicMock()
    cortex.entity_get.return_value = {
        "id": "todo:sketch-test",
        "name": "Sketch test",
        "attributes": {"slug": "sketch-test"},
    }
    out_dir = tmp_path / "materialized"
    out_dir.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "implement_admission.sketch_materialize.materialize_sketch",
            lambda source_ref, *, cortex, out_dir: MagicMock(
                path=str(out_dir / "sketch-test-sketch.md"),
                packet_sha256="deadbeef",
                text="contract: sketch\n",
            ),
        )
        mp.setattr(
            "systems.frontier_consult.implement_admission_bridge.probe_packet_presence",
            lambda *a, **k: True,
        )
        result = resolve_source_ref_to_packet(
            "todo:sketch-test",
            cortex=cortex,
            workspaces_root=tmp_path,
            contract="sketch",
        )
    assert result.route_contract == {"contract": "sketch", "lane": "B"}
