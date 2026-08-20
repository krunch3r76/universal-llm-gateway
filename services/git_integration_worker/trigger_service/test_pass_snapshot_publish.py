"""Tests for fleet-idle pass snapshot publication (slice A)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.trigger_service.fleet_idle import (
    FleetIdleSnapshot,
    FleetVerdict,
    begin_idle_pass,
    read_fleet_idle_memoized,
    reset_grace_tracker,
)
from services.git_integration_worker.trigger_service.models import PREDICATE_FLEET_IDLE
from services.git_integration_worker.trigger_service.pass_snapshot_publish import (
    SNAPSHOT_URI,
    build_observation_payload,
    publish_pass_snapshot,
    snapshot_dest_path,
    staleness_rule_text,
)
from services.git_integration_worker.trigger_service.store import TriggerStore

_PROMPT = "cortex://notes/system/threads/test-prompt.md"
_FLEET_ARGS = {
    "require_tick_empty": True,
    "require_dispatch_idle": True,
    "grace_s": 60,
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
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    reset_grace_tracker()
    begin_idle_pass()
    return TriggerStore()


def _busy_snapshot() -> FleetIdleSnapshot:
    return FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=True,
        tick_empty=True,
        cursor_auto_idle=True,
        cdp_lane_idle=False,
    )


def test_publish_uses_same_memoized_object(store: TriggerStore) -> None:
    """AC1: snapshot file reflects the memoized object the gate consumed."""
    now = datetime.now(UTC)
    row = store.schedule(
        created_by="test",
        fire_at=now - timedelta(seconds=5),
        prompt_uri=_PROMPT,
        predicate=PREDICATE_FLEET_IDLE,
        predicate_args=_FLEET_ARGS,
    )
    snap = _busy_snapshot()
    reader = _StaticFleetReader(snap)

    with patch(
        "services.git_integration_worker.trigger_service.store_claim.read_fleet_idle_memoized",
        side_effect=lambda _reader=None: read_fleet_idle_memoized(reader),
    ):
        begin_idle_pass()
        memo = read_fleet_idle_memoized(reader)
        claimed = store.claim_due(now=now)

    assert claimed is None
    dest = snapshot_dest_path()
    assert dest.is_file()
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["fleet_verdict"] == snap.verdict.value
    assert payload["cdp_lane_idle"] is False
    assert payload["trigger_row_id"] == row.id
    assert payload["grace_s"] == 60
    assert memo is snap


def test_publish_failure_does_not_wedge_gate(store: TriggerStore) -> None:
    """AC2: raising writer cannot alter gate verdict or defer semantics."""
    now = datetime.now(UTC)
    store.schedule(
        created_by="test",
        fire_at=now - timedelta(seconds=5),
        prompt_uri=_PROMPT,
        predicate=PREDICATE_FLEET_IDLE,
        predicate_args={**_FLEET_ARGS, "grace_s": 0},
    )
    snap = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=False,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    reader = _StaticFleetReader(snap)

    with patch(
        "services.git_integration_worker.trigger_service.store_claim.read_fleet_idle_memoized",
        side_effect=lambda _reader=None: read_fleet_idle_memoized(reader),
    ), patch(
        "services.git_integration_worker.trigger_service.pass_snapshot_publish.durable_write_text",
        side_effect=RuntimeError("disk full"),
    ):
        begin_idle_pass()
        claimed = store.claim_due(now=now)

    assert claimed is None
    rows = store.list_triggers()
    updated = store.get(rows[0].id)
    assert updated is not None
    assert updated.status == "scheduled"
    assert updated.last_fleet_verdict == "busy"


def test_atomic_write_leaves_no_partial_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3: temp+rename — destination never holds partial JSON."""
    cortex_root = tmp_path / "cortex"
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    snap = _busy_snapshot()
    pass_at = datetime(2026, 7, 31, 20, 0, tzinfo=UTC)
    dest = snapshot_dest_path()
    observed: list[str] = []

    original_replace = __import__("os").replace

    def _spy_replace(src: str, dst: str) -> None:
        observed.append(Path(src).read_text(encoding="utf-8"))
        original_replace(src, dst)

    with patch(
        "durable_io.atomic.os.replace",
        _spy_replace,
    ):
        publish_pass_snapshot(
            snap,
            trigger_row_id="row-1",
            defer_count=2,
            grace_s=60,
            pass_at=pass_at,
        )

    assert dest.is_file()
    json.loads(dest.read_text(encoding="utf-8"))
    assert not list(dest.parent.glob("*.tmp-*"))
    for body in observed:
        json.loads(body)


def test_snapshot_self_describes_staleness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC5: staleness rule is embedded so readers cannot confuse staleness with failure."""
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    snap = _busy_snapshot()
    payload = build_observation_payload(
        snap,
        trigger_row_id="row-1",
        defer_count=0,
        grace_s=30,
        pass_at=datetime(2026, 7, 31, 20, 0, tzinfo=UTC),
    )
    rule = payload["staleness_rule"]
    assert "UNDETERMINED-for-observation" in rule
    assert "legitimate staleness" in rule
    assert rule == staleness_rule_text()


def test_gate_never_reads_snapshot_file() -> None:
    """AC6: fleet_idle/store_claim have no read path to the published snapshot."""
    import services.git_integration_worker.trigger_service.fleet_idle as fleet_idle
    import services.git_integration_worker.trigger_service.store_claim as store_claim

    fleet_src = Path(fleet_idle.__file__).read_text(encoding="utf-8")
    claim_src = Path(store_claim.__file__).read_text(encoding="utf-8")
    rel = "fleet-idle-gate-observation"
    assert rel not in fleet_src
    assert "read_text" not in claim_src.split("publish_pass_snapshot")[0]
    assert claim_src.count("publish_pass_snapshot") == 2


def test_life_fs_read_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4: published file is readable at the stable cortex URI."""
    cortex_root = tmp_path / "cortex"
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    snap = _busy_snapshot()
    publish_pass_snapshot(
        snap,
        trigger_row_id="row-life",
        defer_count=1,
        grace_s=45,
        pass_at=datetime(2026, 7, 31, 20, 15, tzinfo=UTC),
    )
    rel = SNAPSHOT_URI.removeprefix("cortex://")
    read_back = (cortex_root / rel).read_text(encoding="utf-8")
    payload = json.loads(read_back)
    assert payload["snapshot_uri"] == SNAPSHOT_URI
    assert payload["trigger_row_id"] == "row-life"
