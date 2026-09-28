"""Hop commission while the caller's own generate still holds the external gate.

Exempt path: mission_kind=hop excludes that generate and arms. A second live
gate on the lane still returns cdp_external_gate_live (no second successor).
A harvested gate admits as before.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.offline

_CALLER = "96d6a7df-6a58-4255-9b2a-fe72d7b6d6cd"
_THREAD = "12286"
_REG = "reg-caller"


def _row(
    execution_id: str,
    *,
    stream_state: str = "running",
    registration_id: str | None = _REG,
    status: str = "running",
) -> dict[str, str]:
    row = {
        "execution_id": execution_id,
        "parent_thread": _THREAD,
        "stream_state": stream_state,
        "purpose": "operator-proxy",
        "status": status,
    }
    if registration_id:
        row["registration_id"] = registration_id
    return row


def _install_gate_transport(monkeypatch: pytest.MonkeyPatch, snap: dict) -> None:
    """HTTP stand-in: real hop gate, no Stargate process."""
    monkeypatch.setattr(
        "systems.frontier_consult.cdp_generate._read_lane_snapshot_for_gate",
        lambda **_: snap,
    )

    async def _post(endpoint: str, json: dict) -> MagicMock:
        from systems.frontier_consult.admission import FrontierEndpointError
        from systems.frontier_consult.cdp_generate import (
            _refuse_external_gate_for_generate,
        )

        resp = MagicMock()
        try:
            _refuse_external_gate_for_generate(
                purpose=str(json.get("purpose") or ""),
                parent_thread=json.get("parent_thread"),
                thread_id=str(json.get("dispatch_thread_id") or ""),
                request_id="hop-from-live",
                execution_id="new-exec-not-in-snap",
                mission_kind=json.get("mission_kind"),
                predecessor_registration_id=json.get("predecessor_registration_id"),
            )
        except FrontierEndpointError as exc:
            resp.status_code = exc.status_code
            resp.json.return_value = {"code": exc.code, "error": exc.reason}
            return resp
        resp.status_code = 202
        resp.json.return_value = {
            "execution_id": "successor-exec",
            "status": "started",
        }
        return resp

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=_post)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cdp_escalation.make_async_client",
        lambda *_a, **_k: mock_client,
    )


async def _run_hop(
    monkeypatch: pytest.MonkeyPatch,
    *,
    snap: dict,
    registration_id: str | None,
) -> dict:
    from services.git_integration_worker.cursor_auto import continuity_hop as hop_mod
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    _install_gate_transport(monkeypatch, snap)
    q = queue_mod.reset_queue_for_tests(durable=False)
    q.enqueue(
        thread_id=_THREAD,
        turn_number=526,
        subject=f"CONTINUITY HANDOFF — hop (thread {_THREAD})",
        body="TYPE: CONTINUITY_HANDOFF\nscope: hand the lane\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="none",
        continuity_hop=True,
        continuity_matched_token="TYPE:CONTINUITY_HANDOFF",
        cse_registration_id=registration_id,
    )
    claimed = q.claim_next()
    assert claimed is not None
    terminals: list[dict] = []

    async def _fake_terminal(j, **kwargs):
        terminals.append(kwargs)
        return {
            "ok": kwargs.get("terminal_status") != "status:failed",
            "terminal_status": kwargs.get("terminal_status"),
            "failed": kwargs.get("failed", False),
        }

    monkeypatch.setattr(hop_mod, "post_terminal_status", _fake_terminal)
    monkeypatch.setattr(
        hop_mod, "post_harvest_residual", AsyncMock(return_value={"phase": "live"})
    )
    monkeypatch.setattr(hop_mod, "live_run_for_thread", lambda _t: None)
    monkeypatch.setattr(hop_mod, "_post_hop_admit_report", AsyncMock(return_value=None))
    monkeypatch.setattr(hop_mod, "emit_cdp_effort_bind", lambda **_: None)
    monkeypatch.setattr(
        hop_mod,
        "build_hop_orientation",
        AsyncMock(return_value={"generated": False, "block": None}),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_seating_hook.run_cse_seating_hook",
        lambda *a, **k: {"path": "admitted", "successor_seated": False},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.cse_pager_resolve.refresh_pager_after_hop",
        lambda *a, **k: None,
    )

    await hop_mod.complete_continuity_hop(claimed, queue=q, client=MagicMock())
    assert terminals, "hop closer must post a bus turn"
    return terminals[0]


@pytest.mark.asyncio
async def test_hop_from_live_own_generate_arms_not_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller's own live generate is exempt: hop arms, does not status:failed."""
    posted = await _run_hop(
        monkeypatch,
        snap={"rows": [_row(_CALLER)]},
        registration_id=_REG,
    )
    assert posted["terminal_status"] == "status:armed"
    assert posted.get("failed", False) is False
    payload = posted["payload"]
    assert payload["reason"] == "continuity_hop_cdp_commissioned"
    assert payload["hop_phase"] == "armed"
    assert payload["execution_id"] == "successor-exec"
    assert payload["harvest_residual"] == {"phase": "live"}
    assert payload["reason"] != "continuity_hop_cdp_commission_failed"


@pytest.mark.asyncio
async def test_hop_from_harvested_gate_still_arms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A harvested gate does not occupy the lane; hop commission stays armed."""
    posted = await _run_hop(
        monkeypatch,
        snap={
            "rows": [
                _row(
                    _CALLER,
                    stream_state=f"terminal:{_CALLER}",
                    status="failed",
                )
            ]
        },
        registration_id=_REG,
    )
    assert posted["terminal_status"] == "status:armed"
    assert posted["payload"]["hop_phase"] == "armed"
    assert posted["payload"]["reason"] == "continuity_hop_cdp_commissioned"


@pytest.mark.asyncio
async def test_hop_second_live_gate_does_not_arm_a_second_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another live gate besides the caller still blocks commission."""
    posted = await _run_hop(
        monkeypatch,
        snap={
            "rows": [
                _row(_CALLER),
                _row(
                    "successor-already-live",
                    registration_id="reg-successor",
                ),
            ]
        },
        registration_id=_REG,
    )
    assert posted["terminal_status"] == "status:failed"
    assert posted["payload"]["hop_phase"] == "commission_failed"
    assert posted["payload"]["reason"] == "continuity_hop_cdp_commission_failed"
    commission = posted["payload"]["commission"]
    assert commission["status_code"] == 409
    assert commission["error"]["code"] == "cdp_external_gate_live"
    assert "execution_id" not in posted["payload"] or not posted["payload"].get(
        "execution_id"
    )
