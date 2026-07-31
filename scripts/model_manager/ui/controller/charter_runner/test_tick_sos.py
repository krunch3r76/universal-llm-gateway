"""Charter-tick SOS classify / threshold / claim (offline)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pager_notify import sos as pager_sos

from scripts.model_manager.ui.controller.charter_runner import tick_sos


@pytest.fixture(autouse=True)
def _reset_sos_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tick_sos._counter.clear()  # noqa: SLF001 — test isolation
    monkeypatch.setenv("PAGER_NOTIFY_STATE_DIR", str(tmp_path / "pager"))
    monkeypatch.setenv("CHARTER_TICK_SOS_THRESHOLD", "3")
    monkeypatch.setenv("CHARTER_TICK_SOS_STICKY_THRESHOLD", "3")
    monkeypatch.setenv("CHARTER_TICK_SOS_CDP", "0")
    monkeypatch.setenv("CHARTER_TICK_HEAL_ENABLED", "1")
    monkeypatch.setenv("PAGER_NOTIFY_TICK_SOS", "1")
    monkeypatch.setenv(
        "CHARTER_RUNNER_DATA_DIR", str(tmp_path / "charter-data")
    )


@pytest.mark.offline
def test_classify_consult_pending_empty_hopper() -> None:
    assert (
        tick_sos.classify_skip_for_sos(
            skipped_reason="empty_hopper",
            consult_pending=True,
        )
        == "consult_pending_empty_hopper"
    )
    assert (
        tick_sos.classify_skip_for_sos(
            skipped_reason="empty_hopper",
            consult_pending=False,
        )
        is None
    )


@pytest.mark.offline
def test_classify_sticky_admitted() -> None:
    assert (
        tick_sos.classify_skip_for_sos(
            skipped_reason=None,
            ledger_status="ADMITTED",
            old_decision_label="NOOP",
        )
        == "sticky_admitted"
    )
    assert (
        tick_sos.classify_skip_for_sos(
            skipped_reason=None,
            ledger_status="IDLE",
            old_decision_label="NOOP",
        )
        is None
    )


@pytest.mark.offline
def test_claim_dedupes_within_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHARTER_TICK_SOS_TTL_S", "3600")
    assert pager_sos.claim_tick_sos("6237", "sticky_admitted", now=100.0) is True
    assert pager_sos.claim_tick_sos("6237", "sticky_admitted", now=200.0) is False
    assert pager_sos.claim_tick_sos("6238", "executor_mismatch", now=200.0) is True


@pytest.mark.offline
def test_maybe_fire_after_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    pages: list[tuple[str, str]] = []

    async def fake_notify(**kwargs):
        pages.append((kwargs["root_id"], kwargs["reason"]))
        return True

    async def fake_bus(*_a, **_k):
        return True

    async def fake_emit(**_k):
        return None

    monkeypatch.setattr(tick_sos, "notify_tick_sos", fake_notify)
    monkeypatch.setattr(tick_sos, "_post_cursor_auto_note", fake_bus)
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.telemetry.emit_root_skip_observed",
        fake_emit,
    )

    async def run() -> None:
        for _ in range(2):
            out = await tick_sos.maybe_fire_tick_sos(
                "6237",
                skipped_reason="executor_mismatch",
                giw_payload={},
            )
            assert out is None
        out = await tick_sos.maybe_fire_tick_sos(
            "6237",
            skipped_reason="executor_mismatch",
            giw_payload={},
        )
        assert out is not None
        assert out["reason"] == "executor_mismatch"
        assert out["consecutive"] == 3
        assert pages == [("6237", "executor_mismatch")]

    asyncio.run(run())


@pytest.mark.offline
def test_sticky_live_holder_suppresses_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path / "charter-data"))
    pages: list[tuple[str, str]] = []

    async def fake_notify(**kwargs):
        pages.append((kwargs["root_id"], kwargs["reason"]))
        return True

    async def fake_bus(*_a, **_k):
        return True

    async def fake_emit(**_k):
        return None

    monkeypatch.setattr(tick_sos, "notify_tick_sos", fake_notify)
    monkeypatch.setattr(tick_sos, "_post_cursor_auto_note", fake_bus)
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.telemetry.emit_root_skip_observed",
        fake_emit,
    )
    live = {
        "active_ops": [{"kind": "cursor_sdk", "op_id": "abc"}],
        "cursor_dispatches": {"dispatch_ids": ["abc"]},
    }

    async def run() -> None:
        for _ in range(5):
            out = await tick_sos.maybe_fire_tick_sos(
                "6237",
                skipped_reason=None,
                ledger_status="ADMITTED",
                old_decision_label="NOOP",
                giw_payload=live,
            )
            assert out is None
        assert pages == []

    asyncio.run(run())


@pytest.mark.offline
def test_orphan_holder_fires_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(tmp_path / "charter-data"))
    pages: list[tuple[str, str]] = []

    async def fake_notify(**kwargs):
        pages.append((kwargs["root_id"], kwargs["reason"]))
        return True

    async def fake_bus(*_a, **_k):
        return True

    async def fake_emit(**_k):
        return None

    monkeypatch.setattr(tick_sos, "notify_tick_sos", fake_notify)
    monkeypatch.setattr(tick_sos, "_post_cursor_auto_note", fake_bus)
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.telemetry.emit_root_skip_observed",
        fake_emit,
    )
    orphan = {
        "write_lease": {"holder_dispatch_id": "dead"},
        "active_ops": [],
        "cursor_dispatches": {"dispatch_ids": []},
    }

    async def run() -> None:
        out = await tick_sos.maybe_fire_tick_sos(
            "6237",
            skipped_reason=None,
            ledger_status="ADMITTED",
            old_decision_label="NOOP",
            giw_payload=orphan,
        )
        assert out is not None
        assert out["reason"] == "orphan_holder_no_live_backing"
        assert pages == [("6237", "orphan_holder_no_live_backing")]

    asyncio.run(run())


@pytest.mark.offline
def test_empty_hopper_ages_to_escalate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = tmp_path / "charter-data"
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data))
    from scripts.model_manager.ui.controller.charter_runner import ledger_age

    ledger_age.seed_first_seen(
        "tick_stall",
        "6237",
        first_seen_at_ts=1.0,
        observation_count=1,
        data_dir=data,
    )
    pages: list[tuple[str, str]] = []

    async def fake_notify(**kwargs):
        pages.append((kwargs["root_id"], kwargs["reason"]))
        return True

    async def fake_bus(*_a, **_k):
        return True

    async def fake_emit(**_k):
        return None

    monkeypatch.setattr(tick_sos, "notify_tick_sos", fake_notify)
    monkeypatch.setattr(tick_sos, "_post_cursor_auto_note", fake_bus)
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.telemetry.emit_root_skip_observed",
        fake_emit,
    )

    async def run() -> None:
        out = await tick_sos.maybe_fire_tick_sos(
            "6237",
            skipped_reason="empty_hopper",
            giw_payload={},
        )
        assert out is not None
        assert out["reason"] == "skip_age_exceeded"

    asyncio.run(run())
    assert pages == [("6237", "skip_age_exceeded")]


@pytest.mark.offline
def test_episode_actuator_falls_back_to_old_decision_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """a:26923 — slow-fuse SOS must name kernel label when fire_attempt_reason empty."""
    from scripts.model_manager.ui.controller.charter_runner import ledger_age
    from scripts.model_manager.ui.controller.charter_runner.root_health import (
        observe_root_health,
    )

    data = tmp_path / "charter-data"
    monkeypatch.setenv("CHARTER_RUNNER_DATA_DIR", str(data))
    monkeypatch.setenv("CHARTER_TICK_HEAL_ENABLED", "1")
    ledger_age.seed_first_seen(
        "tick_stall",
        "6237",
        first_seen_at_ts=1.0,
        observation_count=400,
        data_dir=data,
    )
    pages: list[tuple[str, str]] = []

    async def fake_notify(**kwargs):
        pages.append((kwargs["root_id"], kwargs["reason"]))
        return True

    async def fake_bus(*_a, **_k):
        return True

    async def fake_escalation(**_k):
        return None

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.tick_sos.notify_tick_sos",
        fake_notify,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.tick_sos._post_cursor_auto_note",
        fake_bus,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.telemetry.emit_tick_escalation",
        fake_escalation,
    )

    async def run() -> None:
        result = await observe_root_health(
            "6237",
            fire_attempt_outcome=None,
            fire_attempt_reason=None,
            old_decision_label="kernel_consult_already_queued",
            data_dir=data,
        )
        assert result.unhealthy is True
        assert result.fire_attempt_reason == "kernel_consult_already_queued"

    asyncio.run(run())
    assert pages == [("6237", "kernel_consult_already_queued")]
