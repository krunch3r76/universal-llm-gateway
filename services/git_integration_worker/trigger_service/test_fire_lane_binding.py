"""Lane binding passthrough on trigger fire (no GIW route imports)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.trigger_service.fire import (
    _lane_binding_from_row,
    lane_available,
)
from services.git_integration_worker.trigger_service.models import TriggerRow


def _row(*, predicate_args: str | None) -> TriggerRow:
    return TriggerRow(
        id="t1",
        created_at="t",
        created_by="test",
        fire_at="t",
        prompt_uri="cortex://notes/system/threads/x.md",
        purpose="operator-proxy",
        model="opus-5",
        arc=None,
        so_what=None,
        status="scheduled",
        attempts=0,
        max_attempts=3,
        last_error=None,
        claimed_at=None,
        execution_id=None,
        fired_at=None,
        terminal_status=None,
        archive_uri=None,
        cancelled_at=None,
        predicate=None,
        predicate_args=predicate_args,
        expires_at=None,
        last_predicate_error=None,
    )


def test_lane_binding_from_predicate_args() -> None:
    row = _row(
        predicate_args=(
            '{"parent_thread":"7188","mission_kind":"hop",'
            '"predecessor_registration_id":"pred-reg"}'
        ),
    )
    parent, hop, pred = _lane_binding_from_row(row)
    assert parent == "7188"
    assert hop is True
    assert pred == "pred-reg"


def test_lane_available_passes_parent_thread_to_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []

    def _refusal(snap, **kwargs):
        captured.append(kwargs)
        return False, None, None

    monkeypatch.setattr(
        "cdp_ask.lane_admission.purpose_lane_refusal",
        _refusal,
    )
    client = MagicMock()
    client._request.return_value = {"rows": [], "seat_rows": []}
    row = _row(
        predicate_args='{"parent_thread":"6655","mission_kind":"root"}',
    )
    lane_available(client, purpose="operator-proxy", row=row)
    assert captured[0]["parent_thread"] == "6655"
    assert captured[0]["hop_succession"] is False
    assert captured[0]["mission_kind"] == "root"
