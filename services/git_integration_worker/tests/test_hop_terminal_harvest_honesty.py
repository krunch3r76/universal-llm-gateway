"""Hop job terminal honesty — admit is armed; harvest fail appends status:failed."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile import (
    reconcile_stall_revocations,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    load_watches,
    mark_hop_fired,
)
from services.git_integration_worker.cursor_auto.hop_harvest_terminal import (
    build_harvest_failed_turn,
    post_harvest_terminal_for_action,
)
from services.git_integration_worker.tests.commission_spy import commission_spy

pytestmark = pytest.mark.offline

_NOW = 1_700_000_000.0
_EXEC = "8d27c0fe-93f2-4694-82c4-fdca5f9f2b16"
_THREAD = "7230"


def _hop_job(queue, *, body: str = "TYPE: CONTINUITY_HANDOFF\n"):
    queue.enqueue(
        thread_id=_THREAD,
        turn_number=1,
        subject=f"cursor-auto hop cadence — continuity hop thread={_THREAD}",
        body=body,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="light-bounded",
        continuity_hop=True,
        continuity_matched_token="TYPE:CONTINUITY_HANDOFF",
    )
    claimed = queue.claim_next()
    assert claimed is not None
    return claimed


@pytest.mark.asyncio
async def test_commission_ok_posts_armed_not_done(monkeypatch):
    """AC: Stargate admit + execution_id ⇒ status:armed, no dispatched-and-relayed."""
    from services.git_integration_worker.cursor_auto import continuity_hop as hop_mod
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    claimed = _hop_job(q)
    terminals: list[dict] = []

    async def _fake_terminal(j, **kwargs):
        terminals.append(kwargs)
        return {"ok": True, "terminal_status": kwargs.get("terminal_status")}

    monkeypatch.setattr(
        hop_mod, "commission_cdp_escalation", commission_spy(execution_id=_EXEC)
    )
    monkeypatch.setattr(hop_mod, "post_terminal_status", _fake_terminal)
    monkeypatch.setattr(
        hop_mod, "post_harvest_residual", AsyncMock(return_value={"ok": True})
    )
    monkeypatch.setattr(hop_mod, "live_run_for_thread", lambda _t: None)
    monkeypatch.setattr(hop_mod, "_post_hop_admit_report", AsyncMock(return_value=None))
    monkeypatch.setattr(hop_mod, "emit_cdp_effort_bind", lambda **_: None)

    result = await hop_mod.complete_continuity_hop(
        claimed, queue=q, client=MagicMock()
    )
    assert terminals, "hop closer must post a bus turn"
    posted = terminals[0]
    assert posted["terminal_status"] == "status:armed"
    assert posted["disposition"] is None
    payload = posted["payload"]
    assert payload["reason"] == "continuity_hop_cdp_commissioned"
    assert payload["hop_phase"] == "armed"
    assert payload["generate_harvest"] == "open"
    assert payload["execution_id"] == _EXEC
    assert "dispatched-and-relayed" not in json.dumps(payload)
    assert result.get("terminal_status") == "status:armed"


@pytest.mark.asyncio
async def test_commission_non_2xx_posts_failed_not_done(monkeypatch):
    """AC: Stargate non-2xx leaves no status:done on the hop job."""
    from services.git_integration_worker.cursor_auto import continuity_hop as hop_mod
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    claimed = _hop_job(q)
    terminals: list[dict] = []

    async def _fail_commission(job, **kwargs):
        return {
            "ok": False,
            "status_code": 429,
            "error": {"detail": "project-ask HTTP 429"},
        }

    async def _fake_terminal(j, **kwargs):
        terminals.append(kwargs)
        return {
            "ok": False,
            "terminal_status": kwargs.get("terminal_status"),
            "failed": True,
        }

    monkeypatch.setattr(hop_mod, "commission_cdp_escalation", _fail_commission)
    monkeypatch.setattr(hop_mod, "post_terminal_status", _fake_terminal)
    monkeypatch.setattr(
        hop_mod, "post_harvest_residual", AsyncMock(return_value={"ok": True})
    )
    monkeypatch.setattr(hop_mod, "live_run_for_thread", lambda _t: None)
    monkeypatch.setattr(hop_mod, "_post_hop_admit_report", AsyncMock(return_value=None))

    await hop_mod.complete_continuity_hop(claimed, queue=q, client=MagicMock())
    assert terminals
    posted = terminals[0]
    assert posted["terminal_status"] == "status:failed"
    assert posted["failed"] is True
    assert posted["disposition"] == "failed"
    assert posted["payload"]["hop_phase"] == "commission_failed"
    assert posted["payload"]["reason"] == "continuity_hop_cdp_commission_failed"


def test_harvest_failed_successor_seated_true_when_registration_present() -> None:
    """AC1: revoked + watch registration_id ⇒ successor_seated is True."""
    birth = "fce45372d33d49f6993959f18113f839"
    reg = "4c82db64a1b2c3d4e5f6789012345678"
    row = {
        "successor_birth_id": birth,
        "registration_id": reg,
    }
    result = post_harvest_terminal_for_action(
        "revoked",
        thread_id=_THREAD,
        row=row,
        payload={
            "execution_id": _EXEC,
            "error": "project-ask HTTP 429",
            "stall_stage": "submit",
        },
        poster=lambda _t, _s, _b: None,
    )
    assert result is not None
    payload = json.loads(result["body"])
    assert payload["successor_birth_id"] == birth
    assert payload["successor_seated"] is True


def test_harvest_failed_turn_shape_quotes_status_failed() -> None:
    """AC3: failing-generate terminal subject/body are status:failed, not done."""
    subject, body = build_harvest_failed_turn(
        thread_id=_THREAD,
        execution_id=_EXEC,
        error="project-ask HTTP 429",
        stall_stage="submit",
        successor_birth_id="fce45372d33d49f6993959f18113f839",
    )
    assert subject.startswith("status:failed")
    assert "status:done" not in subject
    payload = json.loads(body)
    assert payload["disposition"] == "failed"
    assert payload["reason"] == "continuity_hop_generate_harvest_failed"
    assert payload["execution_id"] == _EXEC
    assert payload["error"] == "project-ask HTTP 429"
    # No registration_id on watch — successor_seated stays False (minted key only).
    assert payload["successor_seated"] is False
    assert payload["history_integrity"] == "append"
    assert "dispatched-and-relayed" not in body


def test_generate_stall_429_appends_failed_not_done(tmp_path: Path) -> None:
    """Admit-then-429 harvest ⇒ appended status:failed; no status:done turn."""
    watch_path = tmp_path / "watches.json"
    state_path = tmp_path / "state.json"
    mark_hop_fired(
        _THREAD,
        now=_NOW,
        path=watch_path,
        execution_id=_EXEC,
        successor_birth_id="fce45372d33d49f6993959f18113f839",
        active_work_snap={
            "rows": [
                {
                    "execution_id": "exec-incumbent",
                    "registration_id": "reg-live",
                    "status": "running",
                }
            ]
        },
    )
    posted: list[tuple[str, str, str]] = []

    def _poster(thread_id: str, subject: str, body: str) -> None:
        posted.append((thread_id, subject, body))

    def _query(_sql: str, _params: list, _limit: int) -> list[dict]:
        return [
            {
                "seq": 3,
                "signal": "cdp.generate.stalled",
                "payload": {
                    "execution_id": _EXEC,
                    "stall_stage": "submit",
                    "error": "project-ask HTTP 429",
                },
            }
        ]

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_succession_revoked"
    ):
        result = reconcile_stall_revocations(
            watches_path=watch_path,
            state_path=state_path,
            now=_NOW + 20.0,
            query_fn=_query,
            harvest_poster=_poster,
        )
    assert any(a["action"] == "revoked" for a in result["actions"])
    assert posted, "harvest fail must append a bus turn"
    thread_id, subject, body = posted[0]
    assert thread_id == _THREAD
    assert subject.startswith("status:failed")
    assert "status:done" not in subject
    payload = json.loads(body)
    assert payload["disposition"] == "failed"
    assert payload["error"] == "project-ask HTTP 429"
    assert payload["execution_id"] == _EXEC
    watches = load_watches(watch_path)
    assert watches[_THREAD]["succession_status"] == "revoked"


def test_post_admit_generate_failure_never_successor_produced(tmp_path: Path) -> None:
    """AC-1: post-admit generate failure never yields done / dispatched-and-relayed.

    Injects a bare-id proof (admit-shaped) plus a 429 stall. Lifecycle must
    stay non-successor at every posted harvest turn.
    """
    watch_path = tmp_path / "watches.json"
    state_path = tmp_path / "state.json"
    mark_hop_fired(
        _THREAD,
        now=_NOW,
        path=watch_path,
        execution_id=_EXEC,
        successor_birth_id="fce45372d33d49f6993959f18113f839",
        active_work_snap={
            "rows": [
                {
                    "execution_id": "exec-incumbent",
                    "registration_id": "reg-live",
                    "status": "running",
                }
            ]
        },
    )
    posted: list[tuple[str, str, str]] = []

    def _poster(thread_id: str, subject: str, body: str) -> None:
        posted.append((thread_id, subject, body))

    def _query(_sql: str, _params: list, _limit: int) -> list[dict]:
        return [
            {
                "seq": 2,
                "signal": "cdp.generate.proof",
                "payload": {"execution_id": _EXEC},
            },
            {
                "seq": 3,
                "signal": "cdp.generate.stalled",
                "payload": {
                    "execution_id": _EXEC,
                    "stall_stage": "submit",
                    "error": "project-ask HTTP 429",
                },
            },
        ]

    with patch(
        "services.git_integration_worker.cursor_auto.hop_cadence_stall_reconcile.emit_succession_revoked"
    ):
        result = reconcile_stall_revocations(
            watches_path=watch_path,
            state_path=state_path,
            now=_NOW + 20.0,
            query_fn=_query,
            harvest_poster=_poster,
        )
    assert any(a["action"] == "revoked" for a in result["actions"])
    assert not any(a["action"] == "confirmed" for a in result["actions"])
    for _thread_id, subject, body in posted:
        assert "status:done" not in subject
        assert "dispatched-and-relayed" not in body
        payload = json.loads(body)
        assert payload.get("disposition") != "dispatched-and-relayed"
    watches = load_watches(watch_path)
    assert watches[_THREAD]["succession_status"] == "revoked"
