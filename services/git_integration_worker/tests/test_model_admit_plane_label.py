"""Arc 6655 Unit A — admit-plane labeling for envelope model: / admit body."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from services.git_integration_worker.cursor_auto.closeout_replay import _extract_model
from services.git_integration_worker.cursor_auto.nested_sdk import (
    post_operator_closeout,
    post_operator_confer,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob


def _job(*, contract: str = "implement") -> AutoJob:
    return AutoJob(
        job_id="j-model-a",
        thread_id="6655",
        turn_number=3,
        subject="DIRECTIVE",
        body="TYPE: DIRECTIVE",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="default",
        contract=contract,
    )


def test_closeout_envelope_marks_model_plane_admit_resolved() -> None:
    bus = MagicMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body="ok"))
    closeout_body = (
        "TYPE: CLOSEOUT\nstatus: complete\ncheckpoint: nothing_authored\n\n"
        "| Field | Value |\n|---|---|\n"
        "| status_claim | complete |\n"
        "| ac_verdict | PASS |\n"
    )
    asyncio.run(
        post_operator_closeout(
            _job(),
            status="complete",
            dispatch_id="auto-model-a",
            model_id="cursor/composer-2.5",
            sdk_body=None,
            closeout_body=closeout_body,
            closeout_source="section2_sidecar",
            bus=bus,
        )
    )
    sent = bus.reply.await_args.kwargs["body"]
    assert "model: cursor/composer-2.5" in sent
    assert "model_plane: admit-resolved" in sent
    assert _extract_model(sent) == "cursor/composer-2.5"


def test_confer_envelope_marks_model_plane_admit_resolved() -> None:
    bus = MagicMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body="ok"))
    asyncio.run(
        post_operator_confer(
            _job(contract="confer"),
            dispatch_id="auto-confer-a",
            model_id="cursor/grok-4.5",
            status="complete",
            closeout_body="confer body",
            bus=bus,
        )
    )
    sent = bus.reply.await_args.kwargs["body"]
    assert "model: cursor/grok-4.5" in sent
    assert "model_plane: admit-resolved" in sent
