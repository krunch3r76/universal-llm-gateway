"""C1 — manifest completeness (every cell populated, unknowns explicit)."""

from __future__ import annotations

from execution_conformance.manifest import (
    ACTUATORS,
    CAPABILITIES,
    MANIFEST,
    assert_manifest_complete,
    manifest_unknowns,
)


def test_p4_c1_manifest_every_cell_populated() -> None:
    assert_manifest_complete()
    for actuator in ACTUATORS:
        for cap in CAPABILITIES:
            cell = MANIFEST[actuator][cap]
            assert cell.level in {"supported", "partial", "unsupported", "unknown"}
            assert cell.source
            assert "path" in cell.source or "/" in cell.source


def test_p4_c1_manifest_unknowns_explicit() -> None:
    unknowns = manifest_unknowns()
    assert unknowns == [
        (
            "cursor_bridge",
            "cancel",
            MANIFEST["cursor_bridge"]["cancel"],
        )
    ]
