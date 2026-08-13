"""L2 inheritance-loop proof — orientation reaches the hop successor (7119)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from services.git_integration_worker.cursor_auto.hop_orientation import (
    build_hop_orientation,
    format_resolved_envelope,
    prepend_orientation,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

_EFFORT = {"requested": "xhigh", "resolved_effort": "high", "wire_effort": "high"}


def _job(body: str = "TYPE: CONTINUITY_HANDOFF\ncarry the arc\n") -> AutoJob:
    return AutoJob(
        job_id="j-hop-orient",
        thread_id="7119",
        turn_number=9,
        subject="hop",
        body=body,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="xhigh",
        contract="light-bounded",
        continuity_hop=True,
    )


def test_resolved_envelope_names_what_the_successor_actually_runs_as() -> None:
    line = format_resolved_envelope(model="cdp/opus-5", effort=_EFFORT)
    assert "model=cdp/opus-5" in line
    assert "requested_effort=xhigh" in line
    assert "resolved_effort=high" in line


def test_prepend_orientation_puts_block_ahead_of_directive() -> None:
    merged = prepend_orientation("TYPE: CONTINUITY_HANDOFF\n", "ORIENTATION")
    assert merged.startswith("ORIENTATION")
    assert merged.index("ORIENTATION") < merged.index("TYPE: CONTINUITY_HANDOFF")


def test_prepend_orientation_is_identity_without_a_block() -> None:
    assert prepend_orientation("body", None) == "body"


@pytest.mark.asyncio
async def test_build_hop_orientation_composes_card_and_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns: list[dict[str, Any]] = [
        {
            "id": 3052,
            "turn_number": 12,
            "from": "cursor-auto",
            "subject": "status:admitted — hop",
            "body": "field_parity: status=ok scope=envelope",
            "created_at": "2026-08-13T02:00:00Z",
        }
    ]
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.hop_orientation.fetch_thread_turns",
        AsyncMock(return_value=turns),
    )
    result = await build_hop_orientation(_job(), model="cdp/opus-5", effort=_EFFORT)
    assert result["generated"] is True
    block = result["block"]
    assert "resolved_envelope: model=cdp/opus-5" in block
    assert "GENERATED ARRIVAL CARD (L2)" in block
    assert "handoff_prompt generated_at=" in block
    # The predecessor's admit turn is what the successor otherwise never reads.
    assert result["inheritance_loop_closed"] is True


@pytest.mark.asyncio
async def test_hop_still_hops_when_orientation_generation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.hop_orientation.fetch_thread_turns",
        AsyncMock(side_effect=RuntimeError("bus down")),
    )
    result = await build_hop_orientation(_job(), model="cdp/opus-5", effort=_EFFORT)
    assert result["generated"] is False
    assert result["error"] == "bus down"
    # Degraded, not empty: the successor still learns what it is running as.
    assert "resolved_envelope: model=cdp/opus-5" in result["block"]


@pytest.mark.asyncio
async def test_commission_receives_orientation_prefixed_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.cursor_auto import continuity_hop as hop_mod

    captured: dict[str, Any] = {}

    async def _fake_commission(job, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return {"ok": True, "execution_id": "exec-1"}

    monkeypatch.setattr(hop_mod, "post_harvest_residual", AsyncMock(return_value={}))
    monkeypatch.setattr(hop_mod, "_post_hop_admit_report", AsyncMock(return_value=None))
    monkeypatch.setattr(hop_mod, "live_run_for_thread", lambda _t: None)
    monkeypatch.setattr(hop_mod, "commission_cdp_escalation", _fake_commission)
    monkeypatch.setattr(
        hop_mod,
        "build_hop_orientation",
        AsyncMock(
            return_value={
                "generated": True,
                "block": "ORIENTATION BLOCK",
                "inheritance_loop_closed": True,
            }
        ),
    )
    monkeypatch.setattr(hop_mod, "emit_cdp_effort_bind", lambda **_: None)
    monkeypatch.setattr(
        hop_mod, "post_terminal_status", AsyncMock(return_value={"ok": True})
    )

    await hop_mod.complete_continuity_hop(_job(), queue=AsyncMock(), client=AsyncMock())

    prompt = captured["prompt_override"]
    assert prompt.startswith("ORIENTATION BLOCK")
    assert "TYPE: CONTINUITY_HANDOFF" in prompt
