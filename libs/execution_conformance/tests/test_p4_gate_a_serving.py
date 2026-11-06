"""Gate A — execution_conformance paths declared unserved for implement_admission."""

from __future__ import annotations

from pathlib import Path

from implement_admission.serving_coverage import (
    path_serving_coverage,
    residue_for_empty_nominations,
)

_ROOT = Path(__file__).resolve().parents[3]
_PKG = Path(__file__).resolve().parents[1]


def test_p4_gate_a_execution_conformance_unserved() -> None:
    for path in sorted(_PKG.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        if not rel.startswith("libs/execution_conformance/"):
            continue
        assert path_serving_coverage(rel) == "unserved"
        residue = residue_for_empty_nominations(rel)
        assert any("declared unserved" in line for line in residue)
        assert not any(line.startswith("unmapped_serving:") for line in residue)
