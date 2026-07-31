"""Offline tests for create-time density_triage gate."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from implement_admission.density_triage_create_gate import (
    validate_todo_density_triage_at_create,
)


@pytest.mark.offline
def test_accepts_implement_gate_triage_values() -> None:
    for value in ("mechanical", "judgment_required", "recon_pending"):
        validate_todo_density_triage_at_create(
            "todo:x", {"density_triage": value}
        )


@pytest.mark.offline
def test_rejects_unset_and_unknown() -> None:
    with pytest.raises(HTTPException) as unset:
        validate_todo_density_triage_at_create("todo:x", {})
    assert unset.value.status_code == 422
    assert unset.value.detail["error"] == "density_triage_required"

    with pytest.raises(HTTPException) as bad:
        validate_todo_density_triage_at_create(
            "todo:x", {"density_triage": "sparse"}
        )
    assert bad.value.status_code == 422
    assert "sparse" in bad.value.detail["message"]
