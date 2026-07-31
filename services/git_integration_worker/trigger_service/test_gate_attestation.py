"""Fleet gate attestation on trigger wake prompts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cdp_ask.client import CdpAskClientError

from services.git_integration_worker.trigger_service.fire import fire_once, submit_fire
from services.git_integration_worker.trigger_service.fleet_idle import (
    FleetIdleSnapshot,
    FleetVerdict,
    begin_idle_pass,
    read_fleet_idle_memoized,
    reset_grace_tracker,
)
from services.git_integration_worker.trigger_service.gate_attestation import (
    compose_attested_prompt,
    load_prompt_body,
    render_attestation_block,
)
from services.git_integration_worker.trigger_service.models import PREDICATE_FLEET_IDLE
from services.git_integration_worker.trigger_service.store import TriggerStore

_PROMPT_REL = "notes/system/threads/test-prompt.md"
_PROMPT_URI = f"cortex://{_PROMPT_REL}"
_ORIGINAL_BODY = "TYPE: DIRECTIVE\nvision: mechanical — idle wake test\n"
_FLEET_ARGS = {
    "require_tick_empty": True,
    "require_dispatch_idle": True,
    "grace_s": 45,
}


class _StaticFleetReader:
    def __init__(self, snapshot: FleetIdleSnapshot) -> None:
        self._snapshot = snapshot
        self.read_count = 0

    def read(self) -> FleetIdleSnapshot:
        self.read_count += 1
        return self._snapshot


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TriggerStore:
    cortex_root = tmp_path / "cortex"
    prompt_path = cortex_root / _PROMPT_REL
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(_ORIGINAL_BODY, encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    reset_grace_tracker()
    begin_idle_pass()
    return TriggerStore()


def _idle_snapshot() -> FleetIdleSnapshot:
    return FleetIdleSnapshot(
        verdict=FleetVerdict.IDLE,
        dispatch_idle=True,
        tick_empty=True,
        cursor_auto_idle=True,
        cdp_lane_idle=True,
    )


def test_load_prompt_body_reads_cortex_uri(store: TriggerStore) -> None:
    assert load_prompt_body(_PROMPT_URI) == _ORIGINAL_BODY


def test_fleet_idle_attestation_includes_probe_fields(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) + timedelta(hours=1),
        prompt_uri=_PROMPT_URI,
        predicate=PREDICATE_FLEET_IDLE,
        predicate_args=_FLEET_ARGS,
    )
    snap = _idle_snapshot()
    attested_at = datetime(2026, 7, 31, 19, 30, tzinfo=UTC)
    block = render_attestation_block(
        row,
        snapshot=snap,
        attested_at=attested_at,
    )
    assert "fleet_gate_applied: true" in block
    assert "verdict: idle" in block
    assert "dispatch_idle: true" in block
    assert "tick_empty: true" in block
    assert "cursor_auto_idle: true" in block
    assert "cdp_lane_idle: true" in block
    assert "grace_s: 45" in block
    assert "attested_at_utc: 2026-07-31T19:30:00Z" in block
    assert f"prompt_uri: {_PROMPT_URI}" in block
    assert "pass_snapshot_uri:" in block
    assert "not agent_bus.request" in block


def test_non_fleet_row_attestation_states_no_gate(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) + timedelta(hours=1),
        prompt_uri=_PROMPT_URI,
    )
    block = render_attestation_block(
        row,
        attested_at=datetime(2026, 7, 31, 19, 30, tzinfo=UTC),
    )
    assert "fleet_gate_applied: false" in block
    assert "no fleet_idle predicate" in block
    assert "verdict:" not in block


def test_compose_attested_prompt_preserves_body_verbatim(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) + timedelta(hours=1),
        prompt_uri=_PROMPT_URI,
        predicate=PREDICATE_FLEET_IDLE,
        predicate_args=_FLEET_ARGS,
    )
    snap = _idle_snapshot()
    attested_at = datetime(2026, 7, 31, 19, 30, tzinfo=UTC)
    with patch(
        "services.git_integration_worker.trigger_service.gate_attestation.read_fleet_idle_memoized",
        return_value=snap,
    ):
        composed = compose_attested_prompt(row, attested_at=attested_at)
    assert composed.startswith(_ORIGINAL_BODY.rstrip())
    assert "attested_at_utc: 2026-07-31T19:30:00Z" in composed
    assert "pass_snapshot_uri:" in composed
    assert composed.index(_ORIGINAL_BODY.rstrip()) < composed.index("## FLEET GATE ATTESTATION")


def test_fleet_reader_invoked_once_across_claim_and_submit(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    store.schedule(
        created_by="test",
        fire_at=now - timedelta(seconds=5),
        prompt_uri=_PROMPT_URI,
        predicate=PREDICATE_FLEET_IDLE,
        predicate_args={**_FLEET_ARGS, "grace_s": 0},
    )
    reader = _StaticFleetReader(_idle_snapshot())
    client = MagicMock()
    client.submit.return_value = {"execution_id": "exec-attest", "status": "running"}

    with patch(
        "services.git_integration_worker.trigger_service.store_claim.read_fleet_idle_memoized",
        side_effect=lambda _reader=None: read_fleet_idle_memoized(reader),
    ), patch(
        "services.git_integration_worker.trigger_service.gate_attestation.read_fleet_idle_memoized",
        side_effect=lambda _reader=None: read_fleet_idle_memoized(reader),
    ), patch(
        "services.git_integration_worker.trigger_service.fire.lane_available",
        return_value=(True, None),
    ), patch(
        "services.git_integration_worker.trigger_service.fire.publish_lib_signal",
    ):
        begin_idle_pass()
        claimed = store.claim_due(now=now)
        assert claimed is not None
        fired = fire_once(store, claimed, client=client)

    assert fired.status == "fired"
    assert reader.read_count == 1
    submit_body = client.submit.call_args[0][0]
    prompt_text = submit_body.prompt_text
    assert prompt_text is not None
    assert _ORIGINAL_BODY.rstrip() in prompt_text
    assert "verdict: idle" in prompt_text
    assert f"prompt_uri: {_PROMPT_URI}" in prompt_text


def test_submit_fire_raises_on_missing_execution_id(store: TriggerStore) -> None:
    row = store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) - timedelta(seconds=1),
        prompt_uri=_PROMPT_URI,
    )
    client = MagicMock()
    client.submit.return_value = {"status": "running"}
    with pytest.raises(CdpAskClientError, match="missing execution_id"):
        submit_fire(row, client=client)


def test_lane_busy_retry_path_unchanged(store: TriggerStore) -> None:
    store.schedule(
        created_by="test",
        fire_at=datetime.now(UTC) - timedelta(seconds=1),
        prompt_uri=_PROMPT_URI,
    )
    claimed = store.claim_due()
    assert claimed is not None
    client = MagicMock()
    with patch(
        "services.git_integration_worker.trigger_service.fire.lane_available",
        return_value=(False, "lane busy: 1 live operator-proxy session(s)"),
    ), patch(
        "services.git_integration_worker.trigger_service.fire.publish_lib_signal",
    ):
        updated = fire_once(store, claimed, client=client)
    assert updated.status == "scheduled"
    assert updated.attempts == 0
    client.submit.assert_not_called()
