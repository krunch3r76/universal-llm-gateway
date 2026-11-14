"""Runner payload carries compose quota from last bare ensure (a:43854 r2/r3)."""

from __future__ import annotations

import json

import pytest
from claude_bundles import compose_setup_emit as cse
from claude_bundles.events_compose_attest import emit_compose_attested_from_result


@pytest.mark.offline
def test_compose_runner_quota_fields_reexported() -> None:
    from cdp_ask.runner import compose_runner_quota_fields as from_runner

    cse._LAST_BARE_COMPOSE_ENSURE = {"quota_weekly_pct": 88, "step": "compose_v2"}
    try:
        assert from_runner() == {"quota_weekly_pct": 88, "quota_near": False}
    finally:
        cse._LAST_BARE_COMPOSE_ENSURE = None


@pytest.mark.offline
def test_runner_payload_absent_banner_emits_explicit_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.runner import compose_runner_quota_fields as from_runner

    for snap in (
        None,
        {"ok": True, "step": "compose_v2", "quota_weekly_pct": None},
    ):
        monkeypatch.setattr(cse, "_LAST_BARE_COMPOSE_ENSURE", snap)
        converse_payload = {"ok": True, **from_runner()}
        ask_payload: dict[str, object] = {"ok": True}
        ask_payload.update(from_runner())
        for payload in (converse_payload, ask_payload):
            assert "quota_weekly_pct" in payload
            assert payload["quota_weekly_pct"] is None
            assert payload["quota_near"] is False
            wire = json.loads(json.dumps(payload))
            assert "quota_weekly_pct" in wire
            assert wire["quota_weekly_pct"] is None
            assert wire["quota_near"] is False


@pytest.mark.offline
def test_compose_attested_event_carries_quota() -> None:
    result = {
        "ok": False,
        "step": "quota_near",
        "failure_class": "quota_near",
        "quota_weekly_pct": 96,
    }
    event = emit_compose_attested_from_result(
        result,
        execution_id="sg",
        satellite_execution_id="sat",
    )
    assert event is not None
    assert event.payload["quota_weekly_pct"] == 96
    assert event.payload["quota_near"] is True
