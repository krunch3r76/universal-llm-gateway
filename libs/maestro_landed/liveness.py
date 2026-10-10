"""Interpret fleet_liveness snapshots for restart-plan drops."""

from __future__ import annotations

from typing import Any


def service_live(snapshot: dict[str, Any], service: str) -> bool:
    if "error" in snapshot or not isinstance(snapshot.get("services"), list):
        return False
    by_service = {
        row.get("service"): row for row in snapshot["services"] if isinstance(row, dict)
    }
    row = by_service.get(service)
    if not isinstance(row, dict):
        return False
    validation = row.get("code_ref_validation")
    if not isinstance(validation, dict):
        return False
    liveness = validation.get("liveness")
    if not isinstance(liveness, dict):
        return False
    return liveness.get("answer") == "yes"
