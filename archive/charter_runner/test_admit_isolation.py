"""Unit tests for per-root admission 4xx isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# Phase 3: admit.py deleted (fire path lives in window_exec). Skip until ported.
pytestmark = pytest.mark.skip(reason="Phase 3: admit.py deleted — port to window_exec")

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    ParsedCheckpoint,
)
from scripts.model_manager.ui.controller.charter_runner.admission import Decision
from scripts.model_manager.ui.controller.charter_runner.env_snapshot import EnvSnapshot
from scripts.model_manager.ui.controller.charter_runner.kernel import (
    CharterRunnerTickLoop,
)


def _http_error(status: int, *, body: str = "rejected") -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://test/api/v1/team/dispatch")
    resp = httpx.Response(status_code=status, text=body, request=req)
    return httpx.HTTPStatusError("upstream failure", request=req, response=resp)


def _eligible_decision(root_id: str = "5705") -> Decision:
    parsed = ParsedCheckpoint(
        wip_is_none=True,
        wip_text="none",
        next_pickup=["G1 — Q"],
        next_pickup_gated=True,
        scoreboard_uri="cortex://notes/system/threads/scoreboard.md",
        has_resume_footer=True,
    )
    return Decision(
        True,
        "eligible",
        root_id,
        checkpoint={"turn_number": 1, "body": "# CHECKPOINT"},
        parsed=parsed,
    )


def test_admit_4xx_marks_failed_clears_intent_returns_false(
    monkeypatch, tmp_path: Path
) -> None:
    caps = CapStore(intent_dir=tmp_path / "intent")
    emit = AsyncMock()
    decision = _eligible_decision()
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async def fire_fail(*_args, **_kwargs) -> dict:
        raise _http_error(422, body='{"detail":"invalid packet"}')

    monkeypatch.setattr(admit.dispatch_client, "fire_window", fire_fail)
    monkeypatch.setattr(
        admit.events, "emit_manage_charter_tick_window_failed", emit, raising=False
    )
    monkeypatch.setattr(admit, "select_packet", lambda *a, **k: ("packet", "subject"))

    result = asyncio.run(
        admit.admit_window(
            decision=decision,
            turns=[],
            caps=caps,
            workspace_root=workspace,
            on_admit=None,
        )
    )

    assert result is False
    allowed, reason = caps.check("5705")
    assert not allowed
    assert reason == "stopped:admission_rejected"
    assert not caps.has_admit_intent("5705", 1)
    emit.assert_awaited_once_with(root="5705", reason="admission_rejected")


def test_admit_5xx_keeps_intent_stops_root(monkeypatch, tmp_path: Path) -> None:
    """a:26168 — 5xx must keep intent + stop root (¬ clear+re-raise thrash)."""
    caps = CapStore(intent_dir=tmp_path / "intent")
    decision = _eligible_decision()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    emit = AsyncMock()

    async def fire_fail(*_args, **_kwargs) -> dict:
        raise _http_error(500)

    monkeypatch.setattr(admit.dispatch_client, "fire_window", fire_fail)
    monkeypatch.setattr(admit, "select_packet", lambda *a, **k: ("packet", "subject"))
    monkeypatch.setattr(
        admit.events, "emit_manage_charter_tick_window_failed", emit, raising=False
    )

    # Intent is marked before fire; 5xx path must leave it in place.
    caps.mark_admit_intent("5705", 1)
    result = asyncio.run(
        admit.admit_window(
            decision=decision,
            turns=[],
            caps=caps,
            workspace_root=workspace,
            on_admit=None,
        )
    )

    assert result is False
    assert caps.has_admit_intent("5705", 1)
    allowed, reason = caps.check("5705")
    assert not allowed
    assert reason == "stopped:admission_transport_error"
    emit.assert_awaited_once_with(root="5705", reason="admission_transport_error")


def test_tick_once_continues_after_first_root_422(monkeypatch, tmp_path: Path) -> None:
    roots = [{"id": "root-a"}, {"id": "root-b"}]
    evaluated: list[str] = []

    async def list_roots() -> list[dict]:
        return roots

    async def fetch_turns(root_id: str) -> list[dict]:
        return []

    async def harvest(_root_id: str, _turns: list[dict], *, admission_mode=None) -> list:
        return []

    def evaluate(
        root_id: str,
        _turns: list[dict],
        _caps: CapStore,
        *,
        env_snapshot=None,
        admission_mode: str = "generate",
        now=None,
    ) -> Decision:
        evaluated.append(root_id)
        return _eligible_decision(root_id)

    async def admit_fail(decision: Decision, turns: list[dict], env: EnvSnapshot) -> bool:
        if decision.root_id == "root-a":
            raise _http_error(422)
        return False

    async def emit_scanned(**kwargs) -> None:
        return None

    async def noop_heal(*_a, **_k) -> bool:
        return False

    async def empty_env() -> None:
        return None

    async def fake_build_env_snapshot(
        *,
        root_ids: list[str],
        env_half=None,
        in_flight=None,
    ) -> EnvSnapshot:
        return EnvSnapshot(
            giw_holder_lease={"held": False, "holder": None, "residue": None},
            propagation_residue={"kind": None, "detail": None},
            in_flight_windows=[],
            satellite_health={"cdp": "up", "project_ask": "up"},
            attendance_by_root={rid: "attended" for rid in root_ids},
            scoreboard_pointer={},
            bus_tip_meta={},
        )

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.bus_client.list_enrolled_roots",
        list_roots,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.bus_client.fetch_turns",
        fetch_turns,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.harvest_completed_windows",
        harvest,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.evaluate_root",
        evaluate,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.maybe_heal_admit_intent_orphan",
        noop_heal,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.build_tick_env_snapshot",
        empty_env,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.env_snapshot.build_env_snapshot",
        fake_build_env_snapshot,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.kernel.events.emit_manage_charter_tick_scanned",
        emit_scanned,
        raising=False,
    )

    loop = CharterRunnerTickLoop(
        service_state=MagicMock(),
        shutdown_gate=MagicMock(),
        workspace_root=tmp_path,
        caps=CapStore(intent_dir=tmp_path / "intent"),
    )
    monkeypatch.setattr(loop, "_admit_window", admit_fail)

    asyncio.run(loop._tick_once())

    assert evaluated == ["root-a", "root-b"]
