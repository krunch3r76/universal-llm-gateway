"""Post-harden material-decision panel split gate (AC9b)."""

from __future__ import annotations

from typing import Any


def material_decision_closeout_complete(
    *,
    material_decision_present: bool,
    panel_artifact: dict[str, Any] | None,
) -> bool:
    """Pre-harden steering reviewer alone never satisfies the panel requirement."""
    if not material_decision_present:
        return True
    if panel_artifact is None:
        return False
    disposition = panel_artifact.get("consensus_disposition")
    families = panel_artifact.get("panel_families") or []
    if disposition != "panel":
        return False
    if not isinstance(families, list) or len(families) < 2:
        return False
    return True


def material_decision_closeout_flags(
    *,
    material_decision_present: bool,
    panel_artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    complete = material_decision_closeout_complete(
        material_decision_present=material_decision_present,
        panel_artifact=panel_artifact,
    )
    return {
        "material_decision_present": material_decision_present,
        "material_decision_closeout_complete": complete,
        "material_decision_panel_required": material_decision_present,
    }
