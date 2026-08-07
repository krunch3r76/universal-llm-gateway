"""Cadence hop must resolve incumbent like the HTTP hop (arc 6893).

Regression: hop_cadence hardcoded ``incumbent=None``, so harvest residual
claimed an empty lane while a claimed Auto commission was mid-flight.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.cursor_auto.hop_cadence import (
    fire_hop_for_decision,
)
from services.git_integration_worker.cursor_auto.hop_cadence_watch import (
    HopDecision,
    StandingHandoffFreshness,
)


@pytest.mark.asyncio
async def test_cadence_hop_passes_claimed_incumbent_to_continuity_hop(monkeypatch):
    """Fails if cadence still hardcodes incumbent=None (AC2)."""
    from services.git_integration_worker.cursor_auto import hop_cadence as cadence_mod
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    incumbent = q.enqueue(
        thread_id="T-cadence-incumbent",
        turn_number=1,
        subject="arc 6893 mid-flight commission",
        body="TYPE: DIRECTIVE\ncontract: implement\n## Scope\nx\n",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    assert q.claim_next().job_id == incumbent.job_id

    captured: list[dict] = []

    async def _capture_hop(job, *, queue, incumbent=None):
        captured.append(
            {
                "hop_job_id": job.job_id,
                "incumbent_job_id": incumbent.job_id if incumbent else None,
                "incumbent_subject": incumbent.subject if incumbent else None,
            }
        )
        return {"ok": True, "reason": "continuity_hop_cdp_commissioned", "execution_id": "e1"}

    monkeypatch.setattr(cadence_mod, "run_continuity_hop_concurrent", _capture_hop)
    monkeypatch.setattr(cadence_mod, "capacity_blocks_hop", lambda **_: (False, None))
    monkeypatch.setattr(cadence_mod, "mark_hop_fired", lambda *a, **k: None)
    monkeypatch.setattr(
        cadence_mod,
        "assess_standing_handoff",
        lambda tid: StandingHandoffFreshness("current", f"cortex://x/{tid}.md", None, 1.0),
    )

    decision = HopDecision(
        thread_id="T-cadence-incumbent",
        action="fire",
        reason="age_exceeded",
        age_s=2000.0,
        threshold_s=1500.0,
        signal="watch_seated_at",
    )
    outcome = await fire_hop_for_decision(
        decision,
        queue=q,
        row={"from_agent": "web-anthropic", "registration_id": "reg-1"},
    )

    assert outcome["ok"] is True
    assert captured, "cadence must invoke continuity hop"
    assert captured[0]["incumbent_job_id"] == incumbent.job_id
    assert captured[0]["incumbent_subject"] == "arc 6893 mid-flight commission"
    # Hop ≠ cancel: claimed incumbent remains claimed and not superseded.
    assert q.get(incumbent.job_id).status == "claimed"
    assert not q.is_superseded(incumbent.job_id)


@pytest.mark.asyncio
async def test_cadence_hop_residual_re_issue_subject_names_incumbent(monkeypatch):
    """AC3: re_issue_subject carries incumbent.subject; hop does not cancel."""
    from services.git_integration_worker.cursor_auto import continuity_hop as hop_mod
    from services.git_integration_worker.cursor_auto import hop_cadence as cadence_mod
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    incumbent = q.enqueue(
        thread_id="T-cadence-residual",
        turn_number=2,
        subject="preserve me — do not supersede",
        body="TYPE: DIRECTIVE\ncontract: implement\n## Scope\ny\n",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    assert q.claim_next().job_id == incumbent.job_id

    residuals: list[dict] = []

    async def _capture_residual(job, *, client, incumbent, dispatch_id):
        payload = (
            {
                "type": "CONTINUITY_HARVEST_RESIDUAL",
                "incumbent_job_id": None,
                "incumbent_subject": None,
                "re_issue_subject": None,
                "note": "empty",
            }
            if incumbent is None
            else {
                "type": "CONTINUITY_HARVEST_RESIDUAL",
                "incumbent_job_id": incumbent.job_id,
                "incumbent_subject": incumbent.subject,
                "re_issue_subject": incumbent.subject,
                "note": "preserved",
            }
        )
        residuals.append(payload)
        return {"ok": True, "payload": payload}

    async def _fake_commission(j, *, model, purpose):
        return {"ok": True, "execution_id": "exec-cadence-1"}

    async def _fake_terminal(j, **kwargs):
        return {"ok": True, "payload": kwargs.get("payload") or {}}

    monkeypatch.setattr(hop_mod, "post_harvest_residual", _capture_residual)
    monkeypatch.setattr(hop_mod, "commission_cdp_escalation", _fake_commission)
    monkeypatch.setattr(hop_mod, "post_terminal_status", _fake_terminal)
    monkeypatch.setattr(hop_mod, "live_run_for_thread", lambda _t: None)
    monkeypatch.setattr(cadence_mod, "capacity_blocks_hop", lambda **_: (False, None))
    monkeypatch.setattr(cadence_mod, "mark_hop_fired", lambda *a, **k: None)
    monkeypatch.setattr(
        cadence_mod,
        "assess_standing_handoff",
        lambda tid: StandingHandoffFreshness("current", f"cortex://x/{tid}.md", None, 1.0),
    )

    decision = HopDecision(
        thread_id="T-cadence-residual",
        action="fire",
        reason="age_exceeded",
        age_s=2000.0,
        threshold_s=1500.0,
        signal="watch_seated_at",
    )
    outcome = await fire_hop_for_decision(
        decision,
        queue=q,
        row={"from_agent": "web-anthropic"},
    )

    assert outcome["ok"] is True
    assert residuals
    assert residuals[0]["re_issue_subject"] == "preserve me — do not supersede"
    assert residuals[0]["incumbent_job_id"] == incumbent.job_id
    assert q.get(incumbent.job_id).status == "claimed"
    assert not q.is_superseded(incumbent.job_id)


@pytest.mark.asyncio
async def test_post_harvest_residual_re_issue_subject_when_incumbent_present():
    """Unit: continuity_hop residual field emit with a real incumbent."""
    from services.git_integration_worker.cursor_auto import continuity_hop as hop_mod
    from services.git_integration_worker.cursor_auto import queue as queue_mod

    q = queue_mod.reset_queue_for_tests(durable=False)
    hop = q.enqueue(
        thread_id="T-residual-unit",
        turn_number=1,
        subject="hop job",
        body="TYPE: CONTINUITY_HANDOFF\n",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="cdp/opus-5",
        desired_effort="high",
        contract="light-bounded",
        continuity_hop=True,
        continuity_matched_token="cadence:auto",
    )
    incumbent = q.enqueue(
        thread_id="T-residual-unit",
        turn_number=0,
        subject="real in-flight work",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor-auto",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    replies: list[dict] = []

    async def _reply(**kwargs):
        replies.append(kwargs)
        return MagicMock(status_code=200)

    client = MagicMock()
    client.reply = _reply
    result = await hop_mod.post_harvest_residual(
        hop,
        client=client,
        incumbent=incumbent,
        dispatch_id="auto-dispatch-xyz",
    )
    assert result["ok"] is True
    body = json.loads(replies[0]["body"])
    assert body["re_issue_subject"] == "real in-flight work"
    assert body["incumbent_job_id"] == incumbent.job_id
    assert body["incumbent_dispatch_id"] == "auto-dispatch-xyz"
