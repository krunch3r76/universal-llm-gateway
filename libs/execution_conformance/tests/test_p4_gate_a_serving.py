"""Gate A — execution_conformance declared unserved for implement_admission."""

from __future__ import annotations

from implement_admission.serving_coverage import (
    path_serving_coverage,
    residue_for_empty_nominations,
)


def test_p4_gate_a_execution_conformance_unserved() -> None:
    path = "libs/execution_conformance/reconciler.py"
    assert path_serving_coverage(path) == "unserved"
    residue = residue_for_empty_nominations(path)
    assert any("declared unserved" in line for line in residue)
    assert not any(line.startswith("unmapped_serving:") for line in residue)
