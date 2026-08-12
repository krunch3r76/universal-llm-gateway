"""Arc 6655 — additive status-register labels at post_terminal_status."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from services.git_integration_worker.cursor_auto.handler_terminal import (
    post_terminal_status,
    terminal_in_seat,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_auto.status_token_register import (
    DISPOSITION_HINT_STATUS_OF,
    DISPOSITION_STATUS_OF,
    ENVELOPE_STATUS_STATUS_OF,
    TERMINAL_STATUS_STATUS_OF,
    disposition_hint_label_verdict,
    disposition_hint_presence,
    disposition_outcome_label_verdict,
    disposition_outcome_presence,
    prose_closeout_register_header_lines,
    stamp_meta_terminal_status_status_of,
)


def _job(*, contract: str = "implement") -> AutoJob:
    return AutoJob(
        job_id="j-status-reg",
        thread_id="6655",
        turn_number=1,
        subject="status register stamp",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="low",
        contract=contract,
    )


def test_disposition_hint_state_verdicts_in_code() -> None:
    """AC4 — every disposition_hint state has an explicit in-code verdict."""
    assert disposition_hint_presence(None) == "absent"
    assert disposition_hint_presence("") == "absent"
    assert disposition_hint_presence("answered") == "present_known"
    assert disposition_hint_presence("dispatched-and-relayed") == "present_known"
    assert disposition_hint_presence("novel-token") == "present_out_of_set"
    assert disposition_hint_label_verdict(None) == "no_label_needed"
    assert disposition_hint_label_verdict("answered") == "label"
    assert disposition_hint_label_verdict("novel-token") == "label"


def test_disposition_outcome_state_verdicts_in_code() -> None:
    """AC4 — every disposition outcome state has an explicit in-code verdict."""
    assert disposition_outcome_presence(None) == "absent"
    assert disposition_outcome_presence("declined") == "present_known"
    assert disposition_outcome_presence("novel-outcome") == "present_out_of_set"
    assert disposition_outcome_label_verdict(None) == "no_label_needed"
    assert disposition_outcome_label_verdict("declined") == "label"
    assert disposition_outcome_label_verdict("novel-outcome") == "label"


def test_post_terminal_status_stamps_disposition_status_of() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    asyncio.run(
        post_terminal_status(
            _job(),
            client=client,
            queue=MagicMock(),
            summary="blocked",
            disposition="blocked",
            contract="implement",
            terminal_status="status:blocked",
            payload={"summary": "blocked", "reason": "empty_directive_scope"},
            failed=True,
        )
    )
    body = json.loads(client.reply.await_args.kwargs["body"])
    assert body["disposition"] == "blocked"
    assert body["disposition_status_of"] == DISPOSITION_STATUS_OF
    assert body["terminal_status_status_of"] == TERMINAL_STATUS_STATUS_OF


def test_post_terminal_status_stamps_terminal_status_status_of_all_modes() -> None:
    """Rank-1b: every terminal_status mode gets the same job-terminalization register."""
    modes = (
        "status:done",
        "status:failed",
        "status:blocked",
        "status:needs-attended",
        "status:superseded",
    )
    for terminal_status in modes:
        client = AsyncMock()
        client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
        asyncio.run(
            post_terminal_status(
                _job(),
                client=client,
                queue=MagicMock(),
                summary=f"mode {terminal_status}",
                disposition=None,
                contract="implement",
                terminal_status=terminal_status,
                payload={"summary": f"mode {terminal_status}"},
                failed=terminal_status != "status:done",
            )
        )
        body = json.loads(client.reply.await_args.kwargs["body"])
        assert body["terminal_status_status_of"] == TERMINAL_STATUS_STATUS_OF
        subject = client.reply.await_args.kwargs["subject"]
        assert subject.startswith(f"{terminal_status} —")


def test_post_terminal_status_subject_unchanged_for_wait_parsers() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    asyncio.run(
        post_terminal_status(
            _job(),
            client=client,
            queue=MagicMock(),
            summary="done",
            disposition="complete",
            contract="implement",
            terminal_status="status:done",
            payload={"summary": "done", "disposition": "complete"},
        )
    )
    assert client.reply.await_args.kwargs["subject"].startswith("status:done —")


def test_post_terminal_status_omits_register_when_disposition_none() -> None:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    asyncio.run(
        post_terminal_status(
            _job(contract="seed"),
            client=client,
            queue=MagicMock(),
            summary="seed in seat",
            disposition=None,
            contract="seed",
            payload={
                "summary": "seed in seat",
                "disposition_hint": "dispatched-and-relayed",
            },
        )
    )
    body = json.loads(client.reply.await_args.kwargs["body"])
    assert "disposition" not in body
    assert "disposition_status_of" not in body
    assert body["disposition_hint"] == "dispatched-and-relayed"
    assert body["disposition_hint_status_of"] == DISPOSITION_HINT_STATUS_OF
    assert body["terminal_status_status_of"] == TERMINAL_STATUS_STATUS_OF


def test_terminal_in_seat_answer_declined_stamps_planned_and_observed_registers() -> None:
    """Specimen class: hint answered (planned) vs disposition declined (observed)."""
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    asyncio.run(
        terminal_in_seat(
            _job(contract="answer"),
            client=client,
            queue=MagicMock(),
            model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
            effort={"requested": "low", "resolved_effort": "low"},
            contract_info={"contract": "answer", "disposition_hint": "answered"},
            gate_plan={"action": "in_seat"},
            answer_body=None,
        )
    )
    body = json.loads(client.reply.await_args.kwargs["body"])
    assert body["disposition_hint"] == "answered"
    assert body["disposition_hint_status_of"] == DISPOSITION_HINT_STATUS_OF
    assert body["disposition"] == "declined"
    assert body["disposition_status_of"] == DISPOSITION_STATUS_OF
    assert body["terminal_status_status_of"] == TERMINAL_STATUS_STATUS_OF
    assert body["disposition_hint"] != body["disposition"]


def test_prose_closeout_register_header_lines_are_additive_and_noncolliding() -> None:
    """Prose labels are header lines; names must not startswith('status:')."""
    lines = prose_closeout_register_header_lines()
    assert lines == [
        f"envelope_status_status_of: {ENVELOPE_STATUS_STATUS_OF}",
        f"terminal_status_status_of: {TERMINAL_STATUS_STATUS_OF}",
    ]
    for line in lines:
        assert not line.lower().startswith("status:")


def test_stamp_meta_terminal_status_status_of_preserves_value() -> None:
    stamped = stamp_meta_terminal_status_status_of({"terminal_status": "completed"})
    assert stamped["terminal_status"] == "completed"
    assert stamped["terminal_status_status_of"] == TERMINAL_STATUS_STATUS_OF
    assert stamp_meta_terminal_status_status_of({"gate_plan": {}}) == {"gate_plan": {}}


def test_post_operator_closeout_stamps_prose_register_headers() -> None:
    """Happy-path implement prose CLOSEOUT carries register lines + meta label."""
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        post_operator_closeout,
    )

    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body="ok"))
    closeout_body = (
        "TYPE: CLOSEOUT\nstatus: complete\ncheckpoint: nothing_authored\n\n"
        "| Field | Value |\n|---|---|\n"
        "| status_claim | complete |\n"
    )
    asyncio.run(
        post_operator_closeout(
            _job(contract="implement"),
            status="complete",
            dispatch_id="auto-prose-reg",
            model_id="cursor/composer-2.5",
            sdk_body=None,
            closeout_body=closeout_body,
            closeout_source="section2_sidecar",
            extra={"terminal_status": "completed"},
            bus=client,
        )
    )
    sent = client.reply.await_args.kwargs["body"]
    subject = client.reply.await_args.kwargs["subject"]
    assert subject.startswith("status:done —")
    assert "status: complete" in sent
    assert "composed_commission: n/a — not-in-closure" in sent
    assert f"envelope_status_status_of: {ENVELOPE_STATUS_STATUS_OF}" in sent
    assert f"terminal_status_status_of: {TERMINAL_STATUS_STATUS_OF}" in sent
    status_lines = [
        line.strip() for line in sent.splitlines() if line.lower().startswith("status:")
    ]
    assert status_lines == ["status: complete"]
    meta_line = next(line for line in sent.splitlines() if line.startswith("meta: "))
    meta = json.loads(meta_line[len("meta: ") :])
    assert meta["terminal_status"] == "completed"
    assert meta["terminal_status_status_of"] == TERMINAL_STATUS_STATUS_OF


def test_post_operator_closeout_composed_commission_orthogonal_to_status() -> None:
    """``status: complete`` may co-exist with ``composed_commission: failed``."""
    from unittest.mock import patch

    from services.git_integration_worker.cursor_auto.composed_commission import (
        COMPOSED_COMMISSION_FAILED,
    )
    from services.git_integration_worker.cursor_auto.nested_sdk import (
        post_operator_closeout,
    )

    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body="ok"))
    closeout_body = (
        "TYPE: CLOSEOUT\nstatus: complete\ncheckpoint: nothing_authored\n\n"
        "| Field | Value |\n|---|---|\n"
        "| status_claim | complete |\n"
    )
    ledger = MagicMock()
    ledger.list_nested_children.return_value = ["child-failed"]
    ledger.dispatch_status_by_id.return_value = {
        "dispatch_id": "child-failed",
        "status": "failed",
    }
    with patch(
        "services.git_integration_worker.cursor_auto.nested_sdk.CursorDispatchLedger"
    ) as ledger_cls:
        ledger_cls.instance.return_value = ledger
        asyncio.run(
            post_operator_closeout(
                _job(contract="implement"),
                status="complete",
                dispatch_id="auto-compose-fail",
                model_id="cursor/composer-2.5",
                sdk_body=None,
                closeout_body=closeout_body,
                closeout_source="section2_sidecar",
                extra={"nest_under": "parent-p", "terminal_status": "completed"},
                bus=client,
            )
        )
    sent = client.reply.await_args.kwargs["body"]
    header_lines = sent.split("\n\n", 1)[0].splitlines()
    status_idx = next(i for i, line in enumerate(header_lines) if line == "status: complete")
    composed_idx = next(
        i for i, line in enumerate(header_lines) if line.startswith("composed_commission:")
    )
    assert composed_idx > status_idx
    assert f"composed_commission: {COMPOSED_COMMISSION_FAILED}" in sent
    assert "status: complete" in sent
