"""Arc 6655 — disposition path-gate (outcome token only under M1)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from services.git_integration_worker.cursor_auto.disposition_outcome import (
    m1_cdp_commission,
    m1_nested_relay,
    outcome_disposition_for_stamp,
)
from services.git_integration_worker.cursor_auto.handler_terminal import terminal_in_seat
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.work_journal import append_journal_entry
from services.git_integration_worker.cursor_auto.wire_map import resolve_contract_disposition


def test_outcome_token_requires_m1() -> None:
    assert (
        outcome_disposition_for_stamp("dispatched-and-relayed", m1_satisfied=True)
        == "dispatched-and-relayed"
    )
    assert (
        outcome_disposition_for_stamp("dispatched-and-relayed", m1_satisfied=False)
        is None
    )


def test_policy_labels_pass_without_m1() -> None:
    for hint in ("answered", "conferred", "executed", "propagated", "declined"):
        assert outcome_disposition_for_stamp(hint, m1_satisfied=False) == hint


def test_m1_helpers() -> None:
    assert m1_nested_relay(dispatch_id="auto-x", relay_ok=True) is True
    assert m1_nested_relay(dispatch_id="auto-x", relay_ok=False) is False
    assert m1_nested_relay(dispatch_id=None, relay_ok=True) is False
    assert m1_cdp_commission(execution_id="exec-1") is True
    assert m1_cdp_commission(execution_id=None) is False
    assert m1_cdp_commission(execution_id="") is False


def test_seed_hint_unchanged_but_in_seat_omits_outcome(tmp_path: Path) -> None:
    """D5 / a:28470 — seed hints outcome but in-seat must omit reader disposition."""
    assert (
        resolve_contract_disposition("seed")["disposition_hint"]
        == "dispatched-and-relayed"
    )
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    job = AutoJob(
        job_id="j-seed",
        thread_id="t-seed",
        turn_number=1,
        subject="seed",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="default",
        contract="seed",
    )
    result = asyncio.run(
        terminal_in_seat(
            job,
            client=client,
            queue=MagicMock(),
            model={"requested": "auto", "resolved_model_id": "cursor/grok-4.5"},
            effort={"requested": "low", "resolved_effort": "low"},
            contract_info={
                "contract": "seed",
                "disposition_hint": "dispatched-and-relayed",
            },
            gate_plan={"action": "in_seat"},
            answer_body="seed note",
        )
    )
    assert "disposition" not in result
    payload = json.loads(client.reply.await_args.kwargs["body"])
    assert "disposition" not in payload
    assert payload["disposition_hint"] == "dispatched-and-relayed"


def test_journal_omits_disposition_key_when_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.work_journal.journal_path",
        lambda **_: tmp_path / "work-journal.jsonl",
    )
    assert append_journal_entry(
        thread_id="t1",
        dispatch_id=None,
        contract="seed",
        terminal_status="status:done",
        disposition=None,
    )
    row = json.loads((tmp_path / "work-journal.jsonl").read_text().strip())
    assert "disposition" not in row
